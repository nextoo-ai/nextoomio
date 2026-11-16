//! Update engine coordinating staging, validation, state machine, and storage.
//!
//! Enforces SYS-05, UPD-04, UPD-05, AR-01, AR-08, and all update staging and installation acceptance criteria.

use crate::cpio::{
    read_entry_bytes, read_next_cpio_header, skip_entry_data, stream_and_hash_entry_data, CpioError,
};
use crate::journal::{JournalController, JournalStorage, ReconcileOutcome};
use crate::sha256::Sha256;
use crate::sw_description::parse_sw_description;
use crate::system::{HealthReport, PartitionType, Slot, SlotBackend, StagingError, StagingStorage};
use nx_protocol::{ErrorCode, UpdateTransactionState};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fmt;
use std::io::Read;

/// Default preallocated staging reservation capacity: 1 GiB (ARCHITECTURE.md section 3.6 step 1).
pub const DEFAULT_STAGING_RESERVATION: u64 = 1_073_741_824;

/// Default hardware compatibility identifier for Phase 0 bench (Raspberry Pi 4).
pub const DEFAULT_HARDWARE_MODEL: &str = "1.0";

/// Default configuration schema version.
pub const DEFAULT_SCHEMA_VERSION: u32 = 1;

/// Machine-readable update transaction status for CLI and API.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateStatus {
    pub state: UpdateTransactionState,
    pub active_slot: String,
    pub default_slot: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_slot: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tx_id: Option<String>,
    pub payload_length: u64,
    pub reserved_capacity: u64,
    pub consumed_attempts: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// Errors occurring during update staging and validation.
#[derive(Debug, PartialEq, Eq)]
pub enum UpdateEngineError {
    Busy,
    Oversized {
        size: u64,
        limit: u64,
    },
    Truncated(String),
    CorruptedArtifact {
        file: String,
        expected: String,
        actual: String,
    },
    IncompatibleHardware {
        declared: Vec<String>,
        target: String,
    },
    IncompatibleSchema {
        min: u32,
        max: u32,
        target: u32,
    },
    InvalidFormat(String),
    InvalidStateTransition {
        from: UpdateTransactionState,
        to: UpdateTransactionState,
    },
    Storage(String),
    TrialFailed(String),
    TrialExhausted,
    TrialDeadlineExceeded,
    HealthCheckFailed(String),
}

impl UpdateEngineError {
    pub fn to_error_code(&self) -> ErrorCode {
        match self {
            Self::Busy => ErrorCode::UpdateBusy,
            Self::Oversized { .. } => ErrorCode::UpdateOversized,
            Self::Truncated(_) => ErrorCode::UpdateTruncated,
            Self::CorruptedArtifact { .. } => ErrorCode::UpdateCorruptedArtifact,
            Self::IncompatibleHardware { .. } => ErrorCode::UpdateIncompatibleHardware,
            Self::IncompatibleSchema { .. } => ErrorCode::UpdateIncompatibleSchema,
            Self::InvalidFormat(_) => ErrorCode::UpdateInvalidFormat,
            Self::InvalidStateTransition { .. } => ErrorCode::CommandUnsupported,
            Self::Storage(_) => ErrorCode::DataUnavailable,
            Self::TrialFailed(_) => ErrorCode::UpdateTrialFailed,
            Self::TrialExhausted => ErrorCode::UpdateTrialExhausted,
            Self::TrialDeadlineExceeded => ErrorCode::UpdateTrialDeadlineExceeded,
            Self::HealthCheckFailed(_) => ErrorCode::UpdateHealthCheckFailed,
        }
    }

    pub fn error_code_str(&self) -> &'static str {
        self.to_error_code().as_str()
    }
}

impl fmt::Display for UpdateEngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Busy => f.write_str("update transaction already in progress (update_busy)"),
            Self::Oversized { size, limit } => {
                write!(f, "update payload {size} bytes exceeds staging reservation {limit} bytes (update_oversized)")
            }
            Self::Truncated(msg) => {
                write!(f, "update payload is truncated: {msg} (update_truncated)")
            }
            Self::CorruptedArtifact {
                file,
                expected,
                actual,
            } => {
                write!(f, "artifact '{file}' checksum mismatch: expected {expected}, actual {actual} (update_corrupted_artifact)")
            }
            Self::IncompatibleHardware { declared, target } => {
                write!(f, "target hardware '{target}' not in declared compatibility list {declared:?} (update_incompatible_hardware)")
            }
            Self::IncompatibleSchema { min, max, target } => {
                write!(f, "schema version {target} outside supported range {min}..{max} (update_incompatible_schema)")
            }
            Self::InvalidFormat(msg) => write!(
                f,
                "invalid update container format: {msg} (update_invalid_format)"
            ),
            Self::InvalidStateTransition { from, to } => {
                write!(f, "cannot transition from state {from:?} to {to:?}")
            }
            Self::Storage(msg) => write!(f, "storage error: {msg}"),
            Self::TrialFailed(msg) => write!(f, "trial boot failed: {msg} (update_trial_failed)"),
            Self::TrialExhausted => f.write_str(
                "trial attempts exhausted; update failed and staying on previous slot (update_trial_exhausted)",
            ),
            Self::TrialDeadlineExceeded => f.write_str(
                "trial deadline of 120 seconds exceeded without commit (update_trial_deadline_exceeded)",
            ),
            Self::HealthCheckFailed(msg) => {
                write!(f, "local health check failed: {msg} (update_health_check_failed)")
            }
        }
    }
}

