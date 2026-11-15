use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;

pub const DEFAULT_DEVICE_ID_PATH: &str = "/data/device-id";

/// Complete device and build identity report (AC1, AC2, AC3, P0-F2.3).
/// Printed as JSON on stdout for remote deployment tooling and diagnostics.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DeviceIdentity {
    pub image_version: String,
    pub version: String,
    pub source_commit: String,
    pub commit: String,
    pub build_id: String,
    pub running_slot: String,
    pub slot: String,
    pub board_model: String,
    pub hardware_compatibility: String,
    pub device_id: String,
}

/// Version-specific identity subset (AC1).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct VersionInfo {
    pub image_version: String,
    pub source_commit: String,
    pub build_id: String,
    pub running_slot: String,
}

/// Hardware identity subset (AC2).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HardwareIdentity {
    pub board_model: String,
    pub hardware_compatibility: String,
}

/// Release file metadata parsed from /etc/os-release or /etc/nextoomio-release.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReleaseInfo {
    pub image_version: String,
    pub source_commit: String,
    pub build_id: String,
    pub hardware_compatibility: String,
    pub board_model: Option<String>,
}

/// Parses key-value pairs from /etc/os-release or /etc/nextoomio-release.
pub fn parse_release_file(content: &str) -> ReleaseInfo {
    let mut map = HashMap::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            let key = k.trim();
            let mut val = v.trim();
            if ((val.starts_with('"') && val.ends_with('"'))
                || (val.starts_with('\'') && val.ends_with('\'')))
                && val.len() >= 2
            {
                val = &val[1..val.len() - 1];
            }
            map.insert(key.to_string(), val.to_string());
        }
    }

    let image_version = map
        .get("NEXTOOMIO_IMAGE_VERSION")
        .or_else(|| map.get("IMAGE_VERSION"))
        .or_else(|| map.get("VERSION_ID"))
        .or_else(|| map.get("VERSION"))
        .cloned()
        .unwrap_or_else(|| "0.0.1-dev".to_string());

    let source_commit = map
        .get("NEXTOOMIO_SOURCE_COMMIT")
        .or_else(|| map.get("SOURCE_COMMIT"))
        .or_else(|| map.get("BUILD_COMMIT"))
        .or_else(|| map.get("GIT_COMMIT"))
        .cloned()
        .unwrap_or_else(|| "unknown".to_string());

    let build_id = map
        .get("NEXTOOMIO_BUILD_ID")
        .or_else(|| map.get("BUILD_ID"))
        .cloned()
        .unwrap_or_else(|| "dev".to_string());

    let hardware_compatibility = map
        .get("NEXTOOMIO_HARDWARE_COMPAT")
        .or_else(|| map.get("HARDWARE_COMPATIBILITY"))
        .or_else(|| map.get("HARDWARE_COMPAT"))
        .cloned()
        .unwrap_or_else(|| "raspberrypi4-64".to_string());

    let board_model = map
        .get("NEXTOOMIO_BOARD_MODEL")
        .or_else(|| map.get("BOARD_MODEL"))
        .cloned();

    ReleaseInfo {
        image_version,
        source_commit,
        build_id,
        hardware_compatibility,
        board_model,
    }
}

/// Loads release information from standard system locations or test overrides.
pub fn load_release_info() -> ReleaseInfo {
    if let Ok(path) = std::env::var("NEXTOOMIO_RELEASE_FILE") {
        if let Ok(content) = fs::read_to_string(&path) {
            return parse_release_file(&content);
        }
    }

    for path in &[
        "/etc/nextoomio-release",
        "/etc/os-release",
        "/usr/lib/os-release",
    ] {
        if let Ok(content) = fs::read_to_string(path) {
            return parse_release_file(&content);
        }
    }

    ReleaseInfo {
        image_version: "0.0.1-dev".to_string(),
        source_commit: "unknown".to_string(),
        build_id: "dev".to_string(),
        hardware_compatibility: "raspberrypi4-64".to_string(),
        board_model: None,
    }
}

/// Detects the running system slot ("a" or "b") based on kernel cmdline, mounts, or configuration.
pub fn detect_running_slot() -> String {
    // 1. Explicit environment override (tests and diagnostics)
    if let Ok(slot) = std::env::var("NEXTOOMIO_SLOT") {
        let s = slot.trim().to_lowercase();
        if s == "a" || s == "b" {
            return s;
        }
    }

    // 2. /etc/nextoomio-slot file
    if let Ok(content) = fs::read_to_string("/etc/nextoomio-slot") {
        let s = content.trim().to_lowercase();
        if s == "a" || s == "b" {
            return s;
        }
    }

    // 3. /proc/cmdline inspection
    if let Ok(cmdline) = fs::read_to_string("/proc/cmdline") {
        if let Some(slot) = parse_slot_from_cmdline(&cmdline) {
            return slot;
        }
    }

    // 4. /proc/mounts inspection
    if let Ok(mounts) = fs::read_to_string("/proc/mounts") {
        if let Some(slot) = parse_slot_from_mounts(&mounts) {
            return slot;
        }
    }

    // Default fallback: slot A (initial baseline image)
    "a".to_string()
}

