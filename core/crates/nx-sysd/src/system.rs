//! Hardware, storage, and slot abstractions for update staging and installation.
//!
//! Exposes traits so that update staging and validation can run without physical
//! block devices on developer workstations (AD-20, AC1..AC6).

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// System boot slot identity (Slot A or Slot B) per ARCHITECTURE.md section 3.6.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Slot {
    A,
    B,
}

/// Partition type within a system slot (boot or root) per ARCHITECTURE.md section 3.6.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PartitionType {
    Boot,
    Root,
}

impl PartitionType {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Boot => "boot",
            Self::Root => "root",
        }
    }
}

impl Slot {
    pub const fn name(self) -> &'static str {
        match self {
            Self::A => "root_a",
            Self::B => "root_b",
        }
    }

    pub const fn boot_device(self) -> &'static str {
        match self {
            Self::A => "/dev/mmcblk0p2",
            Self::B => "/dev/mmcblk0p3",
        }
    }

    pub const fn root_device(self) -> &'static str {
        match self {
            Self::A => "/dev/mmcblk0p5",
            Self::B => "/dev/mmcblk0p6",
        }
    }

    pub const fn partition_device(self, part: PartitionType) -> &'static str {
        match (self, part) {
            (Self::A, PartitionType::Boot) => "/dev/mmcblk0p2",
            (Self::A, PartitionType::Root) => "/dev/mmcblk0p5",
            (Self::B, PartitionType::Boot) => "/dev/mmcblk0p3",
            (Self::B, PartitionType::Root) => "/dev/mmcblk0p6",
        }
    }

    pub const fn inactive(self) -> Self {
        match self {
            Self::A => Self::B,
            Self::B => Self::A,
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        let trimmed = s.trim().to_lowercase();
        if trimmed == "a" || trimmed == "root_a" || trimmed == "slot_a" || trimmed.ends_with("p5") {
            Some(Self::A)
        } else if trimmed == "b"
            || trimmed == "root_b"
            || trimmed == "slot_b"
            || trimmed.ends_with("p6")
        {
            Some(Self::B)
        } else {
            None
        }
    }
}

impl fmt::Display for Slot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// Local health check evaluation report per ARCHITECTURE.md section 3.6 step 5, UPD-06, and AC2.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct HealthReport {
    pub healthy: bool,
    pub system_target_reached: bool,
    pub data_mounted: bool,
    pub sound_card_openable: bool,
    pub ssh_listening: bool,
    pub ssh_key_file_present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<String>,
}

/// Abstract slot interface for inspecting slots, writing partitions, and hardware includes (AC1..AC4).
pub trait SlotBackend: Send + Sync {
    /// Returns the currently running system slot.
    fn get_active_slot(&self) -> Result<Slot, String>;

    /// Returns the currently configured default boot slot.
    fn get_default_slot(&self) -> Result<Slot, String>;

    /// Computes the SHA-256 checksum of an entire partition's current contents (AC1).
    fn checksum_partition(&self, slot: Slot, partition: PartitionType) -> Result<String, String>;

    /// Streams artifact bytes into the target partition (AC1, SYS-05, UPD-05).
    fn write_partition<R: Read>(
        &mut self,
        slot: Slot,
        partition: PartitionType,
        reader: R,
        size: u64,
    ) -> Result<(), String>;

    /// Reads back data up to `length` bytes from the slot partition and computes SHA-256 (AC2).
    fn read_partition_checksum(
        &self,
        slot: Slot,
        partition: PartitionType,
        length: u64,
    ) -> Result<String, String>;

    /// Retrieves the sound-card selection (nextoomio-hardware.txt) from the running boot slot (AC4).
    fn get_active_sound_card_selection(&self) -> Result<Option<String>, String>;

    /// Retrieves the sound-card selection (nextoomio-hardware.txt) from the specified slot (AC4).
    fn get_sound_card_selection(&self, slot: Slot) -> Result<Option<String>, String>;

    /// Writes the sound-card selection (nextoomio-hardware.txt) to the inactive boot slot (AC4).
    fn set_sound_card_selection(&mut self, slot: Slot, content: &str) -> Result<(), String>;

    /// Flushes and syncs all partition writes to persistent storage (ARCHITECTURE.md section 3.6).
    fn sync_partitions(&mut self) -> Result<(), String>;

    /// Arms one-shot trial boot for the specified slot (AC1, AD-14).
    fn arm_trial_boot(&mut self, target_slot: Slot) -> Result<(), String>;

    /// Clears the one-shot tryboot selector after trial finalization or failure (AC4, AC6).
    fn clear_trial_boot(&mut self) -> Result<(), String>;

    /// Returns the currently armed trial slot, if any.
    #[cfg(test)]
    fn is_trial_armed(&self) -> Result<Option<Slot>, String>;

    /// Triggers tryboot-flagged reboot to boot the tryboot selector (AC1, AC4).
    ///
    /// For testing, `PhysicalSlotBackend` honours `NEXTOOMIO_NO_REBOOT` to skip actual reboot
    /// execution. Note: this disables the safety reboot for test fixtures.
    fn trigger_tryboot(&mut self) -> Result<(), String>;

    /// Triggers plain system reboot to return to the default slot on timeout/failure (AC3).
    ///
    /// Used by the trial-watchdog path. Does NOT honour `NEXTOOMIO_NO_REBOOT`.
    fn trigger_reboot(&mut self) -> Result<(), String>;

    /// Arms the hardware watchdog to enforce independent trial deadline (AC3, AR-01).
    fn arm_watchdog(&mut self, timeout_secs: u64) -> Result<(), String>;

    /// Keep an armed watchdog alive until the trial deadline.
    fn feed_watchdog(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Disarms the hardware watchdog when trial transaction commits successfully (AC3, AR-01).
    fn disarm_watchdog(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Retrieves kernel/system uptime in seconds for trial deadline accounting (AC3).
    fn get_uptime_secs(&self) -> Result<f64, String>;

    /// Evaluates the local trial boot health check conditions (AC2, AC7).
    fn check_health(&self) -> Result<HealthReport, String>;
}

/// Mock slot backend for unit tests.
#[derive(Debug, Clone)]
pub struct MockSlotBackend {
    pub active_slot: Slot,
    pub default_slot: Slot,
    pub partitions: std::collections::HashMap<(Slot, PartitionType), Vec<u8>>,
    pub sound_card_selections: std::collections::HashMap<Slot, Option<String>>,
    pub written_partitions: Vec<(Slot, PartitionType)>,
    pub corrupt_readback: bool,
    pub write_active_slot_glitch: bool,
    pub abort_during_write: bool,
    pub trial_armed: Option<Slot>,
    pub tryboot_triggered: bool,
    pub reboot_triggered: bool,
    pub watchdog_armed: Option<u64>,
    pub watchdog_disarmed: bool,
    pub uptime_secs: f64,
    pub uptime_step: std::sync::Arc<std::sync::atomic::AtomicU64>,
    pub uptime_reads: std::sync::Arc<std::sync::atomic::AtomicU64>,
    pub health_report: Option<HealthReport>,
}

impl MockSlotBackend {
    pub fn new(active: Slot, default: Slot) -> Self {
        let mut partitions = std::collections::HashMap::new();
        partitions.insert((Slot::A, PartitionType::Boot), vec![0x11; 1024]);
        partitions.insert((Slot::A, PartitionType::Root), vec![0x22; 4096]);
        partitions.insert((Slot::B, PartitionType::Boot), vec![0x33; 1024]);
        partitions.insert((Slot::B, PartitionType::Root), vec![0x44; 4096]);

        let mut sound_card_selections = std::collections::HashMap::new();
        sound_card_selections.insert(Slot::A, Some("dtoverlay=hifiberry-amp\n".to_string()));
        sound_card_selections.insert(Slot::B, None);

        Self {
            active_slot: active,
            default_slot: default,
            partitions,
            sound_card_selections,
            written_partitions: Vec::new(),
            corrupt_readback: false,
            write_active_slot_glitch: false,
            abort_during_write: false,
            trial_armed: None,
            tryboot_triggered: false,
            reboot_triggered: false,
            watchdog_armed: None,
            watchdog_disarmed: false,
            uptime_secs: 0.0,
            uptime_step: std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0)),
            uptime_reads: std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0)),
            health_report: None,
        }
    }
}

