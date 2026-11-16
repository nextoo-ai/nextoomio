use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;

pub const DEFAULT_DEVICE_ID_PATH: &str = "/data/device-id";

/// Explicit error type for device identity detection and persistence (CQ-R9, CQ-R2).
#[derive(Debug, PartialEq, Eq)]
pub enum IdentityError {
    ReleaseFileNotFound,
    ReleaseFieldMissing(&'static str),
    HardwareModelNotFound,
    HardwareCompatibilityNotFound,
    DeviceIdReadFailed(String),
    DeviceIdInvalid(String),
    EntropySourceFailed(String),
    AtomicWriteFailed(String),
}

impl std::fmt::Display for IdentityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ReleaseFileNotFound => write!(
                f,
                "release metadata file (/etc/nextoomio-release) not found"
            ),
            Self::ReleaseFieldMissing(field) => {
                write!(f, "release metadata file missing required field: {field}")
            }
            Self::HardwareModelNotFound => write!(
                f,
                "failed to read device tree hardware model from /proc/device-tree/model"
            ),
            Self::HardwareCompatibilityNotFound => write!(
                f,
                "hardware compatibility string missing from release metadata"
            ),
            Self::DeviceIdReadFailed(e) => write!(f, "failed to read device ID: {e}"),
            Self::DeviceIdInvalid(id) => {
                write!(f, "device ID content '{id}' is not a valid RFC 4122 UUIDv4")
            }
            Self::EntropySourceFailed(e) => {
                write!(
                    f,
                    "failed to read random entropy from OS (/dev/urandom): {e}"
                )
            }
            Self::AtomicWriteFailed(e) => write!(f, "failed to atomically persist device ID: {e}"),
        }
    }
}

impl std::error::Error for IdentityError {}

/// Complete device and build identity report (AC1, AC2, AC3, P0-F2.3).
/// Printed as JSON on stdout for remote deployment tooling and diagnostics.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DeviceIdentity {
    pub image_version: String,
    pub source_commit: String,
    pub build_id: String,
    pub running_slot: String,
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

/// Release file metadata parsed from /etc/nextoomio-release.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReleaseInfo {
    pub image_version: String,
    pub source_commit: String,
    pub build_id: String,
    pub hardware_compatibility: String,
}

/// Injected paths for identity detection, allowing hermetic unit testing without environment variables (CQ-R14).
#[derive(Debug, Clone)]
pub struct IdentityPaths<'a> {
    pub release_file: &'a Path,
    pub cmdline_file: &'a Path,
    pub dt_model_file: &'a Path,
    pub device_id_file: &'a Path,
}

impl<'a> Default for IdentityPaths<'a> {
    fn default() -> Self {
        Self {
            release_file: Path::new("/etc/nextoomio-release"),
            cmdline_file: Path::new("/proc/cmdline"),
            dt_model_file: Path::new("/proc/device-tree/model"),
            device_id_file: Path::new(DEFAULT_DEVICE_ID_PATH),
        }
    }
}

/// Parses key-value pairs from /etc/nextoomio-release (AC1, AC2, AC4).
/// Accepts only keys written by the image (IMAGE_VERSION, SOURCE_COMMIT, BUILD_ID, HARDWARE_COMPATIBILITY).
/// Fails explicitly if any required release field is missing or unknown (CQ-R2).
pub fn parse_release_file(content: &str) -> Result<ReleaseInfo, IdentityError> {
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
        .get("IMAGE_VERSION")
        .cloned()
        .ok_or(IdentityError::ReleaseFieldMissing("IMAGE_VERSION"))?;

    let source_commit = map
        .get("SOURCE_COMMIT")
        .cloned()
        .ok_or(IdentityError::ReleaseFieldMissing("SOURCE_COMMIT"))?;

    if source_commit.trim().is_empty() || source_commit == "unknown" {
        return Err(IdentityError::ReleaseFieldMissing("SOURCE_COMMIT"));
    }

    let build_id = map
        .get("BUILD_ID")
        .cloned()
        .ok_or(IdentityError::ReleaseFieldMissing("BUILD_ID"))?;

    let hardware_compatibility = map
        .get("HARDWARE_COMPATIBILITY")
        .cloned()
        .ok_or(IdentityError::HardwareCompatibilityNotFound)?;

    Ok(ReleaseInfo {
        image_version,
        source_commit,
        build_id,
        hardware_compatibility,
    })
}

