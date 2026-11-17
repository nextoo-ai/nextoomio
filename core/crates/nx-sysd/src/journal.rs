//! Durable two-record update transaction journal per ARCHITECTURE.md section 3.6.
//!
//! Alternates writes between `journal.0` and `journal.1`, protected by generation
//! numbers and CRC32 checksums. On recovery, selects the newest valid record.

use nx_protocol::UpdateTransactionState;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// Computes CRC32 (IEEE 802.3 standard) checksum.
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

/// Durable transaction journal record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JournalRecord {
    pub format_version: u32,
    pub generation: u64,
    pub tx_id: Option<String>,
    pub state: UpdateTransactionState,
    pub active_slot: String,
    pub default_slot: String,
    pub target_slot: Option<String>,
    pub payload_len: u64,
    pub payload_sha256: Option<String>,
    pub error_code: Option<String>,
    pub consumed_attempts: u32,
    /// The deploy tool requested remote confirmation for this transaction (AD-22).
    ///
    /// Omitted from the serialized record and from the checksum while `false`, so that
    /// records without remote confirmation keep the byte layout of journal format 1.
    #[serde(default, skip_serializing_if = "is_false")]
    pub remote_confirm: bool,
    /// Trial attempt for which the remote confirmation was received; 0 when none.
    ///
    /// A confirmation applies only to the attempt it was received in; a later trial
    /// attempt of the same transaction needs its own confirmation.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub confirmed_attempt: u32,
    #[serde(default)]
    pub checksum: u32,
}

fn is_false(value: &bool) -> bool {
    !*value
}

fn is_zero(value: &u32) -> bool {
    *value == 0
}

impl JournalRecord {
    pub fn new_initial(active: &str, default: &str) -> Self {
        let mut rec = Self {
            format_version: 1,
            generation: 1,
            tx_id: None,
            state: UpdateTransactionState::Idle,
            active_slot: active.to_string(),
            default_slot: default.to_string(),
            target_slot: None,
            payload_len: 0,
            payload_sha256: None,
            error_code: None,
            consumed_attempts: 0,
            remote_confirm: false,
            confirmed_attempt: 0,
            checksum: 0,
        };
        rec.checksum = rec.compute_checksum();
        rec
    }

    /// Whether the current trial attempt has received its remote confirmation (AD-22).
    pub fn is_confirmed(&self) -> bool {
        self.confirmed_attempt != 0 && self.confirmed_attempt == self.consumed_attempts
    }

    pub fn compute_checksum(&self) -> u32 {
        #[derive(Serialize)]
        struct ChecksumPayload<'a> {
            format_version: u32,
            generation: u64,
            tx_id: &'a Option<String>,
            state: UpdateTransactionState,
            active_slot: &'a str,
            default_slot: &'a str,
            target_slot: &'a Option<String>,
            payload_len: u64,
            payload_sha256: &'a Option<String>,
            error_code: &'a Option<String>,
            consumed_attempts: u32,
            #[serde(skip_serializing_if = "is_false")]
            remote_confirm: bool,
            #[serde(skip_serializing_if = "is_zero")]
            confirmed_attempt: u32,
        }

        let payload = ChecksumPayload {
            format_version: self.format_version,
            generation: self.generation,
            tx_id: &self.tx_id,
            state: self.state,
            active_slot: &self.active_slot,
            default_slot: &self.default_slot,
            target_slot: &self.target_slot,
            payload_len: self.payload_len,
            payload_sha256: &self.payload_sha256,
            error_code: &self.error_code,
            consumed_attempts: self.consumed_attempts,
            remote_confirm: self.remote_confirm,
            confirmed_attempt: self.confirmed_attempt,
        };

        if let Ok(bytes) = serde_json::to_vec(&payload) {
            crc32(&bytes)
        } else {
            0
        }
    }

    pub fn is_valid(&self) -> bool {
        self.format_version == 1 && self.checksum == self.compute_checksum()
    }
}