impl SlotBackend for MockSlotBackend {
    fn get_active_slot(&self) -> Result<Slot, String> {
        Ok(self.active_slot)
    }

    fn get_default_slot(&self) -> Result<Slot, String> {
        Ok(self.default_slot)
    }

    fn checksum_partition(&self, slot: Slot, partition: PartitionType) -> Result<String, String> {
        let data = self
            .partitions
            .get(&(slot, partition))
            .map(|v| v.as_slice())
            .unwrap_or(&[]);
        Ok(crate::sha256::Sha256::hex(data))
    }

    fn write_partition<R: Read>(
        &mut self,
        slot: Slot,
        partition: PartitionType,
        mut reader: R,
        size: u64,
    ) -> Result<(), String> {
        if self.abort_during_write {
            panic!("simulated power cut or reboot during write_partition");
        }
        self.written_partitions.push((slot, partition));
        let mut buf = vec![0u8; size as usize];
        reader.read_exact(&mut buf).map_err(|e| {
            format!("cannot read payload for partition {slot:?}/{partition:?}: {e}")
        })?;
        self.partitions.insert((slot, partition), buf);

        if self.write_active_slot_glitch && slot == self.active_slot.inactive() {
            // Simulate a forbidden write leak into the active slot
            if let Some(active_root) = self
                .partitions
                .get_mut(&(self.active_slot, PartitionType::Root))
            {
                active_root.push(0xFF);
            }
        }
        Ok(())
    }

    fn read_partition_checksum(
        &self,
        slot: Slot,
        partition: PartitionType,
        length: u64,
    ) -> Result<String, String> {
        if self.corrupt_readback {
            return Ok(crate::sha256::Sha256::hex(b"corrupted_readback_data"));
        }
        let data = self
            .partitions
            .get(&(slot, partition))
            .map(|v| v.as_slice())
            .unwrap_or(&[]);
        let read_len = (length as usize).min(data.len());
        Ok(crate::sha256::Sha256::hex(&data[..read_len]))
    }

    fn get_active_sound_card_selection(&self) -> Result<Option<String>, String> {
        Ok(self
            .sound_card_selections
            .get(&self.active_slot)
            .cloned()
            .flatten())
    }

    fn get_sound_card_selection(&self, slot: Slot) -> Result<Option<String>, String> {
        Ok(self.sound_card_selections.get(&slot).cloned().flatten())
    }

    fn set_sound_card_selection(&mut self, slot: Slot, content: &str) -> Result<(), String> {
        self.sound_card_selections
            .insert(slot, Some(content.to_string()));
        Ok(())
    }

    fn sync_partitions(&mut self) -> Result<(), String> {
        Ok(())
    }

    fn arm_trial_boot(&mut self, target_slot: Slot) -> Result<(), String> {
        self.trial_armed = Some(target_slot);
        Ok(())
    }

    fn clear_trial_boot(&mut self) -> Result<(), String> {
        self.trial_armed = None;
        Ok(())
    }

    #[cfg(test)]
    fn is_trial_armed(&self) -> Result<Option<Slot>, String> {
        Ok(self.trial_armed)
    }

    fn trigger_tryboot(&mut self) -> Result<(), String> {
        self.tryboot_triggered = true;
        Ok(())
    }

    fn trigger_reboot(&mut self) -> Result<(), String> {
        self.reboot_triggered = true;
        Ok(())
    }

    fn arm_watchdog(&mut self, timeout_secs: u64) -> Result<(), String> {
        self.watchdog_armed = Some(timeout_secs);
        Ok(())
    }

    fn disarm_watchdog(&mut self) -> Result<(), String> {
        self.watchdog_disarmed = true;
        Ok(())
    }

    fn get_uptime_secs(&self) -> Result<f64, String> {
        let reads = self
            .uptime_reads
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let step = self.uptime_step.load(std::sync::atomic::Ordering::SeqCst) as f64 / 1000.0;
        Ok(self.uptime_secs + (reads as f64 * step))
    }

    fn check_health(&self) -> Result<HealthReport, String> {
        if let Some(ref report) = self.health_report {
            Ok(report.clone())
        } else {
            Ok(HealthReport {
                healthy: true,
                system_target_reached: true,
                data_mounted: true,
                sound_card_openable: true,
                ssh_listening: true,
                ssh_key_file_present: true,
                details: None,
            })
        }
    }
}