/// Detects running slot from /proc/cmdline (D-004).
/// The running slot is derived from root=PARTUUID=4e58544d-05 (A) or -06 (B) only,
/// anything else is reported as unknown (CQ-R2, CQ-R14).
pub fn detect_running_slot_with_path(cmdline_path: &Path) -> String {
    if let Ok(cmdline) = fs::read_to_string(cmdline_path) {
        parse_slot_from_cmdline(&cmdline)
    } else {
        "unknown".to_string()
    }
}

/// Parses slot designation from kernel command line string (D-004).
pub fn parse_slot_from_cmdline(cmdline: &str) -> String {
    // Partition 5 (PARTUUID=4e58544d-05) is slot A (root_a)
    // Partition 6 (PARTUUID=4e58544d-06) is slot B (root_b)
    if cmdline.contains("root=PARTUUID=4e58544d-05") || cmdline.contains("PARTUUID=4e58544d-05") {
        "a".to_string()
    } else if cmdline.contains("root=PARTUUID=4e58544d-06")
        || cmdline.contains("PARTUUID=4e58544d-06")
    {
        "b".to_string()
    } else {
        "unknown".to_string()
    }
}

/// Detects board model from device tree (AC2).
/// Returns explicit error if model cannot be read rather than falling back to an unverified guess (CQ-R2).
pub fn detect_board_model_with_path(dt_path: &Path) -> Result<String, IdentityError> {
    if let Ok(bytes) = fs::read(dt_path) {
        let s = String::from_utf8_lossy(&bytes);
        let cleaned = s.trim_matches('\0').trim();
        if !cleaned.is_empty() {
            return Ok(cleaned.to_string());
        }
    }

    Err(IdentityError::HardwareModelNotFound)
}

/// Validates that a string strictly adheres to RFC 4122 UUIDv4 format.
pub fn validate_uuid_v4(s: &str) -> bool {
    let s = s.trim();
    if s.len() != 36 {
        return false;
    }
    let bytes = s.as_bytes();
    if bytes[8] != b'-' || bytes[13] != b'-' || bytes[18] != b'-' || bytes[23] != b'-' {
        return false;
    }
    // Version 4 check at index 14
    if bytes[14] != b'4' {
        return false;
    }
    // Variant check (8, 9, a, or b) at index 19
    match bytes[19] {
        b'8' | b'9' | b'a' | b'b' | b'A' | b'B' => {}
        _ => return false,
    }
    for (i, &b) in bytes.iter().enumerate() {
        if i == 8 || i == 13 || i == 18 || i == 23 {
            continue;
        }
        if !b.is_ascii_hexdigit() {
            return false;
        }
    }
    true
}

/// Generates a RFC 4122 compliant UUIDv4 string using randomness strictly from the OS (/dev/urandom).
/// Returns error if OS randomness is unavailable; never falls back to predictable timestamps (CQ-R9, CQ-R2).
pub fn generate_random_uuid_v4() -> Result<String, IdentityError> {
    let mut bytes = [0u8; 16];
    let mut f = fs::File::open("/dev/urandom").map_err(|e| {
        IdentityError::EntropySourceFailed(format!("failed to open /dev/urandom: {e}"))
    })?;
    f.read_exact(&mut bytes).map_err(|e| {
        IdentityError::EntropySourceFailed(format!("failed to read from /dev/urandom: {e}"))
    })?;

    // Set UUIDv4 version (0100) and variant (10)
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    Ok(format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3],
        bytes[4], bytes[5],
        bytes[6], bytes[7],
        bytes[8], bytes[9],
        bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15],
    ))
}

