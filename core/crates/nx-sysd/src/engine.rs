//! Update engine coordinating staging, validation, state machine, and storage.
//!
//! Enforces UPD-04, UPD-05, AR-01, AR-08, and all acceptance criteria AC1..AC6.

use crate::cpio::{
    read_entry_bytes, read_next_cpio_header, skip_entry_data, stream_and_hash_entry_data, CpioError,
};
use crate::journal::{JournalController, JournalStorage};
use crate::sha256::Sha256;
use crate::sw_description::parse_sw_description;
use crate::system::{SlotBackend, StagingStorage};
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

        // Ensure staging storage is preallocated (AC1, AC6)
        storage
            .init_staging(cap)
            .map_err(UpdateEngineError::Storage)?;

        let mut journal =
            JournalController::load_or_init(journal_storage, active.name(), default.name())
                .map_err(UpdateEngineError::Storage)?;

        // Reconcile interrupted boot (AC4)
        journal
            .reconcile_boot()
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

    /// Queries the current update transaction status.
    pub fn status(&self) -> UpdateStatus {
        let rec = self.journal.current();
        UpdateStatus {
            state: rec.state,
            active_slot: rec.active_slot.clone(),
            default_slot: rec.default_slot.clone(),
            target_slot: rec.target_slot.clone(),
            tx_id: rec.tx_id.clone(),
            payload_length: rec.payload_len,
            reserved_capacity: self.reserved_capacity,
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
        // In Phase 0 story #19, install execution is out of scope (P0-F4.4 / #20).
        Ok(())
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
            Err(e) => {
                let err = if e.contains("exceeds reserved") {
                    UpdateEngineError::Oversized {
                        size: self.reserved_capacity + 1,
                        limit: self.reserved_capacity,
                    }
                } else {
                    UpdateEngineError::Truncated(e)
                };
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

        // Staging cleanup resets payload metadata without unlinking or shrinking the reservation (AC6)
        self.storage.cleanup().map_err(UpdateEngineError::Storage)?;

        Ok(())
    }

    /// Aborts or resets any active/failed update transaction to Idle without reducing reservation (AC6).
    pub fn abort(&mut self) -> Result<UpdateStatus, UpdateEngineError> {
        self.journal
            .commit_state(UpdateTransactionState::Idle, None, None, 0, None, None)
            .map_err(UpdateEngineError::Storage)?;

        self.storage.cleanup().map_err(UpdateEngineError::Storage)?;

        Ok(self.status())
    }
}