#[cfg(target_os = "linux")]
fn mount_vfat(dev: &Path, target: &Path) -> Result<(), String> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let c_dev = CString::new(dev.as_os_str().as_bytes())
        .map_err(|e| format!("invalid device path: {e}"))?;
    let c_target = CString::new(target.as_os_str().as_bytes())
        .map_err(|e| format!("invalid mount point path: {e}"))?;
    let c_fstype = CString::new("vfat").map_err(|e| format!("invalid filesystem type: {e}"))?;

    extern "C" {
        fn mount(
            special_file: *const std::os::raw::c_char,
            dir: *const std::os::raw::c_char,
            fstype: *const std::os::raw::c_char,
            rwflag: std::os::raw::c_ulong,
            data: *const std::os::raw::c_void,
        ) -> std::os::raw::c_int;
    }

    // SAFETY:
    // 1. `c_dev`, `c_target`, and `c_fstype` are valid null-terminated C strings that remain valid for the duration of the call.
    // 2. `data` is NULL, which is valid for standard vfat mount.
    let ret = unsafe {
        mount(
            c_dev.as_ptr(),
            c_target.as_ptr(),
            c_fstype.as_ptr(),
            0,
            std::ptr::null(),
        )
    };
    if ret != 0 {
        let err = std::io::Error::last_os_error();
        return Err(format!(
            "failed to mount {} at {} (vfat): {err}",
            dev.display(),
            target.display()
        ));
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn mount_vfat(dev: &Path, target: &Path) -> Result<(), String> {
    let output = std::process::Command::new("mount")
        .arg("-t")
        .arg("vfat")
        .arg(dev)
        .arg(target)
        .output()
        .map_err(|e| format!("failed to execute mount: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "failed to mount {} at {}: {stderr}",
            dev.display(),
            target.display()
        ));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn unmount_target(target: &Path) -> Result<(), String> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let c_target = CString::new(target.as_os_str().as_bytes())
        .map_err(|e| format!("invalid unmount target path: {e}"))?;

    extern "C" {
        fn umount2(
            target: *const std::os::raw::c_char,
            flags: std::os::raw::c_int,
        ) -> std::os::raw::c_int;
    }

    // SAFETY: `c_target` is a valid null-terminated C string valid for the duration of the call.
    let ret = unsafe { umount2(c_target.as_ptr(), 0) };
    if ret != 0 {
        let err = std::io::Error::last_os_error();
        return Err(format!("failed to unmount {}: {err}", target.display()));
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn unmount_target(target: &Path) -> Result<(), String> {
    let output = std::process::Command::new("umount")
        .arg(target)
        .output()
        .map_err(|e| format!("failed to execute umount: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("failed to unmount {}: {stderr}", target.display()));
    }
    Ok(())
}

/// Scoped RAII mount guard ensuring boot partition is unmounted when dropped (AC4).
struct ScopedBootMount {
    mount_dir: PathBuf,
    needs_unmount: bool,
}

impl ScopedBootMount {
    fn open(dev: &Path, default_mount_dir: &Path) -> Result<Self, String> {
        if let Ok(dir) = std::env::var("NEXTOOMIO_INACTIVE_BOOT_DIR") {
            let p = PathBuf::from(dir);
            fs::create_dir_all(&p)
                .map_err(|e| format!("cannot create dir {}: {e}", p.display()))?;
            return Ok(Self {
                mount_dir: p,
                needs_unmount: false,
            });
        }

        fs::create_dir_all(default_mount_dir).map_err(|e| {
            format!(
                "cannot create mount point {}: {e}",
                default_mount_dir.display()
            )
        })?;

        mount_vfat(dev, default_mount_dir)?;

        Ok(Self {
            mount_dir: default_mount_dir.to_path_buf(),
            needs_unmount: true,
        })
    }

    fn path(&self) -> &Path {
        &self.mount_dir
    }
}

impl Drop for ScopedBootMount {
    fn drop(&mut self) {
        if self.needs_unmount {
            let _ = unmount_target(&self.mount_dir);
        }
    }
}

/// Scoped RAII mount guard for /boot/select partition: remounts rw for writing, and ro on drop (AC5, AR-01).
struct ScopedSelectRwMount {
    mount_dir: Option<PathBuf>,
}

impl ScopedSelectRwMount {
    fn remount_if_needed(autoboot_path: &Path) -> Result<Self, String> {
        if std::env::var("NEXTOOMIO_SELECT_DIR").is_ok()
            || std::env::var("NEXTOOMIO_AUTOBOOT_TXT").is_ok()
        {
            return Ok(Self { mount_dir: None });
        }
        let _mount_point = autoboot_path
            .parent()
            .unwrap_or_else(|| Path::new("/boot/select"));
        #[cfg(target_os = "linux")]
        {
            let output = std::process::Command::new("mount")
                .args(["-o", "remount,rw"])
                .arg(_mount_point)
                .output()
                .map_err(|e| {
                    format!(
                        "cannot run mount to remount rw {}: {e}",
                        _mount_point.display()
                    )
                })?;
            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                return Err(format!(
                    "failed to remount rw {}: {stderr}",
                    _mount_point.display()
                ));
            }
            Ok(Self {
                mount_dir: Some(_mount_point.to_path_buf()),
            })
        }
        #[cfg(not(target_os = "linux"))]
        {
            Ok(Self { mount_dir: None })
        }
    }
}

impl Drop for ScopedSelectRwMount {
    fn drop(&mut self) {
        if let Some(_mount_point) = &self.mount_dir {
            #[cfg(target_os = "linux")]
            {
                let _ = std::process::Command::new("mount")
                    .args(["-o", "remount,ro"])
                    .arg(_mount_point)
                    .output();
            }
        }
    }
}

/// Finds the card number in `/proc/asound/cards` matching the given overlay or configuration name.
pub fn find_sound_card_index(cards_content: &str, overlay_name: &str) -> Option<u32> {
    let clean = overlay_name.replace('-', "_");
    let overlay_lower = overlay_name.to_lowercase();
    let clean_lower = clean.to_lowercase();
    let mut current_idx: Option<u32> = None;
    let mut current_matches = false;

    for line in cards_content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let line_lower = line.to_lowercase();
        if let Some(first_char) = line.trim_start().chars().next() {
            if first_char.is_ascii_digit() {
                if let Some(idx) = current_idx {
                    if current_matches {
                        return Some(idx);
                    }
                }
                let mut parts = line.split_whitespace();
                if let Some(num_str) = parts.next() {
                    if let Ok(idx) = num_str.parse::<u32>() {
                        current_idx = Some(idx);
                        current_matches = line_lower.contains(&overlay_lower)
                            || line_lower.contains(&clean_lower);
                        continue;
                    }
                }
            }
        }
        if current_idx.is_some()
            && (line_lower.contains(&overlay_lower) || line_lower.contains(&clean_lower))
        {
            current_matches = true;
        }
    }

    if current_matches {
        current_idx
    } else {
        None
    }
}

pub fn is_card_device_openable(card_idx: u32) -> bool {
    let control_path = PathBuf::from(format!("/dev/snd/controlC{card_idx}"));
    if let Ok(file) = File::open(&control_path) {
        drop(file);
        return true;
    }
    let pcm_p = PathBuf::from(format!("/dev/snd/pcmC{card_idx}D0p"));
    if let Ok(file) = File::open(&pcm_p) {
        drop(file);
        return true;
    }
    let pcm_c = PathBuf::from(format!("/dev/snd/pcmC{card_idx}D0c"));
    if let Ok(file) = File::open(&pcm_c) {
        drop(file);
        return true;
    }
    false
}

/// Validates an SSH authorized_keys file per AD-23:
/// - Maximum file size 16 KiB (16384 bytes)
/// - Maximum 16 keys
/// - Accepted key types: ssh-ed25519, ecdsa-sha2-nistp256, ecdsa-sha2-nistp384, ecdsa-sha2-nistp521, ssh-rsa (>= 3072 bits)
/// - Key options forbidden
/// - Tolerates Windows line endings
pub fn validate_ssh_authorized_keys(content: &str) -> Result<usize, String> {
    if content.len() > 16384 {
        return Err("authorized_keys file exceeds 16 KiB limit (AD-23)".to_string());
    }
    let mut key_count = 0;
    for (line_num, line) in content.lines().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        key_count += 1;
        if key_count > 16 {
            return Err("authorized_keys file exceeds maximum of 16 keys (AD-23)".to_string());
        }
        let mut tokens = trimmed.split_whitespace();
        let key_type = tokens.next().unwrap_or("");
        match key_type {
            "ssh-ed25519"
            | "ecdsa-sha2-nistp256"
            | "ecdsa-sha2-nistp384"
            | "ecdsa-sha2-nistp521" => {
                if tokens.next().is_none() {
                    return Err(format!(
                        "line {} has missing key data for {key_type}",
                        line_num + 1
                    ));
                }
            }
            "ssh-rsa" => {
                if let Some(b64) = tokens.next() {
                    // AD-23: RSA keys must have at least 3072 bits (modulus length >= 384 bytes, base64 payload >= 500 chars)
                    if b64.len() < 500 {
                        return Err(format!(
                            "line {} has RSA key with less than 3072 bits (AD-23)",
                            line_num + 1
                        ));
                    }
                } else {
                    return Err(format!(
                        "line {} has missing key data for ssh-rsa",
                        line_num + 1
                    ));
                }
            }
            other => {
                return Err(format!(
                    "line {} has disallowed key type or option prefix '{other}' (AD-23)",
                    line_num + 1
                ));
            }
        }
    }
    if key_count == 0 {
        return Err("authorized_keys file contains no valid keys".to_string());
    }
    Ok(key_count)
}

static WATCHDOG_FILE: std::sync::Mutex<Option<File>> = std::sync::Mutex::new(None);

#[cfg(any(target_os = "linux", test))]
fn negotiate_watchdog_timeout(
    requested: i32,
    mut set: impl FnMut(i32) -> Result<(), String>,
    mut get: impl FnMut() -> Result<i32, String>,
) -> Result<i32, String> {
    let set_error = set(requested).err();
    let actual = get()
        .map_err(|e| format!("cannot read watchdog timeout: {e}; set error: {set_error:?}"))?;
    if actual < 2 {
        return Err(format!("unusable watchdog timeout: {actual}"));
    }
    Ok(actual)
}

#[cfg(test)]
#[test]
fn watchdog_timeout_accepts_rejected_or_clamped_request() {
    assert_eq!(
        negotiate_watchdog_timeout(120, |_| Err("too large".into()), || Ok(15)).unwrap(),
        15
    );
    assert_eq!(
        negotiate_watchdog_timeout(120, |_| Ok(()), || Ok(15)).unwrap(),
        15
    );
    assert!(negotiate_watchdog_timeout(120, |_| Ok(()), || Ok(1)).is_err());
    assert!(negotiate_watchdog_timeout(
        120,
        |_| Err("rejected".into()),
        || Err("unavailable".into())
    )
    .is_err());
}

/// Physical slot backend reading from Linux kernel cmdline, mount tables, and block devices.
#[derive(Debug, Clone, Copy, Default)]
pub struct PhysicalSlotBackend;

impl PhysicalSlotBackend {
    fn autoboot_path(&self) -> PathBuf {
        if let Ok(val) = std::env::var("NEXTOOMIO_AUTOBOOT_TXT") {
            PathBuf::from(val)
        } else if let Ok(val) = std::env::var("NEXTOOMIO_SELECT_DIR") {
            PathBuf::from(val).join("autoboot.txt")
        } else {
            PathBuf::from("/boot/select/autoboot.txt")
        }
    }

    fn check_sound_card_openable(&self) -> bool {
        let configured_card = self.get_active_sound_card_selection().ok().flatten();
        let cards = fs::read_to_string("/proc/asound/cards").unwrap_or_default();
        if cards.trim().is_empty() {
            return false;
        }

        if let Some(cfg) = &configured_card {
            let overlay_name = cfg.lines().find_map(|line| {
                let t = line.trim();
                t.strip_prefix("dtoverlay=").map(str::trim)
            });
            if let Some(name) = overlay_name {
                if let Some(card_idx) = find_sound_card_index(&cards, name) {
                    return is_card_device_openable(card_idx);
                } else {
                    return false;
                }
            }
        }

        is_card_device_openable(0)
    }

    fn is_device_path_overridden(&self, slot: Slot, partition: PartitionType) -> bool {
        let env_var = match (slot, partition) {
            (Slot::A, PartitionType::Boot) => "NEXTOOMIO_SLOT_A_BOOT",
            (Slot::A, PartitionType::Root) => "NEXTOOMIO_SLOT_A_ROOT",
            (Slot::B, PartitionType::Boot) => "NEXTOOMIO_SLOT_B_BOOT",
            (Slot::B, PartitionType::Root) => "NEXTOOMIO_SLOT_B_ROOT",
        };
        std::env::var(env_var).is_ok()
    }

    fn get_device_path(&self, slot: Slot, partition: PartitionType) -> PathBuf {
        let env_var = match (slot, partition) {
            (Slot::A, PartitionType::Boot) => "NEXTOOMIO_SLOT_A_BOOT",
            (Slot::A, PartitionType::Root) => "NEXTOOMIO_SLOT_A_ROOT",
            (Slot::B, PartitionType::Boot) => "NEXTOOMIO_SLOT_B_BOOT",
            (Slot::B, PartitionType::Root) => "NEXTOOMIO_SLOT_B_ROOT",
        };
        if let Ok(val) = std::env::var(env_var) {
            PathBuf::from(val)
        } else {
            PathBuf::from(slot.partition_device(partition))
        }
    }
}

/// Pure evaluation function testing local health check criteria from inputs (AC2, AC7).
pub fn evaluate_health_criteria(
    system_target_reached: bool,
    mounts_content: &str,
    sound_card_present_and_openable: bool,
    ssh_key_file_present: bool,
    ssh_listening: bool,
) -> HealthReport {
    // 2. Data partition mounted (strictly from mount table; directory existence does not qualify)
    let data_mounted = mounts_content.lines().any(|l| {
        let parts: Vec<&str> = l.split_whitespace().collect();
        parts.len() >= 2 && parts[1] == "/data"
    });

    let sound_card_openable = sound_card_present_and_openable;

    // 4. SSH server is listening when a valid key file is present; if key file absent, SSH disabled by design (AD-23)
    let effective_ssh_listening = if !ssh_key_file_present {
        true
    } else {
        ssh_listening
    };

    let healthy =
        system_target_reached && data_mounted && sound_card_openable && effective_ssh_listening;

    let mut missing = Vec::new();
    if !system_target_reached {
        missing.push("system target not reached");
    }
    if !data_mounted {
        missing.push("data partition not mounted");
    }
    if !sound_card_openable {
        missing.push("configured sound card missing or cannot be opened");
    }
    if ssh_key_file_present && !effective_ssh_listening {
        missing.push("SSH server not listening with key file present");
    }

    let details = if missing.is_empty() {
        None
    } else {
        Some(missing.join(", "))
    };

    HealthReport {
        healthy,
        system_target_reached,
        data_mounted,
        sound_card_openable,
        ssh_listening: effective_ssh_listening,
        ssh_key_file_present,
        details,
    }
}

/// Checks whether SSH port 22 is listening in Linux /proc/net/tcp or tcp6 tables.
pub fn is_ssh_port_listening(tcp_data: &str, tcp6_data: &str) -> bool {
    let check = |data: &str| {
        data.lines().skip(1).any(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 4 {
                let local_addr = parts[1];
                let state = parts[3];
                local_addr.ends_with(":0016") && state == "0A" // 0x16 = 22, 0A = TCP_LISTEN
            } else {
                false
            }
        })
    };
    check(tcp_data) || check(tcp6_data)
}