/// Abstract storage for alternating two-record journal files.
pub trait JournalStorage: Send + Sync {
    fn read_record(&self, slot_idx: usize) -> Result<Option<JournalRecord>, String>;
    fn write_record(&mut self, slot_idx: usize, record: &JournalRecord) -> Result<(), String>;
    fn preallocate(&mut self) -> Result<(), String> {
        Ok(())
    }
    /// Whether a journal record exists (valid or not), without writing anything.
    ///
    /// Storage with no record at all still needs initialization; a corrupted record
    /// counts as present so that it is reported instead of being overwritten.
    fn has_records(&self) -> bool {
        !matches!(
            (self.read_record(0), self.read_record(1)),
            (Ok(None), Ok(None))
        )
    }
    /// Takes the single updater lock (ARCHITECTURE.md section 3.6, D-065) without waiting.
    ///
    /// Every state-changing transition holds this lock from loading the journal until its
    /// last write is flushed. The lock is held until the returned guard is dropped, and the
    /// kernel releases it when the holding process dies. Storage that is not shared between
    /// processes returns a guard that holds nothing.
    fn try_lock_updater(&self) -> Result<UpdaterLock, LockError> {
        Ok(UpdaterLock(None))
    }
}

/// Why the updater lock could not be taken.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LockError {
    /// Another process holds the lock (`update_busy`).
    Busy,
    /// The lock file could not be opened or locked.
    Storage(String),
}

impl std::fmt::Display for LockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Busy => f.write_str("the updater lock is held by another process"),
            Self::Storage(msg) => write!(f, "updater lock error: {msg}"),
        }
    }
}

/// Guard for the single updater lock; dropping it releases the lock.
#[derive(Debug)]
pub struct UpdaterLock(Option<fs::File>);

impl Drop for UpdaterLock {
    fn drop(&mut self) {
        if let Some(file) = &self.0 {
            let _ = file.unlock();
        }
    }
}

/// In-memory journal storage for unit tests.
#[derive(Debug, Clone, Default)]
pub struct MemoryJournalStorage {
    pub records: [Option<JournalRecord>; 2],
}

impl JournalStorage for MemoryJournalStorage {
    fn read_record(&self, slot_idx: usize) -> Result<Option<JournalRecord>, String> {
        if slot_idx < 2 {
            match &self.records[slot_idx] {
                Some(rec) => {
                    if rec.is_valid() {
                        Ok(Some(rec.clone()))
                    } else {
                        Err(format!("journal record {slot_idx} is corrupted or invalid"))
                    }
                }
                None => Ok(None),
            }
        } else {
            Err("invalid journal slot index".to_string())
        }
    }

    fn write_record(&mut self, slot_idx: usize, record: &JournalRecord) -> Result<(), String> {
        if slot_idx < 2 {
            self.records[slot_idx] = Some(record.clone());
            Ok(())
        } else {
            Err("invalid journal slot index".to_string())
        }
    }
}

/// File-backed journal storage managing `/data/update/journal.0` and `journal.1`.
#[derive(Debug, Clone)]
pub struct FileJournalStorage {
    pub journal_dir: PathBuf,
}

impl FileJournalStorage {
    /// Preallocated file size for each journal record file in bytes (1 disk block / 4096 bytes).
    pub const JOURNAL_FILE_SIZE: u64 = 4096;

    pub fn new(dir: impl AsRef<Path>) -> Self {
        Self {
            journal_dir: dir.as_ref().to_path_buf(),
        }
    }

    fn path_for(&self, slot_idx: usize) -> PathBuf {
        self.journal_dir.join(format!("journal.{slot_idx}"))
    }

    /// Path of the single updater lock file, `/data/update/.lock` on the device (D-065).
    pub fn lock_path(&self) -> PathBuf {
        self.journal_dir.join(".lock")
    }
}