impl std::error::Error for UpdateEngineError {}

/// Main update engine coordinating staging, validation, and durable state tracking.
pub struct UpdateEngine<B: SlotBackend, S: StagingStorage, J: JournalStorage> {
    pub backend: B,
    pub storage: S,
    pub journal: JournalController<J>,
    pub hardware_model: String,
    pub schema_version: u32,
    pub reserved_capacity: u64,
}

impl<B: SlotBackend, S: StagingStorage, J: JournalStorage> UpdateEngine<B, S, J> {
    /// Initializes or loads an update engine instance.
    pub fn new(
        backend: B,
        mut storage: S,
        journal_storage: J,
        hardware_model: Option<String>,
        schema_version: Option<u32>,
        capacity: Option<u64>,
    ) -> Result<Self, UpdateEngineError> {
        let cap = capacity.unwrap_or(DEFAULT_STAGING_RESERVATION);
        let hw = hardware_model.unwrap_or_else(|| DEFAULT_HARDWARE_MODEL.to_string());
        let schema = schema_version.unwrap_or(DEFAULT_SCHEMA_VERSION);

        let active = backend
            .get_active_slot()
            .map_err(UpdateEngineError::Storage)?;
        let default = backend
            .get_default_slot()
            .map_err(UpdateEngineError::Storage)?;

        let storage_init = storage
            .init_staging(cap)
            .map_err(UpdateEngineError::Storage);
        storage_init?;

        let journal =
            JournalController::load_or_init(journal_storage, active.name(), default.name())
                .map_err(UpdateEngineError::Storage)?;

        Ok(Self {
            backend,
            storage,
            journal,
            hardware_model: hw,
            schema_version: schema,
            reserved_capacity: cap,
        })
    }

    /// Reconciles unfinalized boot/recovery state (e.g. interrupted upload while Receiving).
    /// Called explicitly on real boot (by `nx-sysd update init-staging` / `nextoomio-update-init.service`).
    pub fn reconcile_boot(&mut self) -> Result<ReconcileOutcome, UpdateEngineError> {
        let running_slot = self
            .backend
            .get_active_slot()
            .map_err(UpdateEngineError::Storage)?;
        let outcome = self
            .journal
            .reconcile_boot(running_slot.name())
            .map_err(UpdateEngineError::Storage)?;

        if outcome == ReconcileOutcome::SecondTrialRequested {
            if let Some(target) = self
                .journal
                .current()
                .target_slot
                .as_deref()
                .and_then(Slot::parse)
            {
                let _ = self.backend.arm_trial_boot(target);
            }
        }
        Ok(outcome)
    }

    /// Queries the current update transaction status.
    /// Reports the live slot configuration directly from the backend (AC2).
    pub fn status(&self) -> UpdateStatus {
        let rec = self.journal.current();
        let active_slot = self
            .backend
            .get_active_slot()
            .map(|s| s.name().to_string())
            .unwrap_or_else(|_| rec.active_slot.clone());
        let default_slot = self
            .backend
            .get_default_slot()
            .map(|s| s.name().to_string())
            .unwrap_or_else(|_| rec.default_slot.clone());

        UpdateStatus {
            state: rec.state,
            active_slot,
            default_slot,
            target_slot: rec.target_slot.clone(),
            tx_id: rec.tx_id.clone(),
            payload_length: rec.payload_len,
            reserved_capacity: self.reserved_capacity,
            consumed_attempts: rec.consumed_attempts,
            error_code: rec.error_code.clone(),
            message: None,
        }
    }