impl SlotBackend for PhysicalSlotBackend {
    fn get_active_slot(&self) -> Result<Slot, String> {
        // 0. Environment override for test/developer workstations
        if let Ok(val) = std::env::var("NEXTOOMIO_SLOT") {
            if let Some(slot) = Slot::parse(&val) {
                return Ok(slot);
            }
        }

        // 1. Inspect /proc/cmdline for root device or PARTUUID
        if let Ok(cmdline) = fs::read_to_string("/proc/cmdline") {
            if cmdline.contains("4e58544d-06")
                || cmdline.contains("mmcblk0p6")
                || cmdline.contains("root_b")
            {
                return Ok(Slot::B);
            }
            if cmdline.contains("4e58544d-05")
                || cmdline.contains("mmcblk0p5")
                || cmdline.contains("root_a")
            {
                return Ok(Slot::A);
            }
        }

        // 2. Fallback: inspect /proc/mounts for mount point of /
        if let Ok(mounts) = fs::read_to_string("/proc/mounts") {
            for line in mounts.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 && parts[1] == "/" {
                    let dev = parts[0];
                    if dev.ends_with("p6") || dev.contains("root_b") {
                        return Ok(Slot::B);
                    }
                    if dev.ends_with("p5") || dev.contains("root_a") {
                        return Ok(Slot::A);
                    }
                }
            }
        }

