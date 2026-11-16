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
        // Fall back to external mount utility if direct syscall failed (e.g. loop setup or user namespace)
        let output = std::process::Command::new("mount")
            .arg("-t")
            .arg("vfat")
            .arg(dev)
            .arg(target)
            .output()
            .map_err(|e| format!("mount syscall failed ({err}) and exec mount failed: {e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!(
                "failed to mount {} at {} (vfat): syscall {err}; util: {stderr}",
                dev.display(),
                target.display()
            ));
        }
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
        let output = std::process::Command::new("umount")
            .arg(target)
            .output()
            .map_err(|e| format!("umount syscall failed ({err}) and exec umount failed: {e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!(
                "failed to unmount {}: syscall {err}; util: {stderr}",
                target.display()
            ));
        }
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

/// Physical slot backend reading from Linux kernel cmdline, mount tables, and block devices.
#[derive(Debug, Clone, Default)]
pub struct PhysicalSlotBackend;

impl PhysicalSlotBackend {
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
        // Read /boot/autoboot.txt or default to active slot
        if let Ok(autoboot) = fs::read_to_string("/boot/autoboot.txt") {
            for line in autoboot.lines() {
                let trimmed = line.trim();
                if let Some(val) = trimmed.strip_prefix("default_partition=") {
                    if val.trim() == "3" {
                        return Ok(Slot::B);
                    }
                    if val.trim() == "2" {
                        return Ok(Slot::A);
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
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
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
