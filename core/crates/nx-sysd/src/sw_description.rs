//! Parser for SWUpdate `sw-description` metadata files.
//!
//! Supports both standard libconfig syntax (used by OE/Yocto swupdate class)
//! and JSON syntax.

/// Image artifact declared in sw-description.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SwImageArtifact {
    pub filename: String,
    pub device: String,
    pub image_type: String,
    pub compressed: Option<String>,
    pub sha256: String,
    pub installed_directly: bool,
    pub copy: Option<String>,
}

/// Parsed metadata from `sw-description`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SwDescription {
    pub version: String,
    pub description: String,
    pub hardware_compatibility: Vec<String>,
    pub config_schema_min: u32,
    pub config_schema_max: u32,
    pub images: Vec<SwImageArtifact>,
}

impl SwDescription {
    /// Checks whether the update declares compatibility with the given hardware model.
    pub fn is_hardware_compatible(&self, hw_model: &str) -> bool {
        self.hardware_compatibility.iter().any(|h| h == hw_model)
    }

    /// Checks whether the update supports the device's configuration schema version.
    pub fn is_schema_compatible(&self, schema_version: u32) -> bool {
        schema_version >= self.config_schema_min && schema_version <= self.config_schema_max
    }
}

/// Parses sw-description content.
pub fn parse_sw_description(content: &str) -> Result<SwDescription, String> {
    // Strip comments first
    let stripped = strip_comments(content);

    // Try parsing as JSON first if it starts with {
    let trimmed = stripped.trim();
    if trimmed.starts_with('{') {
        if let Ok(desc) = parse_json_sw_description(trimmed) {
            return Ok(desc);
        }
    }

    // Parse libconfig format
    parse_libconfig_sw_description(&stripped)
}

fn strip_comments(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();
    let mut in_string = false;
    let mut escape = false;

    while let Some(c) = chars.next() {
        if in_string {
            out.push(c);
            if escape {
                escape = false;
            } else if c == '\\' {
                escape = true;
            } else if c == '"' {
                in_string = false;
            }
            continue;
        }

        if c == '"' {
            in_string = true;
            out.push(c);
            continue;
        }

        if c == '#' {
            // line comment
            for next_c in chars.by_ref() {
                if next_c == '\n' {
                    out.push('\n');
                    break;
                }
            }
            continue;
        }

        if c == '/' {
            if let Some(&'/') = chars.peek() {
                let _ = chars.next();
                for next_c in chars.by_ref() {
                    if next_c == '\n' {
                        out.push('\n');
                        break;
                    }
                }
                continue;
            } else if let Some(&'*') = chars.peek() {
                let _ = chars.next();
                let mut prev = ' ';
                for next_c in chars.by_ref() {
                    if prev == '*' && next_c == '/' {
                        break;
                    }
                    prev = next_c;
                }
                continue;
            }
        }

        out.push(c);
    }

    out
}

fn extract_string_literal(input: &str) -> Option<String> {
    let start = input.find('"')?;
    let rem = &input[start + 1..];
    let end = rem.find('"')?;
    Some(rem[..end].to_string())
}

fn extract_string_list(input: &str) -> Vec<String> {
    let mut list = Vec::new();
    let mut in_str = false;
    let mut current = String::new();
    let mut escape = false;

    for c in input.chars() {
        if in_str {
            if escape {
                current.push(c);
                escape = false;
            } else if c == '\\' {
                escape = true;
            } else if c == '"' {
                in_str = false;
                list.push(current.clone());
                current.clear();
            } else {
                current.push(c);
            }
        } else if c == '"' {
            in_str = true;
        }
    }

    list
}

