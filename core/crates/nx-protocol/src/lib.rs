//! Shared domain model and protocol invariants for `nextoomio`.
//!
//! Implements the core data contracts defined in `SPECIFICATION.md` and `ARCHITECTURE.md`:
//! - Restart-safe `SessionId` (`device_id` + 128-bit `incarnation_id` + monotonic `sequence`)
//! - Playback ownership domains (`Standalone`, `NativeMultiroom`, `MusicAssistant`)
//! - Reference volume calculation (`V = G * R / 100`) and cubic gain mapping
//! - Ownership-scoped effective mute (`AR-04`: `local_mute || (native_participation && stored_group_mute)`)
//! - Durable update transaction state machine (`AR-01`, `AR-08`, `UPD-04..UPD-06`)
//! - Redacting secret wrapper (`ARCHITECTURE.md` section 3.7)

use serde::{Deserialize, Serialize};
use std::fmt;

/// Redacting wrapper for sensitive credentials and keys (`ARCHITECTURE.md` section 3.7).
/// Never exposes its inner value in `Debug` or `Display` output.
#[derive(Clone, PartialEq, Eq)]
pub struct RedactedSecret<T>(T);

impl<T> RedactedSecret<T> {
    pub const fn new(secret: T) -> Self {
        Self(secret)
    }

    pub const fn expose(&self) -> &T {
        &self.0
    }
}

impl<T> fmt::Debug for RedactedSecret<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

impl<T> fmt::Display for RedactedSecret<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

/// Standalone music inputs (`IN-01..IN-06`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StandaloneInput {
    Files,
    Nas,
    Radio,
    Spotify,
    Airplay,
    Bluetooth,
}

/// Playback ownership domain (`SEL-06`).
/// At most one active owner exists on a device at any time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "domain", content = "source", rename_all = "snake_case")]
pub enum OwnershipDomain {
    Standalone(StandaloneInput),
    NativeMultiroom { master_id: String },
    MusicAssistant,
}

impl OwnershipDomain {
    /// Returns true if the current owner is participating in native multiroom playback (`AR-04`).
    pub fn is_native_multiroom_participant(&self, is_distributing_master: bool) -> bool {
        match self {
            Self::NativeMultiroom { .. } => true,
            Self::Standalone(_) => is_distributing_master,
            Self::MusicAssistant => false,
        }
    }
}

/// Restart-safe playback session identifier (`SPECIFICATION.md` section 4, `ARCHITECTURE.md` section 3.3).
/// Cannot repeat across process restarts within the same OS boot.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SessionId {
    pub device_id: String,
    pub incarnation_hex: String,
    pub sequence: u64,
}

impl SessionId {
    pub fn new(device_id: impl Into<String>, incarnation_bytes: [u8; 16], sequence: u64) -> Self {
        let mut hex = String::with_capacity(32);
        for b in incarnation_bytes {
            use std::fmt::Write;
            let _ = write!(&mut hex, "{b:02x}");
        }
        Self {
            device_id: device_id.into(),
            incarnation_hex: hex,
            sequence,
        }
    }
}

/// Command scope required on volume, mute, and transport operations (`TRN-05`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandScope {
    Local,
    Group,
}

/// Logical volume percentage (`0..=100`, 1% steps; `SPECIFICATION.md` section 5.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct VolumePercent(u8);

impl VolumePercent {
    pub const SILENT: Self = Self(0);
    pub const DEFAULT_LOCAL: Self = Self(30);
    pub const DEFAULT_REFERENCE: Self = Self(100);

    pub const fn new_clamped(value: u8) -> Self {
        if value > 100 {
            Self(100)
        } else {
            Self(value)
        }
    }

    pub const fn get(self) -> u8 {
        self.0
    }

    /// Computes satellite volume `V = G * R / 100` (`VOL-02`).
    pub const fn from_group_and_reference(group: Self, reference: Self) -> Self {
        let product = (group.0 as u16) * (reference.0 as u16);
        Self(((product + 50) / 100) as u8)
    }

    /// Computes linear amplitude gain using the shared cubic curve (`ARCHITECTURE.md` section 3.2):
    /// `0.0` for `v = 0`, and `(v / 100)^3` otherwise.
    pub fn to_linear_gain(self) -> f32 {
        if self.0 == 0 {
            0.0
        } else {
            let norm = (self.0 as f32) / 100.0;
            norm * norm * norm
        }
    }
}

/// Computes effective stage-1 mute (`TRN-04`, `AR-04`).
/// Stored group mute applies only when the device is currently participating in native multiroom playback.
pub const fn effective_stage1_mute(
    local_mute: bool,
    stored_group_mute: bool,
    participating_in_native_playback: bool,
) -> bool {
    local_mute || (participating_in_native_playback && stored_group_mute)
}

/// Stable machine-readable API / MQTT error codes (`I18N-04`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    SetupRequired,
    SetupAlreadyCompleted,
    Unauthenticated,
    CsrfInvalid,
    CommandUnsupported,
    WaitingForClient,
    SourceUnreachable,
    FormatUnsupported,
    PlaylistInvalid,
    NasUnreachable,
    ClockNotSet,
    DataUnavailable,
    SelfMasterRejected,
    TopologyCycleRejected,
    GroupLevelDisabled,
    UpdateOversized,
    UpdateInvalidFormat,
    UpdateIncompatibleHardware,
    UpdateIncompatibleSchema,
    UpdateBusy,
    UpdateTruncated,
    UpdateCorruptedArtifact,
}