    /// Begins an update upload.
    /// Refuses with UpdateBusy if another transaction is currently receiving or installing (AC5).
    pub fn begin_upload(&mut self, declared_len: Option<u64>) -> Result<(), UpdateEngineError> {
        let curr = self.journal.current().state;

        // AC5: Given an upload in progress, when a second upload or an install is requested, then it is refused as busy.
        if curr == UpdateTransactionState::Receiving || curr == UpdateTransactionState::Installing {
            return Err(UpdateEngineError::Busy);
        }

        // Strict state transitions: an upload can start from Idle, or from Failed (AC4 retry).
        // Transition from Staged or Validated without abort is rejected.
        if curr != UpdateTransactionState::Idle && curr != UpdateTransactionState::Failed {
            return Err(UpdateEngineError::InvalidStateTransition {
                from: curr,
                to: UpdateTransactionState::Receiving,
            });
        }

        // AC3: Check declared reservation limit upfront if provided
        if let Some(decl) = declared_len {
            if decl > self.reserved_capacity {
                let err = UpdateEngineError::Oversized {
                    size: decl,
                    limit: self.reserved_capacity,
                };
                let _ = self.journal.commit_state(
                    UpdateTransactionState::Failed,
                    None,
                    None,
                    decl,
                    None,
                    Some(err.error_code_str().to_string()),
                );
                return Err(err);
            }
        }

        self.journal
            .commit_state(UpdateTransactionState::Receiving, None, None, 0, None, None)
            .map_err(UpdateEngineError::Storage)?;

        Ok(())
    }

    /// Requests starting an installation. Refuses with UpdateBusy if another transaction is currently receiving or installing (AC5).
    pub fn begin_install(&mut self) -> Result<(), UpdateEngineError> {
        let curr = self.journal.current().state;
        if curr == UpdateTransactionState::Receiving || curr == UpdateTransactionState::Installing {
            return Err(UpdateEngineError::Busy);
        }
        if curr != UpdateTransactionState::Validated {
            return Err(UpdateEngineError::InvalidStateTransition {
                from: curr,
                to: UpdateTransactionState::Installing,
            });
        }
        Ok(())
    }

    /// Installs a validated update image into the inactive system slot (AC1..AC5, SYS-05, UPD-05).
    ///
    /// 1. Verifies state is Validated (AC3) and not busy (AC5).
    /// 2. Commits 'installing' state to the durable journal (AC5, AR-01).
    /// 3. Computes active slot partition checksums before writing (AC1).
    /// 4. Streams boot and root artifacts from staged container into ONLY the inactive slot partitions (AC1).
    /// 5. Flushes and syncs partition writes.
    /// 6. Verifies active slot partition checksums are identical before and after (AC1).
    /// 7. Reads back inactive partitions and verifies written artifact SHA-256 checksums (AC2).
    /// 8. Copies running sound-card selection (nextoomio-hardware.txt) into the newly written boot slot (AC4).
    /// 9. Commits 'installed' state only upon complete verification (AC2).
    /// 10. Resets staging payload metadata while retaining preallocated extents.
    pub fn install(&mut self) -> Result<UpdateStatus, UpdateEngineError> {
        self.begin_install()?;

        let (tx_id, payload_len, payload_sha) = {
            let curr = self.journal.current();
            (
                curr.tx_id.clone(),
                curr.payload_len,
                curr.payload_sha256.clone(),
            )
        };

        let active_slot = self
            .backend
            .get_active_slot()
            .map_err(UpdateEngineError::Storage)?;
        let target_slot = active_slot.inactive();

        // 1. Commit state: Installing (AC5, AR-01, section 3.6 step 3)
        // If interrupted by power loss or reboot during install, reconcile_boot transitions to Failed.
        self.journal
            .commit_state(
                UpdateTransactionState::Installing,
                tx_id.clone(),
                Some(target_slot.name().to_string()),
                payload_len,
                payload_sha.clone(),
                None,
            )
            .map_err(UpdateEngineError::Storage)?;

        if let Err(e) =
            self.execute_install(active_slot, target_slot, &tx_id, payload_len, &payload_sha)
        {
            let _ = self.fail_install(&e);
            return Err(e);
        }

        Ok(self.status())
    }