impl JournalStorage for FileJournalStorage {
    fn try_lock_updater(&self) -> Result<UpdaterLock, LockError> {
        fs::create_dir_all(&self.journal_dir).map_err(|e| {
            LockError::Storage(format!(
                "cannot create dir {}: {e}",
                self.journal_dir.display()
            ))
        })?;
        let path = self.lock_path();
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|e| LockError::Storage(format!("cannot open {}: {e}", path.display())))?;
        // `File::try_lock` is `flock(LOCK_EX | LOCK_NB)` on Unix.
        match file.try_lock() {
            Ok(()) => Ok(UpdaterLock(Some(file))),
            Err(fs::TryLockError::WouldBlock) => Err(LockError::Busy),
            Err(fs::TryLockError::Error(e)) => Err(LockError::Storage(format!(
                "cannot lock {}: {e}",
                path.display()
            ))),
        }
    }

    fn preallocate(&mut self) -> Result<(), String> {
        fs::create_dir_all(&self.journal_dir)
            .map_err(|e| format!("cannot create dir {}: {e}", self.journal_dir.display()))?;
        for slot in 0..2 {
            let path = self.path_for(slot);
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(&path)
                .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
            let meta = file.metadata().map_err(|e| e.to_string())?;
            if meta.len() < Self::JOURNAL_FILE_SIZE {
                crate::system::allocate_file_extents(&file, Self::JOURNAL_FILE_SIZE)?;
            }
        }
        Ok(())
    }

    fn read_record(&self, slot_idx: usize) -> Result<Option<JournalRecord>, String> {
        let path = self.path_for(slot_idx);
        if !path.exists() {
            return Ok(None);
        }
        let data = fs::read(&path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        if data.is_empty() || data.iter().all(|&b| b == 0) {
            return Ok(None);
        }
        let end_pos = data.iter().position(|&b| b == 0).unwrap_or(data.len());
        let json_slice = &data[..end_pos];
        match serde_json::from_slice::<JournalRecord>(json_slice) {
            Ok(rec) => {
                if rec.is_valid() {
                    Ok(Some(rec))
                } else {
                    Err(format!(
                        "journal record at {} has invalid checksum or format",
                        path.display()
                    ))
                }
            }
            Err(e) => Err(format!(
                "journal record at {} is malformed: {e}",
                path.display()
            )),
        }
    }

    fn write_record(&mut self, slot_idx: usize, record: &JournalRecord) -> Result<(), String> {
        fs::create_dir_all(&self.journal_dir)
            .map_err(|e| format!("cannot create dir {}: {e}", self.journal_dir.display()))?;
        let path = self.path_for(slot_idx);
        let json = serde_json::to_string(record)
            .map_err(|e| format!("cannot serialize journal record: {e}"))?;
        let json_bytes = json.as_bytes();
        if json_bytes.len() > Self::JOURNAL_FILE_SIZE as usize {
            return Err(format!(
                "journal record size ({} bytes) exceeds preallocated size ({} bytes)",
                json_bytes.len(),
                Self::JOURNAL_FILE_SIZE
            ));
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|e| format!("cannot open {}: {e}", path.display()))?;

        let meta = file.metadata().map_err(|e| e.to_string())?;
        if meta.len() < Self::JOURNAL_FILE_SIZE {
            crate::system::allocate_file_extents(&file, Self::JOURNAL_FILE_SIZE)?;
        }

        let mut buf = vec![0u8; Self::JOURNAL_FILE_SIZE as usize];
        buf[..json_bytes.len()].copy_from_slice(json_bytes);

        file.seek(SeekFrom::Start(0))
            .map_err(|e| format!("seek error {}: {e}", path.display()))?;
        file.write_all(&buf)
            .map_err(|e| format!("write error {}: {e}", path.display()))?;
        file.sync_all()
            .map_err(|e| format!("sync_all error {}: {e}", path.display()))?;

        if let Ok(dir_file) = fs::File::open(&self.journal_dir) {
            let _ = dir_file.sync_all();
        }

        Ok(())
    }
}

/// Journal controller that alternates between slot 0 and 1.
#[derive(Debug, Clone)]
pub struct JournalController<S: JournalStorage> {
    storage: S,
    active_slot_idx: usize,
    latest_record: JournalRecord,
}

/// Outcome of boot reconciliation across durable boundaries (AR-01, AC4..AC6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReconcileOutcome {
    Unchanged,
    TruncatedFailed,
    TrialInProgress,
    SecondTrialRequested,
    TrialExhausted,
    ContradictoryFailed,
    JournalCorruptedFailed,
    /// An interrupted commit whose selector already names the target slot was finished (D-026).
    CommitFinished,
    /// An interrupted commit whose selector did not name the target slot ended failed,
    /// with the previous slot selected (D-026).
    CommitReturned,
}

/// Typed journal errors distinguishing corruption from storage/I/O issues (CQ-R9, AC6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JournalError {
    Corrupted(String),
    Storage(String),
}