/// Parses slot designation from kernel command line string (D-004).
pub fn parse_slot_from_cmdline(cmdline: &str) -> Option<String> {
    // Partition 5 (PARTUUID=...-05) is slot A (root_a)
    if cmdline.contains("4e58544d-05")
        || cmdline.contains("mmcblk0p5")
        || cmdline.contains("root_a")
        || cmdline.contains("slot=a")
    {
        return Some("a".to_string());
    }
    // Partition 6 (PARTUUID=...-06) is slot B (root_b)
    if cmdline.contains("4e58544d-06")
        || cmdline.contains("mmcblk0p6")
        || cmdline.contains("root_b")
        || cmdline.contains("slot=b")
    {
        return Some("b".to_string());
    }
    None
}

/// Parses slot designation from mounts table for root filesystem mount.
pub fn parse_slot_from_mounts(mounts: &str) -> Option<String> {
    for line in mounts.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 2 && parts[1] == "/" {
            let dev = parts[0];
            if dev.ends_with("p5") || dev.contains("root_a") {
                return Some("a".to_string());
            }
            if dev.ends_with("p6") || dev.contains("root_b") {
                return Some("b".to_string());
            }
        }
    }
    None
}

/// Detects board model from device tree, release override, or reference fallback.
pub fn detect_board_model(release_override: Option<&str>) -> String {
    if let Ok(model) = std::env::var("NEXTOOMIO_BOARD_MODEL") {
        let m = model.trim();
        if !m.is_empty() {
            return m.to_string();
        }
    }

    if let Some(m) = release_override {
        let trimmed = m.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }

    for path in &[
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    ] {
        if let Ok(bytes) = fs::read(path) {
            let s = String::from_utf8_lossy(&bytes);
            let cleaned = s.trim_matches('\0').trim();
            if !cleaned.is_empty() {
                return cleaned.to_string();
            }
        }
    }

    "Raspberry Pi 4 Model B".to_string()
}

/// Generates a RFC 4122 compliant UUIDv4 string.
pub fn generate_random_uuid_v4() -> String {
    let mut bytes = [0u8; 16];
    if let Ok(mut f) = fs::File::open("/dev/urandom") {
        let _ = f.read_exact(&mut bytes);
    } else {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let p = &bytes as *const _ as usize as u128;
        let mixed = now ^ p.rotate_left(32);
        bytes.copy_from_slice(&mixed.to_le_bytes());
    }

    // Set UUIDv4 version (0100) and variant (10)
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3],
        bytes[4], bytes[5],
        bytes[6], bytes[7],
        bytes[8], bytes[9],
        bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15],
    )
}

/// Retrieves the existing device ID from the data partition or generates and persists a new one (AC3).
pub fn get_or_create_device_id() -> String {
    if let Ok(id) = std::env::var("NEXTOOMIO_DEVICE_ID") {
        let id_clean = id.trim();
        if !id_clean.is_empty() {
            return id_clean.to_string();
        }
    }

    let path_str = std::env::var("NEXTOOMIO_DEVICE_ID_PATH")
        .unwrap_or_else(|_| DEFAULT_DEVICE_ID_PATH.to_string());
    let path = Path::new(&path_str);

    if let Ok(content) = fs::read_to_string(path) {
        let id = content.trim();
        if !id.is_empty() {
            return id.to_string();
        }
    }

    let new_id = generate_random_uuid_v4();

    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut f) = fs::File::create(path) {
        use std::io::Write;
        let _ = writeln!(f, "{new_id}");
        let _ = f.sync_all();
    }

    new_id
}