    fn execute_install(
        &mut self,
        active_slot: Slot,
        target_slot: Slot,
        tx_id: &Option<String>,
        payload_len: u64,
        payload_sha: &Option<String>,
    ) -> Result<(), UpdateEngineError> {
        // Step 1: Pre-install active slot partition checksums (AC1)
        let active_boot_before = self
            .backend
            .checksum_partition(active_slot, PartitionType::Boot)
            .map_err(UpdateEngineError::Storage)?;
        let active_root_before = self
            .backend
            .checksum_partition(active_slot, PartitionType::Root)
            .map_err(UpdateEngineError::Storage)?;

        // Step 2: Open staged payload reader and parse sw-description
        let mut reader = self
            .storage
            .open_payload_reader()
            .map_err(UpdateEngineError::Storage)?;

        let first_header = match read_next_cpio_header(&mut reader) {
            Ok(Some(h)) => h,
            Ok(None) | Err(CpioError::Truncated) => {
                return Err(UpdateEngineError::Truncated(
                    "CPIO stream ended prematurely".to_string(),
                ));
            }
            Err(e) => return Err(UpdateEngineError::InvalidFormat(e.to_string())),
        };

        if first_header.name != "sw-description" {
            return Err(UpdateEngineError::InvalidFormat(format!(
                "first entry must be 'sw-description', found '{}'",
                first_header.name
            )));
        }

        let desc_bytes = read_entry_bytes(&mut reader, first_header.filesize, 10 * 1024 * 1024)
            .map_err(|e| UpdateEngineError::InvalidFormat(e.to_string()))?;
        let desc_text = std::str::from_utf8(&desc_bytes).map_err(|_| {
            UpdateEngineError::InvalidFormat("sw-description is not valid UTF-8".to_string())
        })?;
        let sw_desc = parse_sw_description(desc_text).map_err(|e| {
            UpdateEngineError::InvalidFormat(format!("malformed sw-description: {e}"))
        })?;

        let target_boot_device = target_slot.boot_device();
        let target_root_device = target_slot.root_device();

        let boot_decl = sw_desc
            .images
            .iter()
            .find(|img| img.device == target_boot_device)
            .cloned()
            .ok_or_else(|| {
                UpdateEngineError::InvalidFormat(format!(
                    "missing boot artifact declared for target device '{target_boot_device}' in sw-description"
                ))
            })?;

        let root_decl = sw_desc
            .images
            .iter()
            .find(|img| img.device == target_root_device)
            .cloned()
            .ok_or_else(|| {
                UpdateEngineError::InvalidFormat(format!(
                    "missing root artifact declared for target device '{target_root_device}' in sw-description"
                ))
            })?;

        // Step 3: Stream entries and write ONLY inactive slot partitions (AC1)
        let mut wrote_boot = false;
        let mut wrote_root = false;
        let mut boot_artifact_size = 0u64;
        let mut root_artifact_size = 0u64;

        loop {
            let header = match read_next_cpio_header(&mut reader) {
                Ok(Some(h)) => h,
                Ok(None) => break,
                Err(CpioError::Truncated) => {
                    return Err(UpdateEngineError::Truncated(
                        "CPIO stream ended prematurely during install".to_string(),
                    ));
                }
                Err(e) => return Err(UpdateEngineError::InvalidFormat(e.to_string())),
            };

            let target_part = if header.name == boot_decl.filename && !wrote_boot {
                Some(PartitionType::Boot)
            } else if header.name == root_decl.filename && !wrote_root {
                Some(PartitionType::Root)
            } else {
                None
            };

            if let Some(part) = target_part {
                let mut entry_reader = (&mut reader).take(header.filesize);

                self.backend
                    .write_partition(target_slot, part, &mut entry_reader, header.filesize)
                    .map_err(UpdateEngineError::Storage)?;

                match part {
                    PartitionType::Boot => {
                        boot_artifact_size = header.filesize;
                        wrote_boot = true;
                    }
                    PartitionType::Root => {
                        root_artifact_size = header.filesize;
                        wrote_root = true;
                    }
                }

                let pad = (4 - (header.filesize % 4)) % 4;
                if pad > 0 {
                    let mut p = [0u8; 4];
                    reader.read_exact(&mut p[..pad as usize]).map_err(|e| {
                        UpdateEngineError::InvalidFormat(format!(
                            "failed to read CPIO padding after {}: {e}",
                            header.name
                        ))
                    })?;
                }
            } else {
                skip_entry_data(&mut reader, header.filesize)
                    .map_err(|e| UpdateEngineError::InvalidFormat(e.to_string()))?;
            }
        }

        if !wrote_boot || !wrote_root {
            return Err(UpdateEngineError::InvalidFormat(
                "declared boot or root artifact missing from container".to_string(),
            ));
        }

        // Flush all writes before verification (ARCHITECTURE.md section 3.6 step 3)
        self.backend
            .sync_partitions()
            .map_err(UpdateEngineError::Storage)?;

        // Step 4: Verify active slot checksums are identical before and after (AC1)
        let active_boot_after = self
            .backend
            .checksum_partition(active_slot, PartitionType::Boot)
            .map_err(UpdateEngineError::Storage)?;
        let active_root_after = self
            .backend
            .checksum_partition(active_slot, PartitionType::Root)
            .map_err(UpdateEngineError::Storage)?;

        if active_boot_before != active_boot_after || active_root_before != active_root_after {
            return Err(UpdateEngineError::Storage(
                "active slot partition checksum changed during installation".to_string(),
            ));
        }

        // Step 5: Read back written artifacts from inactive slot and compare checksums (AC2)
        let boot_readback_sha256 = self
            .backend
            .read_partition_checksum(target_slot, PartitionType::Boot, boot_artifact_size)
            .map_err(UpdateEngineError::Storage)?;

        if boot_readback_sha256.to_lowercase() != boot_decl.sha256.to_lowercase() {
            return Err(UpdateEngineError::CorruptedArtifact {
                file: boot_decl.filename.clone(),
                expected: boot_decl.sha256.clone(),
                actual: boot_readback_sha256,
            });
        }

        let root_readback_sha256 = self
            .backend
            .read_partition_checksum(target_slot, PartitionType::Root, root_artifact_size)
            .map_err(UpdateEngineError::Storage)?;

        if root_readback_sha256.to_lowercase() != root_decl.sha256.to_lowercase() {
            return Err(UpdateEngineError::CorruptedArtifact {
                file: root_decl.filename.clone(),
                expected: root_decl.sha256.clone(),
                actual: root_readback_sha256,
            });
        }

        // Step 6: Copy sound-card selection of running slot into newly written boot slot (AC4)
        if let Some(sound_card_cfg) = self
            .backend
            .get_active_sound_card_selection()
            .map_err(UpdateEngineError::Storage)?
        {
            self.backend
                .set_sound_card_selection(target_slot, &sound_card_cfg)
                .map_err(UpdateEngineError::Storage)?;

            self.backend
                .sync_partitions()
                .map_err(UpdateEngineError::Storage)?;

            let written_cfg = self
                .backend
                .get_sound_card_selection(target_slot)
                .map_err(UpdateEngineError::Storage)?;

            if written_cfg.as_deref() != Some(sound_card_cfg.as_str()) {
                return Err(UpdateEngineError::Storage(
                    "sound-card selection verification failed on newly written boot slot"
                        .to_string(),
                ));
            }
        }

        // Step 7: Persist 'installed' state (AC2)
        self.journal
            .commit_state(
                UpdateTransactionState::Installed,
                tx_id.clone(),
                Some(target_slot.name().to_string()),
                payload_len,
                payload_sha.clone(),
                None,
            )
            .map_err(UpdateEngineError::Storage)?;

        // Cleanup staging payload metadata while retaining preallocated reservation
        let _ = self.storage.cleanup();

        Ok(())
    }