impl std::fmt::Display for JournalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Corrupted(msg) => write!(f, "journal corrupted or invalid: {msg}"),
            Self::Storage(msg) => write!(f, "journal storage error: {msg}"),
        }
    }
}

impl std::error::Error for JournalError {}

impl<S: JournalStorage> JournalController<S> {
    /// Loads the newest valid record, initializing the journal when it has no record.
    ///
    /// Initialization writes; callers sharing the storage between processes hold the
    /// updater lock (D-065).
    pub fn load_or_init(
        mut storage: S,
        active_slot: &str,
        default_slot: &str,
    ) -> Result<Self, JournalError> {
        storage.preallocate().map_err(JournalError::Storage)?;
        match Self::select(&storage)? {
            Some((idx, rec)) => Ok(Self {
                storage,
                active_slot_idx: idx,
                latest_record: rec,
            }),
            None => {
                let init = JournalRecord::new_initial(active_slot, default_slot);
                storage
                    .write_record(0, &init)
                    .map_err(JournalError::Storage)?;
                Ok(Self {
                    storage,
                    active_slot_idx: 0,
                    latest_record: init,
                })
            }
        }
    }

    /// Loads the newest valid record without writing anything (D-065).
    ///
    /// Returns `Ok(None)` when the journal has no record, that is, it is not initialized.
    pub fn load_existing(storage: S) -> Result<Option<Self>, JournalError> {
        Ok(Self::select(&storage)?.map(|(idx, rec)| Self {
            storage,
            active_slot_idx: idx,
            latest_record: rec,
        }))
    }

    fn select(storage: &S) -> Result<Option<(usize, JournalRecord)>, JournalError> {
        let r0 = storage.read_record(0);
        let r1 = storage.read_record(1);

        match (r0, r1) {
            (Ok(Some(a)), Ok(Some(b))) => {
                if a.generation >= b.generation {
                    Ok(Some((0, a)))
                } else {
                    Ok(Some((1, b)))
                }
            }
            (Ok(Some(a)), Ok(None)) | (Ok(Some(a)), Err(_)) => Ok(Some((0, a))),
            (Ok(None), Ok(Some(b))) | (Err(_), Ok(Some(b))) => Ok(Some((1, b))),
            (Ok(None), Ok(None)) => Ok(None),
            (r0_err, r1_err) => Err(JournalError::Corrupted(format!(
                "both journal records are invalid or corrupted (slot 0: {r0_err:?}, slot 1: {r1_err:?}); refusing to discard state"
            ))),
        }
    }

    /// Reloads the newest valid journal record from storage.
    pub fn reload(&mut self) -> Result<(), JournalError> {
        let r0 = self.storage.read_record(0);
        let r1 = self.storage.read_record(1);

        match (r0, r1) {
            (Ok(Some(a)), Ok(Some(b))) => {
                if a.generation >= b.generation {
                    self.active_slot_idx = 0;
                    self.latest_record = a;
                } else {
                    self.active_slot_idx = 1;
                    self.latest_record = b;
                }
                Ok(())
            }
            (Ok(Some(a)), Ok(None)) | (Ok(Some(a)), Err(_)) => {
                self.active_slot_idx = 0;
                self.latest_record = a;
                Ok(())
            }
            (Ok(None), Ok(Some(b))) | (Err(_), Ok(Some(b))) => {
                self.active_slot_idx = 1;
                self.latest_record = b;
                Ok(())
            }
            (Err(e0), Err(e1)) => Err(JournalError::Corrupted(format!(
                "both journal records unreadable: {e0}; {e1}"
            ))),
            (Ok(None), Err(e)) | (Err(e), Ok(None)) => Err(JournalError::Corrupted(format!(
                "one journal record missing and one invalid: {e}"
            ))),
            (Ok(None), Ok(None)) => Err(JournalError::Storage(
                "no journal records found on reload".to_string(),
            )),
        }
    }

    /// The journal storage, for inspection.
    pub fn storage(&self) -> &S {
        &self.storage
    }

    pub fn current(&self) -> &JournalRecord {
        &self.latest_record
    }