/// Retrieves existing device ID from target path or atomically generates and persists a new UUIDv4 (AC3).
/// Writes via temporary file, fsyncs data and directory, validates existing UUID format,
/// and propagates all filesystem errors with non-zero exit (CQ-R9, CQ-R2).
pub fn get_or_create_device_id_with_path(path: &Path) -> Result<String, IdentityError> {
    if path.exists() {
        let content = fs::read_to_string(path)
            .map_err(|e| IdentityError::DeviceIdReadFailed(format!("{}: {e}", path.display())))?;
        let id = content.trim();
        if validate_uuid_v4(id) {
            return Ok(id.to_string());
        } else {
            return Err(IdentityError::DeviceIdInvalid(id.to_string()));
        }
    }

    let new_id = generate_random_uuid_v4()?;

    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|e| {
        IdentityError::AtomicWriteFailed(format!(
            "failed to create directory {}: {e}",
            parent.display()
        ))
    })?;

    use std::io::Write;
    let pid = std::process::id();
    let temp_name = format!(".device-id.tmp.{pid}.{}", &new_id[..8]);
    let temp_path = parent.join(temp_name);

    let mut temp_file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp_path)
        .map_err(|e| {
            IdentityError::AtomicWriteFailed(format!(
                "failed to create temp file {}: {e}",
                temp_path.display()
            ))
        })?;

    writeln!(temp_file, "{new_id}").map_err(|e| {
        let _ = fs::remove_file(&temp_path);
        IdentityError::AtomicWriteFailed(format!("failed to write device ID: {e}"))
    })?;

    temp_file.sync_all().map_err(|e| {
        let _ = fs::remove_file(&temp_path);
        IdentityError::AtomicWriteFailed(format!("failed to fsync temp file: {e}"))
    })?;
    drop(temp_file);

    fs::rename(&temp_path, path).map_err(|e| {
        let _ = fs::remove_file(&temp_path);
        IdentityError::AtomicWriteFailed(format!(
            "failed to rename temp file to {}: {e}",
            path.display()
        ))
    })?;

    if let Ok(dir_file) = fs::File::open(parent) {
        let _ = dir_file.sync_all();
    }

    Ok(new_id)
}

/// Constructs complete device identity report using injected paths (AC1, AC2, AC3).
pub fn query_device_identity_with_paths(
    paths: &IdentityPaths,
) -> Result<DeviceIdentity, IdentityError> {
    let release_content =
        fs::read_to_string(paths.release_file).map_err(|_| IdentityError::ReleaseFileNotFound)?;

    let release = parse_release_file(&release_content)?;
    let slot = detect_running_slot_with_path(paths.cmdline_file);
    let board = detect_board_model_with_path(paths.dt_model_file)?;
    let dev_id = get_or_create_device_id_with_path(paths.device_id_file)?;

    Ok(DeviceIdentity {
        image_version: release.image_version,
        source_commit: release.source_commit,
        build_id: release.build_id,
        running_slot: slot,
        board_model: board,
        hardware_compatibility: release.hardware_compatibility,
        device_id: dev_id,
    })
}