    fn fail_install(&mut self, error: &UpdateEngineError) -> Result<(), UpdateEngineError> {
        let code = error.error_code_str().to_string();
        let payload_len = self.journal.current().payload_len;

        self.journal
            .commit_state(
                UpdateTransactionState::Failed,
                None,
                None,
                payload_len,
                None,
                Some(code),
            )
            .map_err(UpdateEngineError::Storage)?;

        let _ = self.storage.cleanup();
        Ok(())
    }

    /// Arms one-shot trial boot of the installed target slot (UPD-06, AR-01, AC1, AC4, AC5).
    ///
    /// 1. Verifies precondition: transaction state must be Installed (or Trial for attempt 2).
    /// 2. Verifies consumed attempts < 2 (refuses with TrialExhausted if >= 2, AC4).
    /// 3. Increments attempt counter: 0 -> 1 on attempt 1, 1 -> 2 on attempt 2.
    /// 4. Durably commits attempt count and state Trial to alternating journal before reboot (AC1, AC5).
    /// 5. Arms the one-shot tryboot selector for target slot, keeping previous slot default (AC1, AD-14).
    /// 6. Triggers reboot if auto_reboot is true.
    pub fn request_trial(
        &mut self,
        _remote_confirm: bool,
        auto_reboot: bool,
    ) -> Result<UpdateStatus, UpdateEngineError> {
        let (current_state, attempts, tx_id, target_slot, default_slot, payload_len, payload_sha) = {
            let curr = self.journal.current();
            (
                curr.state,
                curr.consumed_attempts,
                curr.tx_id.clone(),
                curr.target_slot.clone(),
                curr.default_slot.clone(),
                curr.payload_len,
                curr.payload_sha256.clone(),
            )
        };

        if current_state != UpdateTransactionState::Installed
            && current_state != UpdateTransactionState::Trial
        {
            return Err(UpdateEngineError::InvalidStateTransition {
                from: current_state,
                to: UpdateTransactionState::Trial,
            });
        }

        if attempts >= 2 {
            return Err(UpdateEngineError::TrialExhausted);
        }

        let target_str = target_slot.ok_or_else(|| {
            UpdateEngineError::InvalidFormat("no target slot recorded for trial boot".to_string())
        })?;
        let target = Slot::parse(&target_str).ok_or_else(|| {
            UpdateEngineError::InvalidFormat(format!("invalid target slot: {target_str}"))
        })?;

        let default_slot_parsed = Slot::parse(&default_slot).unwrap_or(Slot::A);
        if target == default_slot_parsed {
            return Err(UpdateEngineError::InvalidFormat(format!(
                "target slot {target} matches default slot {default_slot_parsed}"
            )));
        }

        let new_attempts = attempts + 1;

        // AC1 & AC5: Store attempt count durably in journal before reboot
        self.journal
            .commit_state_with_attempts(
                UpdateTransactionState::Trial,
                tx_id,
                Some(target_str),
                payload_len,
                payload_sha,
                None,
                new_attempts,
            )
            .map_err(UpdateEngineError::Storage)?;

        // Arm one-shot tryboot selector (AC1, AD-14)
        self.backend
            .arm_trial_boot(target)
            .map_err(UpdateEngineError::Storage)?;

        if auto_reboot {
            self.backend
                .trigger_reboot()
                .map_err(UpdateEngineError::Storage)?;
        }

        Ok(self.status())
    }