    #[allow(clippy::too_many_arguments)]
    pub fn commit_state_with_attempts(
        &mut self,
        next_state: UpdateTransactionState,
        tx_id: Option<String>,
        target_slot: Option<String>,
        payload_len: u64,
        payload_sha256: Option<String>,
        error_code: Option<String>,
        consumed_attempts: u32,
    ) -> Result<JournalRecord, String> {
        // The remote-confirmation request and the received confirmation belong to one
        // transaction and survive only while it stays in trial or commit (AD-22).
        let carries_confirmation = tx_id.is_some()
            && tx_id == self.latest_record.tx_id
            && matches!(
                next_state,
                UpdateTransactionState::Trial
                    | UpdateTransactionState::Committing
                    | UpdateTransactionState::Committed
            );
        let (remote_confirm, confirmed_attempt) = if carries_confirmation {
            (
                self.latest_record.remote_confirm,
                self.latest_record.confirmed_attempt,
            )
        } else {
            (false, 0)
        };

        let rec = JournalRecord {
            format_version: 1,
            generation: 0,
            tx_id,
            state: next_state,
            active_slot: self.latest_record.active_slot.clone(),
            default_slot: self.latest_record.default_slot.clone(),
            target_slot,
            payload_len,
            payload_sha256,
            error_code,
            consumed_attempts,
            remote_confirm,
            confirmed_attempt,
            checksum: 0,
        };
        self.write_next(rec)
    }

    /// Persists a copy of the newest record changed by `change` as the next generation.
    pub fn commit_with(
        &mut self,
        change: impl FnOnce(&mut JournalRecord),
    ) -> Result<JournalRecord, String> {
        let mut rec = self.latest_record.clone();
        change(&mut rec);
        self.write_next(rec)
    }

    fn write_next(&mut self, mut rec: JournalRecord) -> Result<JournalRecord, String> {
        let next_slot_idx = 1 - self.active_slot_idx;
        rec.format_version = 1;
        rec.generation = self.latest_record.generation + 1;
        rec.checksum = rec.compute_checksum();

        self.storage.write_record(next_slot_idx, &rec)?;
        self.active_slot_idx = next_slot_idx;
        self.latest_record = rec.clone();

        Ok(rec)
    }

    /// Takes the single updater lock without waiting; see [`JournalStorage::try_lock_updater`].
    pub fn try_lock_updater(&self) -> Result<UpdaterLock, LockError> {
        self.storage.try_lock_updater()
    }

    pub fn commit_state(
        &mut self,
        next_state: UpdateTransactionState,
        tx_id: Option<String>,
        target_slot: Option<String>,
        payload_len: u64,
        payload_sha256: Option<String>,
        error_code: Option<String>,
    ) -> Result<JournalRecord, String> {
        self.commit_state_with_attempts(
            next_state,
            tx_id,
            target_slot,
            payload_len,
            payload_sha256,
            error_code,
            self.latest_record.consumed_attempts,
        )
    }