fn parse_libconfig_sw_description(input: &str) -> Result<SwDescription, String> {
    let mut version = String::new();
    let mut description = String::new();
    let mut hardware_compatibility = Vec::new();
    let mut config_schema_min = 1u32;
    let mut config_schema_max = 1u32;
    let mut images = Vec::new();

    // Scan for hardware-compatibility
    if let Some(pos) = input.find("hardware-compatibility") {
        let after = &input[pos + "hardware-compatibility".len()..];
        if let Some(bracket_start) = after.find('[') {
            if let Some(bracket_end) = after[bracket_start..].find(']') {
                let inside = &after[bracket_start..bracket_start + bracket_end];
                hardware_compatibility = extract_string_list(inside);
            }
        }
    }

    // Scan for version
    for line in input.lines() {
        let line = line.trim();
        if (line.starts_with("version")
            || line.starts_with("version =")
            || line.starts_with("version:"))
            && version.is_empty()
        {
            if let Some(v) = extract_string_literal(line) {
                version = v;
            }
        } else if (line.starts_with("description")
            || line.starts_with("description =")
            || line.starts_with("description:"))
            && description.is_empty()
        {
            if let Some(d) = extract_string_literal(line) {
                description = d;
            }
        } else if line.starts_with("config-schema-min") {
            if let Some(s) = extract_string_literal(line) {
                if let Ok(val) = s.parse::<u32>() {
                    config_schema_min = val;
                }
            } else if let Some(eq) = line.find('=') {
                let val_str = line[eq + 1..].trim_matches(|c: char| !c.is_ascii_digit());
                if let Ok(val) = val_str.parse::<u32>() {
                    config_schema_min = val;
                }
            }
        } else if line.starts_with("config-schema-max") {
            if let Some(s) = extract_string_literal(line) {
                if let Ok(val) = s.parse::<u32>() {
                    config_schema_max = val;
                }
            } else if let Some(eq) = line.find('=') {
                let val_str = line[eq + 1..].trim_matches(|c: char| !c.is_ascii_digit());
                if let Ok(val) = val_str.parse::<u32>() {
                    config_schema_max = val;
                }
            }
        } else if line.starts_with("config-schema-range") {
            if let Some(range_str) = extract_string_literal(line) {
                if let Some((min_s, max_s)) = range_str.split_once("..") {
                    if let Ok(min_v) = min_s.trim().parse::<u32>() {
                        config_schema_min = min_v;
                    }
                    if let Ok(max_v) = max_s.trim().parse::<u32>() {
                        config_schema_max = max_v;
                    }
                }
            }
        }
    }

    // Extract image blocks
    // Each image entry is enclosed in { ... } inside images: ( ... )
    let mut cursor = input;
    while let Some(img_pos) = cursor.find("images") {
        let after_images = &cursor[img_pos + "images".len()..];
        // Find closing parenthesis of images: ( ... )
        if let Some(open_paren) = after_images.find('(') {
            let paren_content = &after_images[open_paren + 1..];
            // Match braces inside
            let mut block_cursor = paren_content;
            while let Some(brace_start) = block_cursor.find('{') {
                let after_brace = &block_cursor[brace_start + 1..];
                if let Some(brace_end) = after_brace.find('}') {
                    let block = &after_brace[..brace_end];
                    if let Some(img) = parse_image_block(block) {
                        images.push(img);
                    }
                    block_cursor = &after_brace[brace_end + 1..];
                } else {
                    break;
                }
            }
            if let Some(close_paren) = after_images.find(')') {
                cursor = &after_images[close_paren + 1..];
                continue;
            }
        }
        cursor = after_images;
    }

    if hardware_compatibility.is_empty() {
        return Err("missing hardware-compatibility in sw-description".to_string());
    }

    Ok(SwDescription {
        version,
        description,
        hardware_compatibility,
        config_schema_min,
        config_schema_max,
        images,
    })
}

fn parse_image_block(block: &str) -> Option<SwImageArtifact> {
    let mut filename = String::new();
    let mut device = String::new();
    let mut image_type = String::new();
    let mut compressed = None;
    let mut sha256 = String::new();
    let mut installed_directly = false;

    for line in block.lines() {
        let line = line.trim();
        if line.starts_with("filename") {
            if let Some(val) = extract_string_literal(line) {
                filename = val;
            }
        } else if line.starts_with("device") {
            if let Some(val) = extract_string_literal(line) {
                device = val;
            }
        } else if line.starts_with("type") {
            if let Some(val) = extract_string_literal(line) {
                image_type = val;
            }
        } else if line.starts_with("compressed") {
            if let Some(val) = extract_string_literal(line) {
                compressed = Some(val);
            }
        } else if line.starts_with("sha256") {
            if let Some(val) = extract_string_literal(line) {
                sha256 = val.to_lowercase();
            }
        } else if line.starts_with("installed-directly") {
            installed_directly = line.contains("true");
        }
    }

    if filename.is_empty() || sha256.is_empty() {
        return None;
    }

    Some(SwImageArtifact {
        filename,
        device,
        image_type,
        compressed,
        sha256,
        installed_directly,
        copy: None,
    })
}