    /// Evaluates local trial boot health check conditions (AC2, AC7).
    pub fn check_health(&self) -> Result<HealthReport, UpdateEngineError> {
        self.backend
            .check_health()
            .map_err(UpdateEngineError::Storage)
    }

    /// Enforces the 120-second trial deadline from trial-slot handoff (UPD-06, AR-01, AC3).
    /// Runs independently of the services being checked.
    pub fn run_trial_watchdog(
        &mut self,
        deadline_secs: u64,
        poll_interval_ms: u64,
    ) -> Result<(), UpdateEngineError> {
        let (state, target_slot) = {
            let curr = self.journal.current();
            (curr.state, curr.target_slot.clone())
        };

        if state != UpdateTransactionState::Trial {
            return Ok(());
        }

        let active_slot = self
            .backend
            .get_active_slot()
            .map_err(UpdateEngineError::Storage)?;

        if target_slot.as_deref() != Some(active_slot.name()) {
            return Ok(());
        }

        let get_uptime_secs = || -> f64 {
            if let Ok(val) = std::env::var("NEXTOOMIO_TEST_UPTIME_SECS") {
                if let Ok(s) = val.parse::<f64>() {
                    return s;
                }
            }
            if let Ok(content) = std::fs::read_to_string("/proc/uptime") {
                if let Some(first) = content.split_whitespace().next() {
                    if let Ok(s) = first.parse::<f64>() {
                        return s;
                    }
                }
            }
            0.0
        };

        let start_monotonic = std::time::Instant::now();
        let initial_uptime = get_uptime_secs();

        loop {
            // Check if transaction was committed
            if self.journal.current().state == UpdateTransactionState::Committed {
                return Ok(());
            }

            // Carry boot budget from kernel/systemd uptime
            let elapsed_uptime = initial_uptime + start_monotonic.elapsed().as_secs_f64();
            if elapsed_uptime >= deadline_secs as f64 {
                // 120s deadline exceeded without commit (AC3): return to previous slot
                let _ = self.backend.trigger_reboot();
                return Err(UpdateEngineError::TrialDeadlineExceeded);
            }

            std::thread::sleep(std::time::Duration::from_millis(poll_interval_ms.max(50)));
        }
    }

    /// Stages payload from an input reader into the preallocated staging file.
    /// Streams reader directly into disk storage while computing SHA-256 without memory buffering.
    pub fn stage_upload<R: Read>(
        &mut self,
        reader: R,
        declared_len: Option<u64>,
    ) -> Result<UpdateStatus, UpdateEngineError> {
        self.begin_upload(declared_len)?;

        struct HashingReader<R: Read> {
            inner: R,
            hasher: Sha256,
        }

        impl<R: Read> Read for HashingReader<R> {
            fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
                let n = self.inner.read(buf)?;
                if n > 0 {
                    self.hasher.update(&buf[..n]);
                }
                Ok(n)
            }
        }

        let mut hashing_reader = HashingReader {
            inner: reader,
            hasher: Sha256::new(),
        };

        let total_written = match self
            .storage
            .write_payload(&mut hashing_reader, declared_len)
        {
            Ok(bytes) => bytes,
            Err(StagingError::Oversized { attempted, limit }) => {
                let err = UpdateEngineError::Oversized {
                    size: attempted,
                    limit,
                };
                let _ = self.journal.commit_state(
                    UpdateTransactionState::Failed,
                    None,
                    None,
                    attempted,
                    None,
                    Some(err.error_code_str().to_string()),
                );
                return Err(err);
            }
            Err(StagingError::Stream(msg)) => {
                let err = UpdateEngineError::Truncated(msg);
                let _ = self.journal.commit_state(
                    UpdateTransactionState::Failed,
                    None,
                    None,
                    0,
                    None,
                    Some(err.error_code_str().to_string()),
                );
                return Err(err);
            }
            Err(StagingError::Storage(msg)) => {
                let err = UpdateEngineError::Storage(msg);
                let _ = self.journal.commit_state(
                    UpdateTransactionState::Failed,
                    None,
                    None,
                    0,
                    None,
                    Some(err.error_code_str().to_string()),
                );
                return Err(err);
            }
        };

        // If declared length was provided, verify actual received bytes match exactly
        if let Some(decl) = declared_len {
            if total_written != decl {
                let err = UpdateEngineError::Truncated(format!(
                    "upload stream ended after {total_written} bytes, expected {decl} bytes"
                ));
                let _ = self.journal.commit_state(
                    UpdateTransactionState::Failed,
                    None,
                    None,
                    total_written,
                    None,
                    Some(err.error_code_str().to_string()),
                );
                return Err(err);
            }
        }