    /// Reconciles state after reboot: handles interrupted uploads/installs (AC5 of P0-F4.4),
    /// interrupted commits (D-026), trial attempt progression / two-attempt limits (AC4, AC5),
    /// and contradictory or unreadable records (AC6).
    pub fn reconcile_boot(&mut self, running_slot: &str) -> Result<ReconcileOutcome, String> {
        // 0. Corrupted/unreadable journal check (AC6)
        if self.latest_record.error_code.as_deref() == Some("update_journal_corrupted") {
            return Ok(ReconcileOutcome::JournalCorruptedFailed);
        }

        // 1. Contradiction check: slot format validity (AC6)
        let default_slot_valid =
            crate::system::Slot::parse(&self.latest_record.default_slot).is_some();
        let target_slot_valid = self
            .latest_record
            .target_slot
            .as_deref()
            .map(|t| crate::system::Slot::parse(t).is_some())
            .unwrap_or(true);

        if !default_slot_valid || !target_slot_valid {
            self.commit_state(
                UpdateTransactionState::Failed,
                self.latest_record.tx_id.clone(),
                None,
                self.latest_record.payload_len,
                None,
                Some("update_record_contradictory".to_string()),
            )?;
            return Ok(ReconcileOutcome::ContradictoryFailed);
        }

        // 1b. Contradiction check: target slot matches default slot (AC6)
        if let Some(target) = &self.latest_record.target_slot {
            if (self.latest_record.state == UpdateTransactionState::Trial
                || self.latest_record.state == UpdateTransactionState::Installing
                || self.latest_record.state == UpdateTransactionState::Installed)
                && target == &self.latest_record.default_slot
            {
                self.commit_state(
                    UpdateTransactionState::Failed,
                    self.latest_record.tx_id.clone(),
                    None,
                    self.latest_record.payload_len,
                    None,
                    Some("update_record_contradictory".to_string()),
                )?;
                return Ok(ReconcileOutcome::ContradictoryFailed);
            }
        }

        if self.latest_record.consumed_attempts > 2 {
            self.commit_state(
                UpdateTransactionState::Failed,
                self.latest_record.tx_id.clone(),
                None,
                self.latest_record.payload_len,
                None,
                Some("update_record_contradictory".to_string()),
            )?;
            return Ok(ReconcileOutcome::ContradictoryFailed);
        }

        // 2. Interrupted receiving or installing (AC5 of P0-F4.4)
        if self.latest_record.state == UpdateTransactionState::Receiving
            || self.latest_record.state == UpdateTransactionState::Installing
        {
            self.commit_state(
                UpdateTransactionState::Failed,
                None,
                None,
                0,
                None,
                Some("update_truncated".to_string()),
            )?;
            return Ok(ReconcileOutcome::TruncatedFailed);
        }

        // 3. Interrupted commit (D-026) is reconciled by the update engine, which can read
        //    the selector back; see `UpdateEngine::reconcile_boot`. Reaching this point in
        //    `Committing` means the caller did not do so: keep the record unchanged.

        // 4. Trial boot reconciliation (AC1, AC4, AC5)
        if self.latest_record.state == UpdateTransactionState::Trial {
            if let Some(target) = &self.latest_record.target_slot {
                if running_slot == target {
                    // Running on trial target slot: trial is in progress
                    let running = running_slot.to_string();
                    self.commit_with(|rec| {
                        rec.active_slot = running;
                        rec.error_code = None;
                    })?;

                    return Ok(ReconcileOutcome::TrialInProgress);
                } else if running_slot == self.latest_record.default_slot {
                    // Booted previous slot: trial failed
                    if self.latest_record.consumed_attempts < 2 {
                        // First attempt failed: request exactly one more trial (attempt 2)
                        let target_str = target.clone();
                        let tx_id = self.latest_record.tx_id.clone();
                        let payload_len = self.latest_record.payload_len;
                        let payload_sha = self.latest_record.payload_sha256.clone();

                        self.commit_state_with_attempts(
                            UpdateTransactionState::Trial,
                            tx_id,
                            Some(target_str),
                            payload_len,
                            payload_sha,
                            None,
                            2, // durably record attempt 2 before reboot (AC1, AC5)
                        )?;
                        return Ok(ReconcileOutcome::SecondTrialRequested);
                    } else {
                        // Second attempt failed: state is failed, previous slot stays selected (AC4)
                        self.commit_state_with_attempts(
                            UpdateTransactionState::Failed,
                            self.latest_record.tx_id.clone(),
                            None,
                            self.latest_record.payload_len,
                            None,
                            Some("update_trial_exhausted".to_string()),
                            self.latest_record.consumed_attempts,
                        )?;
                        return Ok(ReconcileOutcome::TrialExhausted);
                    }
                } else {
                    // Running neither target slot nor default slot while in state Trial is contradictory (AC6)
                    self.commit_state(
                        UpdateTransactionState::Failed,
                        self.latest_record.tx_id.clone(),
                        None,
                        self.latest_record.payload_len,
                        None,
                        Some("update_record_contradictory".to_string()),
                    )?;
                    return Ok(ReconcileOutcome::ContradictoryFailed);
                }
            } else {
                self.commit_state(
                    UpdateTransactionState::Failed,
                    self.latest_record.tx_id.clone(),
                    None,
                    self.latest_record.payload_len,
                    None,
                    Some("update_record_contradictory".to_string()),
                )?;
                return Ok(ReconcileOutcome::ContradictoryFailed);
            }
        }

        Ok(ReconcileOutcome::Unchanged)
    }
}