/// Constructs complete device identity report.
pub fn query_device_identity() -> DeviceIdentity {
    let release = load_release_info();
    let slot = detect_running_slot();
    let board = detect_board_model(release.board_model.as_deref());
    let dev_id = get_or_create_device_id();

    DeviceIdentity {
        image_version: release.image_version.clone(),
        version: release.image_version,
        source_commit: release.source_commit.clone(),
        commit: release.source_commit,
        build_id: release.build_id,
        running_slot: slot.clone(),
        slot,
        board_model: board,
        hardware_compatibility: release.hardware_compatibility,
        device_id: dev_id,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_release_file_standard() {
        let content = r#"
NAME="nextoomio Audio OS"
VERSION="0.0.1-dev"
ID="nextoomio"
VERSION_ID="0.0.1-dev"
BUILD_ID="20261116001200"
IMAGE_VERSION="0.0.1-dev"
NEXTOOMIO_BUILD_ID="20261116001200"
NEXTOOMIO_SOURCE_COMMIT="80903cb41d775175a82c3470a402ef9a4b2de01f"
NEXTOOMIO_IMAGE_VERSION="0.0.1-dev"
NEXTOOMIO_HARDWARE_COMPAT="raspberrypi4-64"
"#;
        let release = parse_release_file(content);
        assert_eq!(release.image_version, "0.0.1-dev");
        assert_eq!(
            release.source_commit,
            "80903cb41d775175a82c3470a402ef9a4b2de01f"
        );
        assert_eq!(release.build_id, "20261116001200");
        assert_eq!(release.hardware_compatibility, "raspberrypi4-64");
    }

    #[test]
    fn test_parse_release_file_nextoomio_release() {
        let content = r#"
IMAGE_VERSION="0.0.1-dev"
BUILD_ID="20261116120000"
SOURCE_COMMIT="cb616e6b8f4ff55142bda9be103942613611e00f"
HARDWARE_COMPATIBILITY="raspberrypi4-64"
"#;
        let release = parse_release_file(content);
        assert_eq!(release.image_version, "0.0.1-dev");
        assert_eq!(
            release.source_commit,
            "cb616e6b8f4ff55142bda9be103942613611e00f"
        );
        assert_eq!(release.build_id, "20261116120000");
        assert_eq!(release.hardware_compatibility, "raspberrypi4-64");
    }

    #[test]
    fn test_parse_slot_from_cmdline() {
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=PARTUUID=4e58544d-05 rootwait ro"
            ),
            Some("a".to_string())
        );
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=/dev/mmcblk0p5 rootwait ro"
            ),
            Some("a".to_string())
        );
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=PARTUUID=4e58544d-06 rootwait ro"
            ),
            Some("b".to_string())
        );
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=/dev/mmcblk0p6 rootwait ro"
            ),
            Some("b".to_string())
        );
        assert_eq!(parse_slot_from_cmdline("console=serial0,115200"), None);
    }

    #[test]
    fn test_parse_slot_from_mounts() {
        let mounts = r#"
/dev/root / ext4 ro,relatime 0 0
devtmpfs /dev devtmpfs rw,relatime 0 0
"#;
        assert_eq!(parse_slot_from_mounts(mounts), None);

        let mounts_a = r#"
/dev/mmcblk0p5 / ext4 ro,relatime 0 0
/dev/mmcblk0p7 /data ext4 rw,noatime 0 0
"#;
        assert_eq!(parse_slot_from_mounts(mounts_a), Some("a".to_string()));

        let mounts_b = r#"
/dev/mmcblk0p6 / ext4 ro,relatime 0 0
/dev/mmcblk0p7 /data ext4 rw,noatime 0 0
"#;
        assert_eq!(parse_slot_from_mounts(mounts_b), Some("b".to_string()));
    }

    #[test]
    fn test_device_id_format_and_persistence() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);
        let id_file = temp_dir.join("device-id");

        // Set env to point to temp file
        std::env::set_var("NEXTOOMIO_DEVICE_ID_PATH", id_file.to_str().unwrap());

        // First call: generates and persists
        let id1 = get_or_create_device_id();
        assert_eq!(id1.len(), 36);
        assert_eq!(id1.matches('-').count(), 4);
        assert_eq!(&id1[14..15], "4"); // UUID version 4

        // Second call: reads persisted file, must be identical
        let id2 = get_or_create_device_id();
        assert_eq!(id1, id2);

        // Verify content on disk
        let file_content = fs::read_to_string(&id_file).unwrap();
        assert_eq!(file_content.trim(), id1);

        // Cleanup
        let _ = fs::remove_file(id_file);
        let _ = fs::remove_dir(temp_dir);
        std::env::remove_var("NEXTOOMIO_DEVICE_ID_PATH");
    }

    #[test]
    fn test_differing_images_ac4() {
        let release1 = parse_release_file(
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"build-100\"\nSOURCE_COMMIT=\"commit-aaa\"\n",
        );
        let release2 = parse_release_file(
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"build-200\"\nSOURCE_COMMIT=\"commit-bbb\"\n",
        );

        assert_ne!(release1.source_commit, release2.source_commit);
        assert_ne!(release1.build_id, release2.build_id);
    }

    #[test]
    fn test_full_identity_json_ac1_ac2_ac3() {
        let identity = DeviceIdentity {
            image_version: "0.0.1-dev".to_string(),
            version: "0.0.1-dev".to_string(),
            source_commit: "80903cb41d775175a82c3470a402ef9a4b2de01f".to_string(),
            commit: "80903cb41d775175a82c3470a402ef9a4b2de01f".to_string(),
            build_id: "20261116001200".to_string(),
            running_slot: "a".to_string(),
            slot: "a".to_string(),
            board_model: "Raspberry Pi 4 Model B Rev 1.4".to_string(),
            hardware_compatibility: "raspberrypi4-64".to_string(),
            device_id: "11112222-3333-4444-5555-666677778888".to_string(),
        };

        let json_str = serde_json::to_string(&identity).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();

        assert_eq!(parsed["image_version"], "0.0.1-dev");
        assert_eq!(
            parsed["source_commit"],
            "80903cb41d775175a82c3470a402ef9a4b2de01f"
        );
        assert_eq!(parsed["build_id"], "20261116001200");
        assert_eq!(parsed["running_slot"], "a");
        assert_eq!(parsed["board_model"], "Raspberry Pi 4 Model B Rev 1.4");
        assert_eq!(parsed["hardware_compatibility"], "raspberrypi4-64");
        assert_eq!(parsed["device_id"], "11112222-3333-4444-5555-666677778888");
    }
}