        let sha256_hex = hashing_reader.hasher.hex_digest();

        // Transition Receiving -> Staged
        self.journal
            .commit_state(
                UpdateTransactionState::Staged,
                None,
                None,
                total_written,
                Some(sha256_hex),
                None,
            )
            .map_err(UpdateEngineError::Storage)?;

        Ok(self.status())
    }

    /// Validates the staged update container (AC2, AC3).
    ///
    /// Validates CPIO container structure, sw-description existence as first file,
    /// hardware model compatibility, configuration schema range, and artifact SHA-256 integrity.
    ///
    /// Streams entries incrementally to prevent memory exhaustion on target hardware.
    /// Preserves active and default boot slots and performs zero writes to the inactive slot.
    pub fn validate(&mut self) -> Result<UpdateStatus, UpdateEngineError> {
        let (curr_state, payload_len, curr_sha) = {
            let curr = self.journal.current();
            (curr.state, curr.payload_len, curr.payload_sha256.clone())
        };
        if curr_state != UpdateTransactionState::Staged {
            return Err(UpdateEngineError::InvalidStateTransition {
                from: curr_state,
                to: UpdateTransactionState::Validated,
            });
        }

        if payload_len == 0 {
            let err = UpdateEngineError::Truncated("empty payload".to_string());
            self.fail_validation(&err)?;
            return Err(err);
        }

        if payload_len > self.reserved_capacity {
            let err = UpdateEngineError::Oversized {
                size: payload_len,
                limit: self.reserved_capacity,
            };
            self.fail_validation(&err)?;
            return Err(err);
        }

        let mut reader = match self.storage.open_payload_reader() {
            Ok(r) => r,
            Err(e) => {
                let err = UpdateEngineError::Storage(e);
                self.fail_validation(&err)?;
                return Err(err);
            }
        };

        // 1. First file must be sw-description
        let first_header = match read_next_cpio_header(&mut reader) {
            Ok(Some(h)) => h,
            Ok(None) | Err(CpioError::Truncated) => {
                let err = UpdateEngineError::Truncated("CPIO stream ended prematurely".to_string());
                self.fail_validation(&err)?;
                return Err(err);
            }
            Err(CpioError::InvalidMagic) => {
                let err = UpdateEngineError::InvalidFormat("invalid CPIO magic".to_string());
                self.fail_validation(&err)?;
                return Err(err);
            }
            Err(e) => {
                let err = UpdateEngineError::InvalidFormat(e.to_string());
                self.fail_validation(&err)?;
                return Err(err);
            }
        };

        if first_header.name != "sw-description" {
            let err = UpdateEngineError::InvalidFormat(format!(
                "first entry in update container must be 'sw-description', found '{}'",
                first_header.name
            ));
            self.fail_validation(&err)?;
            return Err(err);
        }

        // sw-description is loaded into memory (max 10 MiB limit to prevent memory abuse)
        let desc_bytes =
            match read_entry_bytes(&mut reader, first_header.filesize, 10 * 1024 * 1024) {
                Ok(b) => b,
                Err(CpioError::Truncated) => {
                    let err = UpdateEngineError::Truncated("sw-description truncated".to_string());
                    self.fail_validation(&err)?;
                    return Err(err);
                }
                Err(e) => {
                    let err = UpdateEngineError::InvalidFormat(e.to_string());
                    self.fail_validation(&err)?;
                    return Err(err);
                }
            };

        let desc_text = match std::str::from_utf8(&desc_bytes) {
            Ok(s) => s,
            Err(_) => {
                let err = UpdateEngineError::InvalidFormat(
                    "sw-description is not valid UTF-8".to_string(),
                );
                self.fail_validation(&err)?;
                return Err(err);
            }
        };

        // 2. Parse sw-description
        let sw_desc = match parse_sw_description(desc_text) {
            Ok(d) => d,
            Err(e) => {
                let err =
                    UpdateEngineError::InvalidFormat(format!("malformed sw-description: {e}"));
                self.fail_validation(&err)?;
                return Err(err);
            }
        };

        // 3. Hardware compatibility check (AC3)
        if !sw_desc.is_hardware_compatible(&self.hardware_model) {
            let err = UpdateEngineError::IncompatibleHardware {
                declared: sw_desc.hardware_compatibility.clone(),
                target: self.hardware_model.clone(),
            };
            self.fail_validation(&err)?;
            return Err(err);
        }

        // 4. Schema range check
        if !sw_desc.is_schema_compatible(self.schema_version) {
            let err = UpdateEngineError::IncompatibleSchema {
                min: sw_desc.config_schema_min,
                max: sw_desc.config_schema_max,
                target: self.schema_version,
            };
            self.fail_validation(&err)?;
            return Err(err);
        }

        // 5. Artifact presence and streaming SHA-256 verification (AC2, AC3)
        if sw_desc.images.is_empty() {
            let err =
                UpdateEngineError::InvalidFormat("sw-description declares zero images".to_string());
            self.fail_validation(&err)?;
            return Err(err);
        }

        let mut verified_images = HashSet::new();

        loop {
            let header = match read_next_cpio_header(&mut reader) {
                Ok(Some(h)) => h,
                Ok(None) => break, // Reached TRAILER!!! or clean EOF
                Err(CpioError::Truncated) => {
                    let err =
                        UpdateEngineError::Truncated("CPIO stream ended prematurely".to_string());
                    self.fail_validation(&err)?;
                    return Err(err);
                }
                Err(CpioError::InvalidMagic) => {
                    let err = UpdateEngineError::InvalidFormat("invalid CPIO magic".to_string());
                    self.fail_validation(&err)?;
                    return Err(err);
                }
                Err(e) => {
                    let err = UpdateEngineError::InvalidFormat(e.to_string());
                    self.fail_validation(&err)?;
                    return Err(err);
                }
            };

            if let Some(expected_image) = sw_desc
                .images
                .iter()
                .find(|img| img.filename == header.name)
            {
                let mut hasher = Sha256::new();
                if let Err(e) = stream_and_hash_entry_data(&mut reader, header.filesize, |chunk| {
                    hasher.update(chunk);
                }) {
                    let err = if e == CpioError::Truncated {
                        UpdateEngineError::Truncated(format!(
                            "artifact '{}' truncated",
                            header.name
                        ))
                    } else {
                        UpdateEngineError::InvalidFormat(e.to_string())
                    };
                    self.fail_validation(&err)?;
                    return Err(err);
                }

                let actual_sha256 = hasher.hex_digest();
                let expected_sha256 = expected_image.sha256.to_lowercase();

                if actual_sha256 != expected_sha256 {
                    let err = UpdateEngineError::CorruptedArtifact {
                        file: header.name,
                        expected: expected_sha256,
                        actual: actual_sha256,
                    };
                    self.fail_validation(&err)?;
                    return Err(err);
                }

                verified_images.insert(header.name);
            } else {
                if let Err(e) = skip_entry_data(&mut reader, header.filesize) {
                    let err = if e == CpioError::Truncated {
                        UpdateEngineError::Truncated("entry truncated".to_string())
                    } else {
                        UpdateEngineError::InvalidFormat(e.to_string())
                    };
                    self.fail_validation(&err)?;
                    return Err(err);
                }
            }
        }

        // Verify every declared artifact was present in the CPIO container
        for image in &sw_desc.images {
            if !verified_images.contains(&image.filename) {
                let err = UpdateEngineError::InvalidFormat(format!(
                    "declared artifact '{}' missing from CPIO container",
                    image.filename
                ));
                self.fail_validation(&err)?;
                return Err(err);
            }
        }

        // All checks passed!
        let tx_sha = curr_sha.unwrap_or_else(|| Sha256::hex(b"tx"));
        let tx_id = format!(
            "tx-{}-{}",
            &tx_sha[..12],
            self.journal.current().generation + 1
        );

        let active_slot = self
            .backend
            .get_active_slot()
            .map_err(UpdateEngineError::Storage)?;
        let target_slot = active_slot.inactive();

        self.journal
            .commit_state(
                UpdateTransactionState::Validated,
                Some(tx_id),
                Some(target_slot.name().to_string()),
                payload_len,
                Some(tx_sha),
                None,
            )
            .map_err(UpdateEngineError::Storage)?;

        Ok(self.status())
    }

    fn fail_validation(&mut self, error: &UpdateEngineError) -> Result<(), UpdateEngineError> {
        let code = error.error_code_str().to_string();
        let payload_len = self.journal.current().payload_len;

        // Transition to Failed with distinct machine-readable error code
        self.journal
            .commit_state(
                UpdateTransactionState::Failed,
                None,
                None,
                payload_len,
                None,
                Some(code),
            )
            .map_err(UpdateEngineError::Storage)?;

        // Staging cleanup resets payload metadata without unlinking or shrinking the reservation
        self.storage.cleanup().map_err(UpdateEngineError::Storage)?;

        Ok(())
    }

    /// Aborts or resets any active/failed update transaction to Idle without reducing reservation.
    pub fn abort(&mut self) -> Result<UpdateStatus, UpdateEngineError> {
        self.journal
            .commit_state(UpdateTransactionState::Idle, None, None, 0, None, None)
            .map_err(UpdateEngineError::Storage)?;

        self.storage.cleanup().map_err(UpdateEngineError::Storage)?;

        Ok(self.status())
    }
}