/// Constructs complete device identity report using standard production paths.
pub fn query_device_identity() -> Result<DeviceIdentity, IdentityError> {
    query_device_identity_with_paths(&IdentityPaths::default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_release_file_nextoomio_release() {
        let content = r#"
IMAGE_VERSION="0.0.1-dev"
BUILD_ID="20261116120000"
SOURCE_COMMIT="cb616e6b8f4ff55142bda9be103942613611e00f"
HARDWARE_COMPATIBILITY="raspberrypi4-64"
"#;
        let release = parse_release_file(content).expect("nextoomio-release format must parse");
        assert_eq!(release.image_version, "0.0.1-dev");
        assert_eq!(
            release.source_commit,
            "cb616e6b8f4ff55142bda9be103942613611e00f"
        );
        assert_eq!(release.build_id, "20261116120000");
        assert_eq!(release.hardware_compatibility, "raspberrypi4-64");
    }

    #[test]
    fn test_parse_release_file_missing_commit_fails_explicitly() {
        let content = r#"
IMAGE_VERSION="0.0.1-dev"
BUILD_ID="20261116120000"
SOURCE_COMMIT="unknown"
HARDWARE_COMPATIBILITY="raspberrypi4-64"
"#;
        let err = parse_release_file(content).unwrap_err();
        assert_eq!(err, IdentityError::ReleaseFieldMissing("SOURCE_COMMIT"));
    }

    #[test]
    fn test_parse_release_file_missing_hardware_compatibility_fails() {
        let content = r#"
IMAGE_VERSION="0.0.1-dev"
BUILD_ID="20261116120000"
SOURCE_COMMIT="80903cb41d775175a82c3470a402ef9a4b2de01f"
"#;
        let err = parse_release_file(content).unwrap_err();
        assert_eq!(err, IdentityError::HardwareCompatibilityNotFound);
    }

    #[test]
    fn test_parse_slot_from_cmdline() {
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=PARTUUID=4e58544d-05 rootwait ro"
            ),
            "a"
        );
        assert_eq!(
            parse_slot_from_cmdline(
                "coherent_pool=1M 8250.nr_uarts=1 root=PARTUUID=4e58544d-06 rootwait ro"
            ),
            "b"
        );
        assert_eq!(parse_slot_from_cmdline("console=serial0,115200"), "unknown");
        assert_eq!(parse_slot_from_cmdline("root=/dev/mmcblk0p5 ro"), "unknown");
    }

    #[test]
    fn test_detect_board_model_success_and_failure() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_bm_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);
        let dt_model = temp_dir.join("model");

        fs::write(&dt_model, "Raspberry Pi 4 Model B Rev 1.4\0").unwrap();
        let model = detect_board_model_with_path(&dt_model).expect("must read valid model");
        assert_eq!(model, "Raspberry Pi 4 Model B Rev 1.4");

        let missing_dt = temp_dir.join("nonexistent");
        let err = detect_board_model_with_path(&missing_dt).unwrap_err();
        assert_eq!(err, IdentityError::HardwareModelNotFound);

        let _ = fs::remove_file(dt_model);
        let _ = fs::remove_dir(temp_dir);
    }

    #[test]
    fn test_validate_uuid_v4() {
        assert!(validate_uuid_v4("80903cb4-1d77-4175-a82c-3470a402ef9a"));
        assert!(validate_uuid_v4("00000000-0000-4000-8000-000000000000"));
        assert!(validate_uuid_v4("ffffffff-ffff-4fff-bfff-ffffffffffff"));

        // Wrong length
        assert!(!validate_uuid_v4("80903cb4-1d77-4175-a82c-3470a402ef9"));
        assert!(!validate_uuid_v4("80903cb4-1d77-4175-a82c-3470a402ef9aa"));

        // Wrong version (not 4)
        assert!(!validate_uuid_v4("80903cb4-1d77-1175-a82c-3470a402ef9a"));
        assert!(!validate_uuid_v4("80903cb4-1d77-5175-a82c-3470a402ef9a"));

        // Wrong variant (not 8, 9, a, or b)
        assert!(!validate_uuid_v4("80903cb4-1d77-4175-082c-3470a402ef9a"));
        assert!(!validate_uuid_v4("80903cb4-1d77-4175-c82c-3470a402ef9a"));

        // Non-hex characters
        assert!(!validate_uuid_v4("80903cb4-1d77-4175-g82c-3470a402ef9a"));
    }

    #[test]
    fn test_device_id_atomic_persistence_and_second_process_ac3() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_id_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);
        let id_file = temp_dir.join("device-id");

        // First call: generates and atomically persists valid UUIDv4
        let id1 = get_or_create_device_id_with_path(&id_file).expect("device ID must be created");
        assert!(validate_uuid_v4(&id1));
        assert!(id_file.is_file());

        let file_content = fs::read_to_string(&id_file).expect("file must be readable");
        assert_eq!(file_content.trim(), id1);
        assert!(file_content.ends_with('\n'), "file must end with newline");

        // Second call simulating separate process invocation: must return exact same persisted ID
        let id2 = get_or_create_device_id_with_path(&id_file).expect("device ID must be read");
        assert_eq!(id1, id2);

        // Cleanup
        let _ = fs::remove_file(id_file);
        let _ = fs::remove_dir(temp_dir);
    }

    #[test]
    fn test_device_id_corrupted_file_fails_explicitly() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_corrupt_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);
        let id_file = temp_dir.join("device-id");

        fs::write(&id_file, "corrupted-torn-write\n").unwrap();

        let res = get_or_create_device_id_with_path(&id_file);
        assert_eq!(
            res,
            Err(IdentityError::DeviceIdInvalid(
                "corrupted-torn-write".to_string()
            ))
        );

        let _ = fs::remove_file(id_file);
        let _ = fs::remove_dir(temp_dir);
    }

    #[test]
    fn test_query_device_identity_with_paths_ac1_ac2_ac3() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_ident_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);

        let release_file = temp_dir.join("nextoomio-release");
        let cmdline_file = temp_dir.join("cmdline");
        let dt_model_file = temp_dir.join("model");
        let device_id_file = temp_dir.join("device-id");

        fs::write(
            &release_file,
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"20261116001200\"\nSOURCE_COMMIT=\"80903cb41d775175a82c3470a402ef9a4b2de01f\"\nHARDWARE_COMPATIBILITY=\"raspberrypi4-64\"\n",
        )
        .unwrap();
        fs::write(&cmdline_file, "root=PARTUUID=4e58544d-05 ro\n").unwrap();
        fs::write(&dt_model_file, "Raspberry Pi 4 Model B Rev 1.4\0").unwrap();

        let paths = IdentityPaths {
            release_file: &release_file,
            cmdline_file: &cmdline_file,
            dt_model_file: &dt_model_file,
            device_id_file: &device_id_file,
        };

        let identity = query_device_identity_with_paths(&paths).expect("identity query must pass");
        assert_eq!(identity.image_version, "0.0.1-dev");
        assert_eq!(
            identity.source_commit,
            "80903cb41d775175a82c3470a402ef9a4b2de01f"
        );
        assert_eq!(identity.build_id, "20261116001200");
        assert_eq!(identity.running_slot, "a");
        assert_eq!(identity.board_model, "Raspberry Pi 4 Model B Rev 1.4");
        assert_eq!(identity.hardware_compatibility, "raspberrypi4-64");
        assert!(validate_uuid_v4(&identity.device_id));

        let _ = fs::remove_file(release_file);
        let _ = fs::remove_file(cmdline_file);
        let _ = fs::remove_file(dt_model_file);
        let _ = fs::remove_file(device_id_file);
        let _ = fs::remove_dir(temp_dir);
    }

    #[test]
    fn test_query_device_identity_differing_images_ac4() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_diff_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);

        let release_a = temp_dir.join("release_a");
        let release_b = temp_dir.join("release_b");
        let cmdline = temp_dir.join("cmdline");
        let dt_model = temp_dir.join("model");
        let device_id = temp_dir.join("device-id");

        fs::write(
            &release_a,
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"build-100\"\nSOURCE_COMMIT=\"commit-aaa\"\nHARDWARE_COMPATIBILITY=\"raspberrypi4-64\"\n",
        )
        .unwrap();
        fs::write(
            &release_b,
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"build-200\"\nSOURCE_COMMIT=\"commit-bbb\"\nHARDWARE_COMPATIBILITY=\"raspberrypi4-64\"\n",
        )
        .unwrap();
        fs::write(&cmdline, "root=PARTUUID=4e58544d-05 ro\n").unwrap();
        fs::write(&dt_model, "Raspberry Pi 4 Model B\0").unwrap();

        let paths_a = IdentityPaths {
            release_file: &release_a,
            cmdline_file: &cmdline,
            dt_model_file: &dt_model,
            device_id_file: &device_id,
        };
        let paths_b = IdentityPaths {
            release_file: &release_b,
            cmdline_file: &cmdline,
            dt_model_file: &dt_model,
            device_id_file: &device_id,
        };

        let ident_a = query_device_identity_with_paths(&paths_a).unwrap();
        let ident_b = query_device_identity_with_paths(&paths_b).unwrap();

        assert_ne!(ident_a.source_commit, ident_b.source_commit);
        assert_ne!(ident_a.build_id, ident_b.build_id);
        // Persistent device ID must remain identical across differing images (AC3)
        assert_eq!(ident_a.device_id, ident_b.device_id);

        let _ = fs::remove_file(release_a);
        let _ = fs::remove_file(release_b);
        let _ = fs::remove_file(cmdline);
        let _ = fs::remove_file(dt_model);
        let _ = fs::remove_file(device_id);
        let _ = fs::remove_dir(temp_dir);
    }

    #[test]
    fn test_hardware_identity_matches_update_validation() {
        let temp_dir = std::env::temp_dir().join(format!("nx_test_hw_val_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);

        let release_file = temp_dir.join("nextoomio-release");
        let cmdline_file = temp_dir.join("cmdline");
        let dt_model_file = temp_dir.join("model");
        let device_id_file = temp_dir.join("device-id");

        fs::write(
            &release_file,
            "IMAGE_VERSION=\"0.0.1-dev\"\nBUILD_ID=\"20261116001200\"\nSOURCE_COMMIT=\"80903cb41d775175a82c3470a402ef9a4b2de01f\"\nHARDWARE_COMPATIBILITY=\"raspberrypi4-64\"\n",
        )
        .unwrap();
        fs::write(&cmdline_file, "root=PARTUUID=4e58544d-05 ro\n").unwrap();
        fs::write(&dt_model_file, "Raspberry Pi 4 Model B Rev 1.4\0").unwrap();

        let paths = IdentityPaths {
            release_file: &release_file,
            cmdline_file: &cmdline_file,
            dt_model_file: &dt_model_file,
            device_id_file: &device_id_file,
        };

        let identity = query_device_identity_with_paths(&paths).expect("identity query must pass");
        let hardware_report = HardwareIdentity {
            board_model: identity.board_model.clone(),
            hardware_compatibility: identity.hardware_compatibility.clone(),
        };

        // Value reported by `nx-sysd hardware` (hardware_compatibility)
        assert_eq!(hardware_report.hardware_compatibility, "raspberrypi4-64");

        // Verify that update engine validation uses this exact reported hardware compatibility string
        use crate::sw_description::SwDescription;
        let compatible_sw = SwDescription {
            version: "0.0.2".to_string(),
            description: "Next update".to_string(),
            hardware_compatibility: vec![hardware_report.hardware_compatibility.clone()],
            config_schema_min: 1,
            config_schema_max: 1,
            images: vec![],
        };
        assert!(
            compatible_sw.is_hardware_compatible(&hardware_report.hardware_compatibility),
            "sw-description with reported hardware compatibility must pass update validation"
        );

        let incompatible_sw = SwDescription {
            version: "0.0.2".to_string(),
            description: "Next update".to_string(),
            hardware_compatibility: vec!["incompatible-board".to_string()],
            config_schema_min: 1,
            config_schema_max: 1,
            images: vec![],
        };
        assert!(
            !incompatible_sw.is_hardware_compatible(&hardware_report.hardware_compatibility),
            "sw-description with differing hardware compatibility must fail update validation"
        );

        // Verify engine initialized with this hardware compatibility string matches
        let backend =
            crate::system::MockSlotBackend::new(crate::system::Slot::A, crate::system::Slot::A);
        let storage = crate::system::MockStagingStorage::new(10 * 1024 * 1024);
        let journal = crate::journal::MemoryJournalStorage::default();
        let engine = crate::engine::UpdateEngine::new(
            backend,
            storage,
            journal,
            Some(hardware_report.hardware_compatibility.clone()),
            Some(1),
            Some(10 * 1024 * 1024),
        )
        .expect("engine initialization must succeed");

        assert_eq!(
            engine.hardware_model,
            hardware_report.hardware_compatibility
        );
        assert!(compatible_sw.is_hardware_compatible(&engine.hardware_model));
        assert!(!incompatible_sw.is_hardware_compatible(&engine.hardware_model));

        let _ = fs::remove_file(release_file);
        let _ = fs::remove_file(cmdline_file);
        let _ = fs::remove_file(dt_model_file);
        let _ = fs::remove_file(device_id_file);
        let _ = fs::remove_dir(temp_dir);
    }
}
