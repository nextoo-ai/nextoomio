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

/// Abstract slot interface for inspecting active/default slots and writing to the inactive slot.
pub trait SlotBackend: Send + Sync {
    /// Returns the currently running system slot.
    fn get_active_slot(&self) -> Result<Slot, String>;

    /// Returns the currently configured default boot slot.
    fn get_default_slot(&self) -> Result<Slot, String>;

    /// Sets the default boot slot (switches tryboot or autoboot.txt selection).
    fn set_default_slot(&mut self, slot: Slot) -> Result<(), String>;

    /// Writes raw payload to the inactive slot block devices.
    fn write_inactive_slot(&mut self, slot: Slot, data: &[u8]) -> Result<(), String>;
}

/// Mock slot backend for unit tests.
/// Tracks every modification to ensure AC2 and AC3 are preserved.
#[derive(Debug, Clone)]
pub struct MockSlotBackend {
    pub active_slot: Slot,
    pub default_slot: Slot,
    pub inactive_writes: Vec<(Slot, usize)>,
    pub default_slot_changes: Vec<Slot>,
}

impl MockSlotBackend {
    pub fn new(active: Slot, default: Slot) -> Self {
        Self {
            active_slot: active,
            default_slot: default,
            inactive_writes: Vec::new(),
            default_slot_changes: Vec::new(),
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

    fn set_default_slot(&mut self, slot: Slot) -> Result<(), String> {
        self.default_slot = slot;
        self.default_slot_changes.push(slot);
        Ok(())
    }

    fn write_inactive_slot(&mut self, slot: Slot, data: &[u8]) -> Result<(), String> {
        self.inactive_writes.push((slot, data.len()));
        Ok(())
    }
}

/// Physical slot backend reading from Linux kernel cmdline and mount tables.
#[derive(Debug, Clone, Default)]
pub struct PhysicalSlotBackend;

impl SlotBackend for PhysicalSlotBackend {
    fn get_active_slot(&self) -> Result<Slot, String> {
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

        // Default to Slot A if unknown
        Ok(Slot::A)
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

    fn set_default_slot(&mut self, slot: Slot) -> Result<(), String> {
        let part_num = match slot {
            Slot::A => "2",
            Slot::B => "3",
        };
        let content = format!("[all]\ndefault_partition={part_num}\n");
        fs::write("/boot/autoboot.txt", content)
            .map_err(|e| format!("cannot write /boot/autoboot.txt: {e}"))
    }

    fn write_inactive_slot(&mut self, slot: Slot, data: &[u8]) -> Result<(), String> {
        let dev_path = slot.root_device();
        let mut file = OpenOptions::new()
            .write(true)
            .open(dev_path)
            .map_err(|e| format!("cannot open {dev_path}: {e}"))?;
        file.write_all(data)
            .map_err(|e| format!("cannot write to {dev_path}: {e}"))
    }
}

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
    ) -> Result<u64, String>;

    /// Reads the payload bytes (up to payload_size).
    fn read_payload(&self) -> Result<Vec<u8>, String>;

    /// Resets payload length to zero without unlinking, truncating, or punching holes.
    fn cleanup(&mut self) -> Result<(), String>;

    /// Simulates filling the remaining data partition outside the staging reservation (for AC1 testing).
    fn fill_unreserved_space(&mut self, bytes: u64) -> Result<(), String>;
}

/// Mock staging storage for deterministic in-memory verification.
#[derive(Debug, Clone)]
pub struct MockStagingStorage {
    pub reserved_capacity: u64,
    pub buffer: Vec<u8>,
    pub payload_size: usize,
    pub unreserved_free_bytes: u64,
}

impl MockStagingStorage {
    pub fn new(capacity: u64) -> Self {
        Self {
            reserved_capacity: capacity,
            buffer: vec![0u8; capacity as usize],
            payload_size: 0,
            unreserved_free_bytes: 10 * 1024 * 1024 * 1024, // 10 GiB free initially
        }
    }
}

impl StagingStorage for MockStagingStorage {
    fn init_staging(&mut self, capacity: u64) -> Result<(), String> {
        self.reserved_capacity = capacity;
        self.buffer = vec![0u8; capacity as usize];
        self.payload_size = 0;
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
    ) -> Result<u64, String> {
        if let Some(decl) = declared_len {
            if decl > self.reserved_capacity {
                return Err("declared length exceeds reserved staging capacity".to_string());
            }
        }

        let mut read_bytes = 0usize;
        let mut chunk = [0u8; 8192];

        loop {
            let n = reader
                .read(&mut chunk)
                .map_err(|e| format!("read error: {e}"))?;
            if n == 0 {
                break;
            }

            if (read_bytes + n) as u64 > self.reserved_capacity {
                return Err("streamed payload exceeds reserved staging capacity".to_string());
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

    fn cleanup(&mut self) -> Result<(), String> {
        // Preallocated extent is NEVER shortened (AC6, AR-08)
        self.payload_size = 0;
        Ok(())
    }

    fn fill_unreserved_space(&mut self, _bytes: u64) -> Result<(), String> {
        // Fill the rest of the disk so free unreserved space is 0
        self.unreserved_free_bytes = 0;
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

        // Preallocate staging file if missing or wrong size
        let needs_alloc = match fs::metadata(&self.staging_file) {
            Ok(meta) => meta.len() != capacity,
            Err(_) => true,
        };

        if needs_alloc {
            let file = File::create(&self.staging_file)
                .map_err(|e| format!("cannot create {}: {e}", self.staging_file.display()))?;
            file.set_len(capacity)
                .map_err(|e| format!("cannot allocate {} bytes: {e}", capacity))?;
        }

        let meta = StagingMeta {
            payload_size: 0,
            reserved_capacity: capacity,
        };
        let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
        fs::write(&self.meta_file, meta_json)
            .map_err(|e| format!("cannot write meta file: {e}"))?;

        Ok(())
    }

    fn get_allocated_size(&self) -> Result<u64, String> {
        fs::metadata(&self.staging_file)
            .map(|m| m.len())
            .map_err(|e| {
                format!(
                    "cannot stat staging file {}: {e}",
                    self.staging_file.display()
                )
            })
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
    ) -> Result<u64, String> {
        if let Some(decl) = declared_len {
            if decl > self.reserved_capacity {
                return Err("declared length exceeds reserved staging capacity".to_string());
            }
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&self.staging_file)
            .map_err(|e| format!("cannot open {}: {e}", self.staging_file.display()))?;

        file.seek(SeekFrom::Start(0))
            .map_err(|e| format!("seek error: {e}"))?;

        let mut total_written = 0u64;
        let mut buf = [0u8; 65536];

        loop {
            let n = reader
                .read(&mut buf)
                .map_err(|e| format!("read error: {e}"))?;
            if n == 0 {
                break;
            }
            if total_written + (n as u64) > self.reserved_capacity {
                return Err("streamed payload exceeds reserved staging capacity".to_string());
            }
            file.write_all(&buf[..n])
                .map_err(|e| format!("write error: {e}"))?;
            total_written += n as u64;
        }

        file.flush().map_err(|e| format!("flush error: {e}"))?;

        let meta = StagingMeta {
            payload_size: total_written,
            reserved_capacity: self.reserved_capacity,
        };
        let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
        fs::write(&self.meta_file, meta_json)
            .map_err(|e| format!("cannot write meta file: {e}"))?;

        Ok(total_written)
    }

    fn read_payload(&self) -> Result<Vec<u8>, String> {
        let size = self.get_payload_size()? as usize;
        let mut file = File::open(&self.staging_file)
            .map_err(|e| format!("cannot open {}: {e}", self.staging_file.display()))?;
        let mut buf = vec![0u8; size];
        file.read_exact(&mut buf)
            .map_err(|e| format!("cannot read payload: {e}"))?;
        Ok(buf)
    }

    fn cleanup(&mut self) -> Result<(), String> {
        // Metadata is reset to zero, but staging.swu file is NEVER unlinked or truncated (AC6)
        let meta = StagingMeta {
            payload_size: 0,
            reserved_capacity: self.reserved_capacity,
        };
        let meta_json = serde_json::to_string(&meta).map_err(|e| e.to_string())?;
        fs::write(&self.meta_file, meta_json)
            .map_err(|e| format!("cannot write meta file: {e}"))?;
        Ok(())
    }

    fn fill_unreserved_space(&mut self, _bytes: u64) -> Result<(), String> {
        Ok(())
    }
}