fn parse_json_sw_description(json_str: &str) -> Result<SwDescription, String> {
    #[derive(serde::Deserialize)]
    struct JsonArtifact {
        filename: String,
        device: Option<String>,
        #[serde(rename = "type")]
        image_type: Option<String>,
        compressed: Option<String>,
        sha256: String,
        #[serde(default)]
        installed_directly: bool,
    }

    #[derive(serde::Deserialize)]
    struct JsonRoot {
        version: Option<String>,
        description: Option<String>,
        #[serde(default)]
        hardware_compatibility: Vec<String>,
        config_schema_min: Option<u32>,
        config_schema_max: Option<u32>,
        config_schema_range: Option<String>,
        #[serde(default)]
        images: Vec<JsonArtifact>,
    }

    let parsed: JsonRoot =
        serde_json::from_str(json_str).map_err(|e| format!("JSON parse error: {e}"))?;

    let mut min_schema = parsed.config_schema_min.unwrap_or(1);
    let mut max_schema = parsed.config_schema_max.unwrap_or(1);

    if let Some(range) = parsed.config_schema_range {
        if let Some((min_s, max_s)) = range.split_once("..") {
            if let Ok(v) = min_s.trim().parse() {
                min_schema = v;
            }
            if let Ok(v) = max_s.trim().parse() {
                max_schema = v;
            }
        }
    }

    if parsed.hardware_compatibility.is_empty() {
        return Err("missing hardware_compatibility in JSON sw-description".to_string());
    }

    let artifacts = parsed
        .images
        .into_iter()
        .map(|img| SwImageArtifact {
            filename: img.filename,
            device: img.device.unwrap_or_default(),
            image_type: img.image_type.unwrap_or_else(|| "raw".to_string()),
            compressed: img.compressed,
            sha256: img.sha256.to_lowercase(),
            installed_directly: img.installed_directly,
            copy: None,
        })
        .collect();

    Ok(SwDescription {
        version: parsed.version.unwrap_or_default(),
        description: parsed.description.unwrap_or_default(),
        hardware_compatibility: parsed.hardware_compatibility,
        config_schema_min: min_schema,
        config_schema_max: max_schema,
        images: artifacts,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_swupdate_template() {
        let text = r#"
software =
{
    version = "0.0.1-dev";
    description = "nextoomio system update image";
    hardware-compatibility: [ "1.0", "rpi4" ];
    config-schema-range = "1..1";

    stable = {
        copy1 = {
            images: (
                {
                    filename = "nextoomio-boot.vfat.gz";
                    device = "/dev/mmcblk0p2";
                    type = "raw";
                    compressed = "zlib";
                    sha256 = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855";
                    installed-directly = true;
                },
                {
                    filename = "nextoomio-image.rootfs.ext4.gz";
                    device = "/dev/mmcblk0p5";
                    type = "raw";
                    compressed = "zlib";
                    sha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
                    installed-directly = true;
                }
            );
        };
    };
};
"#;

        let desc = parse_sw_description(text).expect("failed to parse sw-description");
        assert_eq!(desc.version, "0.0.1-dev");
        assert!(desc.is_hardware_compatible("1.0"));
        assert!(desc.is_hardware_compatible("rpi4"));
        assert!(!desc.is_hardware_compatible("rpi3"));
        assert!(desc.is_schema_compatible(1));
        assert!(!desc.is_schema_compatible(2));

        assert_eq!(desc.images.len(), 2);
        assert_eq!(desc.images[0].filename, "nextoomio-boot.vfat.gz");
        assert_eq!(
            desc.images[0].sha256,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(desc.images[1].filename, "nextoomio-image.rootfs.ext4.gz");
    }

    #[test]
    fn test_parse_json_sw_description() {
        let json_text = r#"
{
    "version": "0.0.1",
    "description": "test update",
    "hardware_compatibility": ["1.0"],
    "config_schema_range": "1..2",
    "images": [
        {
            "filename": "boot.img",
            "sha256": "1234abcd"
        }
    ]
}
"#;

        let desc = parse_sw_description(json_text).expect("parse failed");
        assert_eq!(desc.version, "0.0.1");
        assert!(desc.is_hardware_compatible("1.0"));
        assert!(desc.is_schema_compatible(2));
        assert_eq!(desc.images.len(), 1);
        assert_eq!(desc.images[0].sha256, "1234abcd");
    }
}