        Err("unable to determine active slot from /proc/cmdline or /proc/mounts".to_string())
    }

    fn get_default_slot(&self) -> Result<Slot, String> {
        let autoboot_path = self.autoboot_path();
        if let Ok(autoboot) = fs::read_to_string(&autoboot_path) {
            let mut in_all_or_general = true;
            for line in autoboot.lines() {
                let trimmed = line.trim();
                if trimmed.starts_with('[') {
                    in_all_or_general = trimmed == "[all]";
                } else if in_all_or_general {
                    if let Some(val) = trimmed.strip_prefix("boot_partition=") {
                        let part = val.trim();
                        if part == "2" {
                            return Ok(Slot::A);
                        } else if part == "3" {
                            return Ok(Slot::B);
                        } else {
                            return Err(format!(
                                "invalid boot_partition '{part}' in {}",
                                autoboot_path.display()
                            ));
                        }
                    } else if let Some(val) = trimmed.strip_prefix("default_partition=") {
                        let part = val.trim();
                        if part == "2" {
                            return Ok(Slot::A);
                        } else if part == "3" {
                            return Ok(Slot::B);
                        } else {
                            return Err(format!(
                                "invalid default_partition '{part}' in {}",
                                autoboot_path.display()
                            ));
                        }
                    }
                }
            }
        }
        self.get_active_slot()
    }

    fn checksum_partition(&self, slot: Slot, partition: PartitionType) -> Result<String, String> {
        let path = self.get_device_path(slot, partition);
        if !path.exists() {
            return Err(format!(
                "partition device {} does not exist",
                path.display()
            ));
        }
        let mut file =
            File::open(&path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
        let mut hasher = crate::sha256::Sha256::new();
        let mut buf = [0u8; 65536];
        loop {
            let n = file
                .read(&mut buf)
                .map_err(|e| format!("read error on {}: {e}", path.display()))?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
        Ok(hasher.hex_digest())
    }

    fn write_partition<R: Read>(
        &mut self,
        slot: Slot,
        partition: PartitionType,
        mut reader: R,
        size: u64,
    ) -> Result<(), String> {
        let path = self.get_device_path(slot, partition);
        let is_override = self.is_device_path_overridden(slot, partition);

        if !path.exists() {
            return Err(format!(
                "target partition device {} does not exist",
                path.display()
            ));
        }

        if !is_override {
            #[cfg(unix)]
            {
                use std::os::unix::fs::FileTypeExt;
                let meta = fs::metadata(&path)
                    .map_err(|e| format!("cannot stat target partition {}: {e}", path.display()))?;
                if !meta.file_type().is_block_device() {
                    return Err(format!(
                        "target partition {} is not a block device",
                        path.display()
                    ));
                }
            }
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(is_override)
            .truncate(false)
            .open(&path)
            .map_err(|e| format!("cannot open partition {}: {e}", path.display()))?;

        file.seek(SeekFrom::Start(0))
            .map_err(|e| format!("seek error on {}: {e}", path.display()))?;

        let mut remaining = size;
        let mut buf = [0u8; 65536];
        while remaining > 0 {
            let to_read = (remaining as usize).min(buf.len());
            let n = reader
                .read(&mut buf[..to_read])
                .map_err(|e| format!("stream read error: {e}"))?;
            if n == 0 {
                return Err(format!(
                    "unexpected EOF while writing partition {}: {remaining} bytes remaining",
                    path.display()
                ));
            }
            file.write_all(&buf[..n])
                .map_err(|e| format!("write error on {}: {e}", path.display()))?;
            remaining -= n as u64;
        }

        file.sync_all()
            .map_err(|e| format!("sync error on {}: {e}", path.display()))?;
        Ok(())
    }

    fn read_partition_checksum(
        &self,
        slot: Slot,
        partition: PartitionType,
        length: u64,
    ) -> Result<String, String> {
        let path = self.get_device_path(slot, partition);
        let mut file =
            File::open(&path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
        file.seek(SeekFrom::Start(0))
            .map_err(|e| format!("seek error on {}: {e}", path.display()))?;

        let mut hasher = crate::sha256::Sha256::new();
        let mut remaining = length;
        let mut buf = [0u8; 65536];
        while remaining > 0 {
            let to_read = (remaining as usize).min(buf.len());
            let n = file
                .read(&mut buf[..to_read])
                .map_err(|e| format!("read error on {}: {e}", path.display()))?;
            if n == 0 {
                return Err(format!(
                    "unexpected EOF while reading back partition {}: {remaining} bytes remaining",
                    path.display()
                ));
            }
            hasher.update(&buf[..n]);
            remaining -= n as u64;
        }

        Ok(hasher.hex_digest())
    }

    fn get_active_sound_card_selection(&self) -> Result<Option<String>, String> {
        let boot_dir = std::env::var("NEXTOOMIO_ACTIVE_BOOT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("/boot"));
        let hw_file = boot_dir.join("nextoomio-hardware.txt");
        if hw_file.exists() {
            fs::read_to_string(&hw_file)
                .map(Some)
                .map_err(|e| format!("cannot read {}: {e}", hw_file.display()))
        } else {
            Ok(None)
        }
    }

    fn get_sound_card_selection(&self, slot: Slot) -> Result<Option<String>, String> {
        let active = self.get_active_slot()?;
        if slot == active {
            let boot_dir = std::env::var("NEXTOOMIO_ACTIVE_BOOT_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/boot"));
            let hw_file = boot_dir.join("nextoomio-hardware.txt");
            return if hw_file.exists() {
                fs::read_to_string(&hw_file)
                    .map(Some)
                    .map_err(|e| format!("cannot read {}: {e}", hw_file.display()))
            } else {
                Ok(None)
            };
        }

        let dev_path = self.get_device_path(slot, PartitionType::Boot);
        let default_mount_dir = PathBuf::from("/run/nextoomio/mnt_boot_inactive");
        let mount_guard = ScopedBootMount::open(&dev_path, &default_mount_dir)?;
        let hw_file = mount_guard.path().join("nextoomio-hardware.txt");
        if hw_file.exists() {
            fs::read_to_string(&hw_file)
                .map(Some)
                .map_err(|e| format!("cannot read {}: {e}", hw_file.display()))
        } else {
            Ok(None)
        }
    }

    fn set_sound_card_selection(&mut self, slot: Slot, content: &str) -> Result<(), String> {
        let active = self.get_active_slot()?;
        if slot == active {
            let boot_dir = std::env::var("NEXTOOMIO_ACTIVE_BOOT_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/boot"));
            fs::create_dir_all(&boot_dir)
                .map_err(|e| format!("cannot create dir {}: {e}", boot_dir.display()))?;
            let hw_file = boot_dir.join("nextoomio-hardware.txt");
            let mut file = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .open(&hw_file)
                .map_err(|e| format!("cannot open {}: {e}", hw_file.display()))?;
            file.write_all(content.as_bytes())
                .map_err(|e| format!("cannot write {}: {e}", hw_file.display()))?;
            file.sync_all()
                .map_err(|e| format!("sync error {}: {e}", hw_file.display()))?;
            return Ok(());
        }

        let dev_path = self.get_device_path(slot, PartitionType::Boot);
        let default_mount_dir = PathBuf::from("/run/nextoomio/mnt_boot_inactive");
        let mount_guard = ScopedBootMount::open(&dev_path, &default_mount_dir)?;
        let hw_file = mount_guard.path().join("nextoomio-hardware.txt");
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&hw_file)
            .map_err(|e| format!("cannot open {}: {e}", hw_file.display()))?;
        file.write_all(content.as_bytes())
            .map_err(|e| format!("cannot write {}: {e}", hw_file.display()))?;
        file.sync_all()
            .map_err(|e| format!("sync error {}: {e}", hw_file.display()))?;
        Ok(())
    }

    fn sync_partitions(&mut self) -> Result<(), String> {
        #[cfg(unix)]
        {
            extern "C" {
                fn sync();
            }
            // SAFETY: sync(2) is a standard POSIX system call taking no arguments and cannot cause undefined behavior.
            unsafe {
                sync();
            }
        }
        Ok(())
    }

    fn arm_trial_boot(&mut self, target_slot: Slot) -> Result<(), String> {
        let autoboot_path = self.autoboot_path();

        let default_slot = self.get_default_slot()?;
        let default_part = match default_slot {
            Slot::A => 2,
            Slot::B => 3,
        };
        let target_part = match target_slot {
            Slot::A => 2,
            Slot::B => 3,
        };

        let content = format!(
            "[all]\ntryboot_a_b=1\nboot_partition={default_part}\n\n[tryboot]\nboot_partition={target_part}\n"
        );

        let parent = autoboot_path.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent)
            .map_err(|e| format!("cannot create dir {}: {e}", parent.display()))?;

        let _mount_guard = ScopedSelectRwMount::remount_if_needed(&autoboot_path)?;

        let tmp_path = parent.join(format!(".autoboot.txt.tmp.{}", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&tmp_path)
            .map_err(|e| format!("cannot open temp file {}: {e}", tmp_path.display()))?;
        file.write_all(content.as_bytes())
            .map_err(|e| format!("cannot write {}: {e}", tmp_path.display()))?;
        file.sync_all()
            .map_err(|e| format!("sync error {}: {e}", tmp_path.display()))?;
        drop(file);

        fs::rename(&tmp_path, &autoboot_path).map_err(|e| {
            format!(
                "atomic rename failed {} -> {}: {e}",
                tmp_path.display(),
                autoboot_path.display()
            )
        })?;

        if let Ok(dir_file) = File::open(parent) {
            let _ = dir_file.sync_all();
        }

        Ok(())
    }

    fn clear_trial_boot(&mut self) -> Result<(), String> {
        let autoboot_path = self.autoboot_path();

        if !autoboot_path.exists() {
            return Ok(());
        }

        let default_slot = self.get_default_slot()?;
        let default_part = match default_slot {
            Slot::A => 2,
            Slot::B => 3,
        };
        let content = format!("[all]\ntryboot_a_b=1\nboot_partition={default_part}\n");

        let parent = autoboot_path.parent().unwrap_or_else(|| Path::new("."));
        let _mount_guard = ScopedSelectRwMount::remount_if_needed(&autoboot_path)?;

        let tmp_path = parent.join(format!(".autoboot.txt.tmp.{}", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&tmp_path)
            .map_err(|e| format!("cannot open temp file {}: {e}", tmp_path.display()))?;
        file.write_all(content.as_bytes())
            .map_err(|e| format!("cannot write {}: {e}", tmp_path.display()))?;
        file.sync_all()
            .map_err(|e| format!("sync error {}: {e}", tmp_path.display()))?;
        drop(file);

        fs::rename(&tmp_path, &autoboot_path).map_err(|e| {
            format!(
                "atomic rename failed {} -> {}: {e}",
                tmp_path.display(),
                autoboot_path.display()
            )
        })?;

        if let Ok(dir_file) = File::open(parent) {
            let _ = dir_file.sync_all();
        }

        Ok(())
    }

    #[cfg(test)]
    fn is_trial_armed(&self) -> Result<Option<Slot>, String> {
        let autoboot_path = self.autoboot_path();

        if !autoboot_path.exists() {
            return Ok(None);
        }
        let text = fs::read_to_string(&autoboot_path)
            .map_err(|e| format!("cannot read {}: {e}", autoboot_path.display()))?;
        let mut in_tryboot = false;
        for line in text.lines() {
            let trimmed = line.trim();
            if trimmed == "[tryboot]" {
                in_tryboot = true;
            } else if trimmed.starts_with('[') {
                in_tryboot = false;
            } else if in_tryboot {
                if let Some(val) = trimmed.strip_prefix("boot_partition=") {
                    if val.trim() == "2" {
                        return Ok(Some(Slot::A));
                    } else if val.trim() == "3" {
                        return Ok(Some(Slot::B));
                    }
                }
            }
        }
        Ok(None)
    }

    /// Triggers tryboot-flagged reboot into inactive partition (AC1).
    ///
    /// # Test hook
    /// If `NEXTOOMIO_NO_REBOOT` is set in the environment, actual reboot command execution
    /// is skipped. Note: this disables the safety reboot and is intended solely for offline
    /// unit and integration test fixtures. It is explicitly NOT honoured on the trial-watchdog
    /// deadline enforcement path (`trigger_reboot`).
    fn trigger_tryboot(&mut self) -> Result<(), String> {
        if std::env::var("NEXTOOMIO_NO_REBOOT").is_ok() {
            return Ok(());
        }
        #[cfg(target_os = "linux")]
        {
            // Raspberry Pi firmware selects [tryboot] section in autoboot.txt only when reboot argument is "0 tryboot"
            let res = std::process::Command::new("reboot")
                .arg("0 tryboot")
                .output()
                .or_else(|_| {
                    std::process::Command::new("systemctl")
                        .arg("reboot")
                        .arg("0 tryboot")
                        .output()
                });
            match res {
                Ok(out) if out.status.success() => Ok(()),
                Ok(out) => {
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    Err(format!(
                        "reboot '0 tryboot' failed with status {}: {stderr}",
                        out.status
                    ))
                }
                Err(e) => Err(format!("failed to execute reboot '0 tryboot': {e}")),
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            Ok(())
        }
    }

    /// Triggers immediate recovery reboot back to the default boot partition (AC3).
    ///
    /// This enforcement method is used on the trial-watchdog deadline path. To guarantee
    /// that trial deadline enforcement cannot be bypassed or silently dropped in production,
    /// this method intentionally does NOT honour `NEXTOOMIO_NO_REBOOT`.
    fn trigger_reboot(&mut self) -> Result<(), String> {
        #[cfg(target_os = "linux")]
        {
            let res = std::process::Command::new("systemctl")
                .arg("reboot")
                .output()
                .or_else(|_| std::process::Command::new("reboot").output());
            match res {
                Ok(out) if out.status.success() => Ok(()),
                Ok(out) => {
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    Err(format!(
                        "reboot failed with status {}: {stderr}",
                        out.status
                    ))
                }
                Err(e) => Err(format!("failed to execute reboot: {e}")),
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            Ok(())
        }
    }

    fn arm_watchdog(&mut self, timeout_secs: u64) -> Result<(), String> {
        let watchdog_path = if let Ok(val) = std::env::var("NEXTOOMIO_WATCHDOG_DEV") {
            PathBuf::from(val)
        } else {
            PathBuf::from("/dev/watchdog")
        };

        if !watchdog_path.exists() {
            return Err(format!(
                "watchdog device not found at {}",
                watchdog_path.display()
            ));
        }

        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::FileTypeExt;
            use std::os::unix::io::AsRawFd;

            let mut guard = WATCHDOG_FILE.lock().unwrap_or_else(|e| e.into_inner());
            if guard.is_some() {
                return Ok(());
            }
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .open(&watchdog_path)
                .map_err(|e| format!("cannot open {}: {e}", watchdog_path.display()))?;

            let is_char_dev = file
                .metadata()
                .map(|m| m.file_type().is_char_device())
                .unwrap_or(false);

            if is_char_dev {
                extern "C" {
                    fn ioctl(
                        fd: std::os::raw::c_int,
                        request: std::os::raw::c_ulong,
                        ...
                    ) -> std::os::raw::c_int;
                }
                negotiate_watchdog_timeout(
                    timeout_secs.min(i32::MAX as u64) as i32,
                    |mut value| {
                        // SAFETY: the ioctl receives a valid fd and a pointer to an i32.
                        if unsafe { ioctl(file.as_raw_fd(), 0xc0045706, &mut value) } < 0 {
                            Err(std::io::Error::last_os_error().to_string())
                        } else {
                            Ok(())
                        }
                    },
                    || {
                        let mut value = 0_i32;
                        // SAFETY: the ioctl writes an i32 through a valid pointer.
                        if unsafe { ioctl(file.as_raw_fd(), 0x80045707, &mut value) } < 0 {
                            Err(std::io::Error::last_os_error().to_string())
                        } else {
                            Ok(value)
                        }
                    },
                )?;
            }

            // Retain the open file descriptor in static WATCHDOG_FILE to keep watchdog running
            *guard = Some(file);
            Ok(())
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = timeout_secs;
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .open(&watchdog_path)
                .map_err(|e| format!("cannot open {}: {e}", watchdog_path.display()))?;
            *WATCHDOG_FILE.lock().unwrap_or_else(|e| e.into_inner()) = Some(file);
            Ok(())
        }
    }

    fn feed_watchdog(&mut self) -> Result<(), String> {
        let mut guard = WATCHDOG_FILE.lock().unwrap_or_else(|e| e.into_inner());
        let file = guard.as_mut().ok_or("watchdog is not armed")?;
        file.write_all(b"\0")
            .map_err(|e| format!("cannot feed watchdog: {e}"))
    }

    fn disarm_watchdog(&mut self) -> Result<(), String> {
        if let Some(mut file) = WATCHDOG_FILE
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            // Linux watchdog API: writing 'V' before closing indicates clean magic close/disarm
            let _ = file.write_all(b"V");
            let _ = file.flush();
        }
        Ok(())
    }

    fn get_uptime_secs(&self) -> Result<f64, String> {
        if let Ok(content) = fs::read_to_string("/proc/uptime") {
            if let Some(first) = content.split_whitespace().next() {
                if let Ok(s) = first.parse::<f64>() {
                    return Ok(s);
                }
            }
        }
        Ok(0.0)
    }

    fn check_health(&self) -> Result<HealthReport, String> {
        #[cfg(target_os = "linux")]
        let system_target_reached = {
            let output = std::process::Command::new("systemctl")
                .arg("is-system-running")
                .output();
            match output {
                Ok(out) => {
                    let status = String::from_utf8_lossy(&out.stdout).trim().to_string();
                    status == "running" || status == "degraded"
                }
                Err(_) => false,
            }
        };
        #[cfg(not(target_os = "linux"))]
        let system_target_reached = true;

        let mounts = fs::read_to_string("/proc/mounts").unwrap_or_default();
        let sound_card_openable = self.check_sound_card_openable();

        let select_dir = PathBuf::from("/boot/select");
        let key_1 = select_dir.join("authorized_keys");
        let key_2 = select_dir.join("authorized_keys.txt");
        let key_path = if key_1.exists() {
            Some(key_1)
        } else if key_2.exists() {
            Some(key_2)
        } else {
            None
        };

        let ssh_key_file_present = match key_path {
            Some(path) => match fs::read_to_string(&path) {
                Ok(content) => validate_ssh_authorized_keys(&content).is_ok(),
                Err(_) => false,
            },
            None => false,
        };

        let tcp = fs::read_to_string("/proc/net/tcp").unwrap_or_default();
        let tcp6 = fs::read_to_string("/proc/net/tcp6").unwrap_or_default();
        let ssh_listening = if !ssh_key_file_present {
            true
        } else {
            is_ssh_port_listening(&tcp, &tcp6)
        };

        Ok(evaluate_health_criteria(
            system_target_reached,
            &mounts,
            sound_card_openable,
            ssh_key_file_present,
            ssh_listening,
        ))
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
    use std::os::unix::io::AsRawFd;
    extern "C" {
        fn posix_fallocate(fd: std::os::raw::c_int, offset: i64, len: i64) -> std::os::raw::c_int;
    }
    if capacity > i64::MAX as u64 {
        return Err("capacity exceeds i64::MAX".to_string());
    }
    let fd = file.as_raw_fd();
    // SAFETY:
    // 1. `file.as_raw_fd()` borrows a valid open file descriptor that remains open for the lifetime of `file`.
    // 2. `capacity as i64` is sound and non-negative because `capacity <= i64::MAX as u64` was validated above.
    // 3. `posix_fallocate` allocates disk extents without modifying file offset and does not truncate.
    let ret = unsafe { posix_fallocate(fd, 0, capacity as i64) };
    if ret != 0 {
        return Err(format!("posix_fallocate failed with errno {ret}"));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub(crate) fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
    use std::os::unix::io::AsRawFd;
    #[repr(C)]
    struct Fstore {
        fst_flags: u32,
        fst_posmode: i32,
        fst_offset: i64,
        fst_length: i64,
        fst_bytesalloc: i64,
    }
    extern "C" {
        fn fcntl(fd: std::os::raw::c_int, cmd: std::os::raw::c_int, ...) -> std::os::raw::c_int;
    }
    const F_PREALLOCATE: std::os::raw::c_int = 42;
    const F_ALLOCATEALL: u32 = 0x00000004;
    const F_PEOFPOSMODE: i32 = 3;

    if capacity > i64::MAX as u64 {
        return Err("capacity exceeds i64::MAX".to_string());
    }

    let mut store = Fstore {
        fst_flags: F_ALLOCATEALL,
        fst_posmode: F_PEOFPOSMODE,
        fst_offset: 0,
        fst_length: capacity as i64,
        fst_bytesalloc: 0,
    };
    let fd = file.as_raw_fd();
    // SAFETY:
    // 1. `file.as_raw_fd()` borrows a valid open file descriptor that remains open for the lifetime of `file`.
    // 2. `store` is uniquely borrowed (`&mut store`), stack-allocated, initialized, and matches the macOS kernel `fstore_t` ABI layout.
    // 3. `capacity as i64` is sound and non-negative because `capacity <= i64::MAX as u64` was validated above.
    let ret = unsafe { fcntl(fd, F_PREALLOCATE, &mut store) };
    if ret != 0 {
        return Err(format!(
            "fcntl(F_PREALLOCATE) failed with error: {}",
            std::io::Error::last_os_error()
        ));
    }
    // F_PREALLOCATE allocates continuous physical disk blocks without updating logical file size (stat.st_size).
    // Synchronize logical file size with the preallocated extents.
    file.set_len(capacity)
        .map_err(|e| format!("cannot set logical file size after preallocation: {e}"))?;
    Ok(())
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub(crate) fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
    file.set_len(capacity)
        .map_err(|e| format!("cannot allocate {capacity} bytes: {e}"))
}

/// Typed errors occurring during staging storage write and preallocation.
#[derive(Debug, PartialEq, Eq)]
pub enum StagingError {
    Oversized { attempted: u64, limit: u64 },
    Stream(String),
    Storage(String),
}

impl fmt::Display for StagingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Oversized { attempted, limit } => {
                write!(
                    f,
                    "payload size {attempted} bytes exceeds reserved staging capacity {limit} bytes"
                )
            }
            Self::Stream(msg) => write!(f, "client stream read error: {msg}"),
            Self::Storage(msg) => write!(f, "storage I/O error: {msg}"),
        }
    }
}

impl std::error::Error for StagingError {}

/// Abstract storage backend for the preallocated update staging reservation.
pub trait StagingStorage: Send + Sync {
    /// Preallocates or initializes the staging area to the given capacity in bytes.
    fn init_staging(&mut self, capacity: u64) -> Result<(), String>;

    /// Returns the currently allocated extent/block size on disk (e.g. 1 GiB).
    fn get_allocated_size(&self) -> Result<u64, String>;

    /// Returns the actual payload length currently stored inside the reservation.
    fn get_payload_size(&self) -> Result<u64, String>;

    /// Streams an incoming payload into the staging file up to the reserved capacity.
    fn write_payload<R: Read>(
        &mut self,
        reader: R,
        declared_len: Option<u64>,
    ) -> Result<u64, StagingError>;

    /// Reads the payload bytes (up to payload_size).
    fn read_payload(&self) -> Result<Vec<u8>, String>;

    /// Returns a streaming reader over the staged payload bytes up to payload_size.
    fn open_payload_reader(&self) -> Result<Box<dyn Read + Send>, String>;

    /// Resets payload length to zero without unlinking, truncating, or punching holes.
    fn cleanup(&mut self) -> Result<(), String>;
}

/// Mock staging storage for deterministic in-memory verification.
#[derive(Debug, Clone)]
pub struct MockStagingStorage {
    pub reserved_capacity: u64,
    pub buffer: Vec<u8>,
    pub payload_size: usize,
}

impl MockStagingStorage {
    pub fn new(capacity: u64) -> Self {
        Self {
            reserved_capacity: capacity,
            buffer: vec![0u8; capacity as usize],
            payload_size: 0,
        }
    }
}

impl StagingStorage for MockStagingStorage {
    fn init_staging(&mut self, capacity: u64) -> Result<(), String> {
        self.reserved_capacity = capacity;
        if self.buffer.len() as u64 != capacity {
            self.buffer = vec![0u8; capacity as usize];
            self.payload_size = 0;
        }
        Ok(())
    }

    fn get_allocated_size(&self) -> Result<u64, String> {
        Ok(self.buffer.len() as u64)
    }

    fn get_payload_size(&self) -> Result<u64, String> {
        Ok(self.payload_size as u64)
    }

    fn write_payload<R: Read>(
        &mut self,
        mut reader: R,
        declared_len: Option<u64>,
    ) -> Result<u64, StagingError> {
        if let Some(decl) = declared_len {
            if decl > self.reserved_capacity {
                return Err(StagingError::Oversized {
                    attempted: decl,
                    limit: self.reserved_capacity,
                });
            }
        }

        let mut read_bytes = 0usize;
        let mut chunk = [0u8; 8192];

        loop {
            let n = reader
                .read(&mut chunk)
                .map_err(|e| StagingError::Stream(format!("read error: {e}")))?;
            if n == 0 {
                break;
            }

            if (read_bytes + n) as u64 > self.reserved_capacity {
                return Err(StagingError::Oversized {
                    attempted: (read_bytes + n) as u64,
                    limit: self.reserved_capacity,
                });
            }

            self.buffer[read_bytes..read_bytes + n].copy_from_slice(&chunk[..n]);
            read_bytes += n;
        }

        self.payload_size = read_bytes;
        Ok(read_bytes as u64)
    }

    fn read_payload(&self) -> Result<Vec<u8>, String> {
        Ok(self.buffer[..self.payload_size].to_vec())
    }

    fn open_payload_reader(&self) -> Result<Box<dyn Read + Send>, String> {
        let size = self.payload_size.min(self.buffer.len());
        Ok(Box::new(std::io::Cursor::new(self.buffer[..size].to_vec())))
    }

    fn cleanup(&mut self) -> Result<(), String> {
        // Preallocated extent is NEVER shortened (AC6, AR-08)
        self.payload_size = 0;
        Ok(())
    }
}

/// Preallocated file size for staging metadata in bytes (1 disk block / 4096 bytes).
pub const STAGING_META_FILE_SIZE: u64 = 4096;

/// Filesystem-backed staging storage managing `/data/update/staging.swu`.
#[derive(Debug, Clone)]
pub struct FileStagingStorage {
    pub staging_dir: PathBuf,
    pub staging_file: PathBuf,
    pub meta_file: PathBuf,
    pub reserved_capacity: u64,
}

impl FileStagingStorage {
    pub fn new(staging_dir: impl AsRef<Path>, capacity: u64) -> Self {
        let dir = staging_dir.as_ref().to_path_buf();
        let staging_file = dir.join("staging.swu");
        let meta_file = dir.join("staging.meta");
        Self {
            staging_dir: dir,
            staging_file,
            meta_file,
            reserved_capacity: capacity,
        }
    }

    /// Persists metadata in place into the preallocated staging.meta file without truncating (AC1, UPD-05, AR-01).
    fn write_meta_in_place(&self, payload_size: u64) -> Result<(), String> {
        let meta = StagingMeta {
            payload_size,
            reserved_capacity: self.reserved_capacity,
        };
        let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
        let json_bytes = meta_json.as_bytes();
        if json_bytes.len() > STAGING_META_FILE_SIZE as usize {
            return Err(format!(
                "staging metadata size ({} bytes) exceeds preallocated size ({} bytes)",
                json_bytes.len(),
                STAGING_META_FILE_SIZE
            ));
        }

        let mut meta_file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.meta_file)
            .map_err(|e| format!("cannot open {}: {e}", self.meta_file.display()))?;

        let meta_len = meta_file.metadata().map_err(|e| e.to_string())?.len();
        if meta_len < STAGING_META_FILE_SIZE {
            allocate_file_extents(&meta_file, STAGING_META_FILE_SIZE)?;
        }

        let mut buf = vec![0u8; STAGING_META_FILE_SIZE as usize];
        buf[..json_bytes.len()].copy_from_slice(json_bytes);

        meta_file
            .seek(SeekFrom::Start(0))
            .map_err(|e| format!("seek error {}: {e}", self.meta_file.display()))?;
        meta_file
            .write_all(&buf)
            .map_err(|e| format!("write error {}: {e}", self.meta_file.display()))?;
        meta_file
            .sync_all()
            .map_err(|e| format!("sync_all error {}: {e}", self.meta_file.display()))?;

        if let Ok(dir) = File::open(&self.staging_dir) {
            let _ = dir.sync_all();
        }

        Ok(())
    }
}

