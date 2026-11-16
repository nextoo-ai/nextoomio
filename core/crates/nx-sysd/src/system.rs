//! Hardware, storage, and slot abstractions for update staging and installation.
//!
//! Exposes traits so that update staging and validation can run without physical
//! block devices on developer workstations (AD-20, AC1..AC6).

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// System boot slot identity (Slot A or Slot B) per ARCHITECTURE.md section 3.6.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Slot {
    A,
    B,
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

/// Abstract slot interface for inspecting active and default boot slots (AC2, AC3).
pub trait SlotBackend: Send + Sync {
    /// Returns the currently running system slot.
    fn get_active_slot(&self) -> Result<Slot, String>;

    /// Returns the currently configured default boot slot.
    fn get_default_slot(&self) -> Result<Slot, String>;
}

/// Mock slot backend for unit tests.
#[derive(Debug, Clone)]
pub struct MockSlotBackend {
    pub active_slot: Slot,
    pub default_slot: Slot,
}

impl MockSlotBackend {
    pub fn new(active: Slot, default: Slot) -> Self {
        Self {
            active_slot: active,
            default_slot: default,
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
}

/// Physical slot backend reading from Linux kernel cmdline and mount tables.
#[derive(Debug, Clone, Default)]
pub struct PhysicalSlotBackend;

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
}

#[cfg(target_os = "linux")]
fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
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
fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
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
fn allocate_file_extents(file: &File, capacity: u64) -> Result<(), String> {
    file.set_len(capacity)
        .map_err(|e| format!("cannot allocate {capacity} bytes: {e}"))
}

/// Typed errors occurring during staging storage write and preallocation.
#[derive(Debug, PartialEq, Eq)]
pub enum StagingError {
    Oversized { attempted: u64, limit: u64 },
    Io(String),
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
            Self::Io(msg) => write!(f, "storage I/O error: {msg}"),
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
                .map_err(|e| StagingError::Io(format!("read error: {e}")))?;
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

    /// Atomically persists metadata by writing to a temporary file, syncing, and renaming (UPD-05, AR-01).
    fn write_meta_atomic(&self, payload_size: u64) -> Result<(), String> {
        let meta = StagingMeta {
            payload_size,
            reserved_capacity: self.reserved_capacity,
        };
        let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
        let tmp_path = self.staging_dir.join("staging.meta.tmp");

        let mut tmp_file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&tmp_path)
            .map_err(|e| format!("cannot open {}: {e}", tmp_path.display()))?;

        tmp_file
            .write_all(meta_json.as_bytes())
            .map_err(|e| format!("write error {}: {e}", tmp_path.display()))?;

        tmp_file
            .sync_all()
            .map_err(|e| format!("sync_all error {}: {e}", tmp_path.display()))?;

        drop(tmp_file);

        fs::rename(&tmp_path, &self.meta_file)
            .map_err(|e| format!("cannot rename to {}: {e}", self.meta_file.display()))?;

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

        // 3. Metadata initialization (idempotent: never resets existing payload_size)
        if self.meta_file.exists() {
            if let Ok(content) = fs::read_to_string(&self.meta_file) {
                if let Ok(mut meta) = serde_json::from_str::<StagingMeta>(&content) {
                    if meta.reserved_capacity != capacity {
                        meta.reserved_capacity = capacity;
                        if let Ok(json) = serde_json::to_string(&meta) {
                            let _ = fs::write(&self.meta_file, json);
                        }
                    }
                }
            }
        } else {
            let meta = StagingMeta {
                payload_size: 0,
                reserved_capacity: capacity,
            };
            let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
            fs::write(&self.meta_file, meta_json)
                .map_err(|e| format!("cannot write meta file: {e}"))?;
        }

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
        let content = fs::read_to_string(&self.meta_file)
            .map_err(|e| format!("cannot read meta file: {e}"))?;
        let meta: StagingMeta =
            serde_json::from_str(&content).map_err(|e| format!("invalid meta json: {e}"))?;
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
                StagingError::Io(format!("cannot open {}: {e}", self.staging_file.display()))
            })?;

        file.seek(SeekFrom::Start(0))
            .map_err(|e| StagingError::Io(format!("seek error: {e}")))?;

        let mut total_written = 0u64;
        let mut buf = [0u8; 65536];

        loop {
            let n = reader
                .read(&mut buf)
                .map_err(|e| StagingError::Io(format!("read error: {e}")))?;
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
                .map_err(|e| StagingError::Io(format!("write error: {e}")))?;
            total_written += n as u64;
        }

        // Ensure all written payload extents are committed to durable storage (UPD-05, AR-01)
        file.sync_all()
            .map_err(|e| StagingError::Io(format!("sync_all error on staging file: {e}")))?;

        // Atomically commit metadata to disk before returning (write to temp file, sync, atomic rename)
        self.write_meta_atomic(total_written)
            .map_err(StagingError::Io)?;

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
        // Metadata is reset to zero atomically, but staging.swu file is NEVER unlinked or truncated (AC6, AR-08)
        self.write_meta_atomic(0)
    }
}