impl ErrorCode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::SetupRequired => "setup_required",
            Self::SetupAlreadyCompleted => "setup_already_completed",
            Self::Unauthenticated => "unauthenticated",
            Self::CsrfInvalid => "csrf_invalid",
            Self::CommandUnsupported => "command_unsupported",
            Self::WaitingForClient => "waiting_for_client",
            Self::SourceUnreachable => "source_unreachable",
            Self::FormatUnsupported => "format_unsupported",
            Self::PlaylistInvalid => "playlist_invalid",
            Self::NasUnreachable => "nas_unreachable",
            Self::ClockNotSet => "clock_not_set",
            Self::DataUnavailable => "data_unavailable",
            Self::SelfMasterRejected => "self_master_rejected",
            Self::TopologyCycleRejected => "topology_cycle_rejected",
            Self::GroupLevelDisabled => "group_level_disabled",
            Self::UpdateOversized => "update_oversized",
            Self::UpdateInvalidFormat => "update_invalid_format",
            Self::UpdateIncompatibleHardware => "update_incompatible_hardware",
            Self::UpdateIncompatibleSchema => "update_incompatible_schema",
            Self::UpdateBusy => "update_busy",
            Self::UpdateTruncated => "update_truncated",
            Self::UpdateCorruptedArtifact => "update_corrupted_artifact",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Durable update transaction states (`ARCHITECTURE.md` section 3.6, `AR-01`, `AR-08`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateTransactionState {
    Idle,
    Receiving,
    Staged,
    Validated,
    Installing,
    Installed,
    Trial,
    Committing,
    Committed,
    Failed,
}

impl UpdateTransactionState {
    /// Checks whether a state transition is valid under the durable updater contract.
    pub const fn can_transition_to(self, next: Self) -> bool {
        use UpdateTransactionState::*;
        matches!(
            (self, next),
            (Idle, Receiving)
                | (Committed, Receiving)
                | (Failed, Receiving)
                | (Receiving, Staged)
                | (Receiving, Failed)
                | (Staged, Validated)
                | (Staged, Failed)
                | (Validated, Installing)
                | (Validated, Failed)
                | (Installing, Installed)
                | (Installing, Failed)
                | (Installed, Trial)
                | (Installed, Failed)
                | (Trial, Trial)
                | (Trial, Committing)
                | (Trial, Failed)
                | (Committing, Committed)
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn volume_formula_and_cubic_gain_follow_specification() {
        let g = VolumePercent::new_clamped(50);
        let r = VolumePercent::new_clamped(60);
        let v = VolumePercent::from_group_and_reference(g, r);
        assert_eq!(v.get(), 30);
        assert_eq!(VolumePercent::SILENT.to_linear_gain(), 0.0);
        assert!((VolumePercent::new_clamped(100).to_linear_gain() - 1.0).abs() < f32::EPSILON);
    }

    #[test]
    fn group_mute_requires_active_native_participation_ar04() {
        // Group-muted satellite switched to Bluetooth or MA with local_mute = false:
        assert!(!effective_stage1_mute(false, true, false));
        // Same satellite participating in native playback inherits group mute:
        assert!(effective_stage1_mute(false, true, true));
        // Local mute stays muted after group unmute:
        assert!(effective_stage1_mute(true, false, true));
    }

    #[test]
    fn redacted_secret_never_leaks_in_debug_or_display() {
        let secret = RedactedSecret::new("super-secret-group-key");
        assert_eq!(format!("{secret:?}"), "[REDACTED]");
        assert_eq!(format!("{secret}"), "[REDACTED]");
        assert_eq!(*secret.expose(), "super-secret-group-key");
    }

    #[test]
    fn update_transaction_forbids_skipping_validation() {
        use UpdateTransactionState::*;
        assert!(Receiving.can_transition_to(Staged));
        assert!(Staged.can_transition_to(Validated));
        assert!(!Staged.can_transition_to(Installing));
        assert!(!Receiving.can_transition_to(Trial));
    }

    #[test]
    fn update_error_codes_serialize_to_snake_case() {
        assert_eq!(ErrorCode::UpdateOversized.as_str(), "update_oversized");
        assert_eq!(
            ErrorCode::UpdateInvalidFormat.as_str(),
            "update_invalid_format"
        );
        assert_eq!(
            ErrorCode::UpdateIncompatibleHardware.as_str(),
            "update_incompatible_hardware"
        );
        assert_eq!(
            ErrorCode::UpdateIncompatibleSchema.as_str(),
            "update_incompatible_schema"
        );
        assert_eq!(ErrorCode::UpdateBusy.as_str(), "update_busy");
        assert_eq!(ErrorCode::UpdateTruncated.as_str(), "update_truncated");
        assert_eq!(
            ErrorCode::UpdateCorruptedArtifact.as_str(),
            "update_corrupted_artifact"
        );

        let json = serde_json::to_string(&ErrorCode::UpdateCorruptedArtifact).unwrap();
        assert_eq!(json, "\"update_corrupted_artifact\"");
    }
}