#[derive(serde::Serialize, serde::Deserialize)]
struct StagingMeta {
    payload_size: u64,
    reserved_capacity: u64,
}

impl StagingStorage for FileStagingStorage {
    fn init_staging(&mut self, capacity: u64) -> Result<(), String> {
        self.reserved_capacity = capacity;
        fs::create_dir_all(&self.staging_dir)
            .map_err(|e| format!("cannot create {}: {e}", self.staging_dir.display()))?;

        // 1. Open or create staging file without truncating (AC6)
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.staging_file)
            .map_err(|e| format!("cannot open {}: {e}", self.staging_file.display()))?;

        // 2. Preallocate extents if newly created or smaller than requested capacity
        let current_meta = file
            .metadata()
            .map_err(|e| format!("cannot stat {}: {e}", self.staging_file.display()))?;

        #[cfg(unix)]
        let current_allocated = {
            use std::os::unix::fs::MetadataExt;
            current_meta.blocks() * 512
        };
        #[cfg(not(unix))]
        let current_allocated = current_meta.len();

        if current_allocated < capacity || current_meta.len() < capacity {
            allocate_file_extents(&file, capacity)?;
        }

        // 3. Preallocate staging.meta file without truncating (AC1)
        let meta_file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.meta_file)
            .map_err(|e| format!("cannot open {}: {e}", self.meta_file.display()))?;

        let meta_stat = meta_file
            .metadata()
            .map_err(|e| format!("cannot stat {}: {e}", self.meta_file.display()))?;

        if meta_stat.len() < STAGING_META_FILE_SIZE {
            allocate_file_extents(&meta_file, STAGING_META_FILE_SIZE)?;
        }

        // 4. Metadata initialization (idempotent: preserves existing payload_size if valid)
        let current_payload = self.get_payload_size().unwrap_or(0);
        self.write_meta_in_place(current_payload)?;

        Ok(())
    }

    fn get_allocated_size(&self) -> Result<u64, String> {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            fs::metadata(&self.staging_file)
                .map(|m| m.blocks() * 512)
                .map_err(|e| {
                    format!(
                        "cannot stat staging file {}: {e}",
                        self.staging_file.display()
                    )
                })
        }
        #[cfg(not(unix))]
        {
            fs::metadata(&self.staging_file)
                .map(|m| m.len())
                .map_err(|e| {
                    format!(
                        "cannot stat staging file {}: {e}",
                        self.staging_file.display()
                    )
                })
        }
    }

    fn get_payload_size(&self) -> Result<u64, String> {
        if !self.meta_file.exists() {
            return Ok(0);
        }
        let data = fs::read(&self.meta_file)
            .map_err(|e| format!("cannot read meta file {}: {e}", self.meta_file.display()))?;
        if data.is_empty() || data.iter().all(|&b| b == 0) {
            return Ok(0);
        }
        let end_pos = data.iter().position(|&b| b == 0).unwrap_or(data.len());
        let meta: StagingMeta = serde_json::from_slice(&data[..end_pos])
            .map_err(|e| format!("invalid meta json: {e}"))?;
        Ok(meta.payload_size)
    }

    fn write_payload<R: Read>(
        &mut self,
        mut reader: R,
        declared_len: Option<u64>,
    ) -> Result<u64, StagingError> {
        if let Some(decl) = declared_len {
            if decl > self.reserved_capacity {
                return Err(StagingError::Oversized {
                    attempted: decl,
                    limit: self.reserved_capacity,
                });
            }
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&self.staging_file)
            .map_err(|e| {
                StagingError::Storage(format!("cannot open {}: {e}", self.staging_file.display()))
            })?;

        file.seek(SeekFrom::Start(0))
            .map_err(|e| StagingError::Storage(format!("seek error: {e}")))?;

        let mut total_written = 0u64;
        let mut buf = [0u8; 65536];

        loop {
            let n = reader
                .read(&mut buf)
                .map_err(|e| StagingError::Stream(format!("read error: {e}")))?;
            if n == 0 {
                break;
            }
            if total_written + (n as u64) > self.reserved_capacity {
                return Err(StagingError::Oversized {
                    attempted: total_written + (n as u64),
                    limit: self.reserved_capacity,
                });
            }
            file.write_all(&buf[..n])
                .map_err(|e| StagingError::Storage(format!("write error: {e}")))?;
            total_written += n as u64;
        }

        // Ensure all written payload extents are committed to durable storage (UPD-05, AR-01)
        file.sync_all()
            .map_err(|e| StagingError::Storage(format!("sync_all error on staging file: {e}")))?;

        // Persist metadata in-place into preallocated staging.meta before returning (AC1)
        self.write_meta_in_place(total_written)
            .map_err(StagingError::Storage)?;

        Ok(total_written)
    }

    fn read_payload(&self) -> Result<Vec<u8>, String> {
        let size = self.get_payload_size()?;
        if size > self.reserved_capacity {
            return Err("payload size exceeds reserved capacity".to_string());
        }
        let mut file = File::open(&self.staging_file)
            .map_err(|e| format!("cannot open {}: {e}", self.staging_file.display()))?;
        let mut buf = vec![0u8; size as usize];
        file.read_exact(&mut buf)
            .map_err(|e| format!("cannot read payload: {e}"))?;
        Ok(buf)
    }

    fn open_payload_reader(&self) -> Result<Box<dyn Read + Send>, String> {
        let size = self.get_payload_size()?;
        if size > self.reserved_capacity {
            return Err("payload size exceeds reserved capacity".to_string());
        }
        let file = File::open(&self.staging_file)
            .map_err(|e| format!("cannot open {}: {e}", self.staging_file.display()))?;
        Ok(Box::new(file.take(size)))
    }

    fn cleanup(&mut self) -> Result<(), String> {
        // Metadata is reset to zero in-place; staging.swu and staging.meta are NEVER unlinked or truncated (AC1, AC6, AR-08)
        self.write_meta_in_place(0)
    }
}
