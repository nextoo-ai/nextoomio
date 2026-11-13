//! Deterministic playback-session manager and arbitration rules (`SEL-01..SEL-07`, `MR-05`, `MA-07`, `VOL-01..VOL-05`).

use nx_protocol::{
    effective_stage1_mute, ErrorCode, OwnershipDomain, SessionId, StandaloneInput, VolumePercent,
};

/// Active playback session tracked by `SessionManager`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveSession {
    pub id: SessionId,
    pub owner: OwnershipDomain,
    pub paused: bool,
}

/// Deterministic single-threaded playback session manager (`ARCHITECTURE.md` section 3.3).
#[derive(Debug)]
pub struct SessionManager {
    device_id: String,
    incarnation_bytes: [u8; 16],
    next_sequence: u64,
    active_session: Option<ActiveSession>,
    last_selected_standalone: Option<StandaloneInput>,
    last_accepted_master_session: Option<SessionId>,
    local_volume: VolumePercent,
    reference_volume: VolumePercent,
    group_volume: VolumePercent,
    central_volume_enabled: bool,
    local_mute: bool,
    stored_group_mute: bool,
    has_registered_satellites: bool,
}

impl SessionManager {
    pub fn new(device_id: impl Into<String>, incarnation_bytes: [u8; 16]) -> Self {
        Self {
            device_id: device_id.into(),
            incarnation_bytes,
            next_sequence: 0,
            active_session: None,
            last_selected_standalone: None,
            last_accepted_master_session: None,
            local_volume: VolumePercent::DEFAULT_LOCAL,
            reference_volume: VolumePercent::DEFAULT_REFERENCE,
            group_volume: VolumePercent::DEFAULT_LOCAL,
            central_volume_enabled: false,
            local_mute: false,
            stored_group_mute: false,
            has_registered_satellites: false,
        }
    }

    pub const fn active_session(&self) -> Option<&ActiveSession> {
        self.active_session.as_ref()
    }

    pub const fn local_volume(&self) -> VolumePercent {
        self.local_volume
    }

    /// Effective stage-1 mute (`TRN-04`, `AR-04`).
    pub fn effective_mute(&self) -> bool {
        let participating = self
            .active_session
            .as_ref()
            .map(|s| {
                s.owner
                    .is_native_multiroom_participant(self.has_registered_satellites)
            })
            .unwrap_or(false);
        effective_stage1_mute(self.local_mute, self.stored_group_mute, participating)
    }

    /// Grants a direct playback start on this device (`SEL-01`).
    pub fn request_direct_start(&mut self, owner: OwnershipDomain) -> SessionId {
        self.next_sequence += 1;
        let id = SessionId::new(&self.device_id, self.incarnation_bytes, self.next_sequence);
        if let OwnershipDomain::Standalone(input) = owner {
            self.last_selected_standalone = Some(input);
        }
        if matches!(owner, OwnershipDomain::NativeMultiroom { .. }) && self.central_volume_enabled {
            self.local_volume =
                VolumePercent::from_group_and_reference(self.group_volume, self.reference_volume);
        }
        self.active_session = Some(ActiveSession {
            id: id.clone(),
            owner,
            paused: false,
        });
        id
    }

    /// Handles a native master takeover request (`MR-05`, `SEL-04`).
    /// Exempts devices currently owned by Music Assistant, and ignores already-handled master sessions.
    pub fn request_group_takeover(
        &mut self,
        master_id: impl Into<String>,
        master_session_id: SessionId,
    ) -> Option<SessionId> {
        if let Some(current) = &self.active_session {
            if matches!(current.owner, OwnershipDomain::MusicAssistant) {
                return None;
            }
        }
        if self.last_accepted_master_session.as_ref() == Some(&master_session_id) {
            return None;
        }
        self.last_accepted_master_session = Some(master_session_id);
        Some(self.request_direct_start(OwnershipDomain::NativeMultiroom {
            master_id: master_id.into(),
        }))
    }

    /// Ends the current session if `session_id` matches the active session (`SEL-05`).
    /// Never restores a previous source. Stale session events are ignored.
    pub fn end_session(&mut self, session_id: &SessionId) -> bool {
        if self
            .active_session
            .as_ref()
            .map(|s| &s.id == session_id)
            .unwrap_or(false)
        {
            self.active_session = None;
            true
        } else {
            false
        }
    }

    /// Updates local volume (`VOL-03`). Does not overwrite stored reference volume.
    pub fn set_local_volume(&mut self, volume: VolumePercent) {
        self.local_volume = volume;
    }

    /// Configures group volume state and applies `V = G * R / 100` only if central control is enabled
    /// and the device is currently playing native multiroom (`VOL-02`, `VOL-04`, `VOL-05`).
    pub fn apply_group_volume_snapshot(
        &mut self,
        group_volume: VolumePercent,
        central_enabled: bool,
        group_mute: bool,
    ) {
        self.group_volume = group_volume;
        self.central_volume_enabled = central_enabled;
        self.stored_group_mute = group_mute;

        let is_native_satellite = matches!(
            self.active_session.as_ref().map(|s| &s.owner),
            Some(OwnershipDomain::NativeMultiroom { .. })
        );
        if central_enabled && is_native_satellite {
            self.local_volume =
                VolumePercent::from_group_and_reference(self.group_volume, self.reference_volume);
        }
    }

    pub fn set_reference_volume(&mut self, reference: VolumePercent) {
        self.reference_volume = reference;
    }

    pub fn set_local_mute(&mut self, muted: bool) {
        self.local_mute = muted;
    }

    pub fn reject_self_or_cycle_master(&self, candidate_master_id: &str) -> Result<(), ErrorCode> {
        if candidate_master_id == self.device_id {
            return Err(ErrorCode::SelfMasterRejected);
        }
        if self.has_registered_satellites {
            return Err(ErrorCode::TopologyCycleRejected);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stale_session_end_cannot_stop_newer_session() {
        let mut mgr = SessionManager::new("dev-1", [1u8; 16]);
        let s1 = mgr.request_direct_start(OwnershipDomain::Standalone(StandaloneInput::Radio));
        let s2 = mgr.request_direct_start(OwnershipDomain::Standalone(StandaloneInput::Bluetooth));
        assert!(!mgr.end_session(&s1));
        assert_eq!(mgr.active_session().map(|s| &s.id), Some(&s2));
        assert!(mgr.end_session(&s2));
        assert!(mgr.active_session().is_none());
    }

    #[test]
    fn master_takeover_respects_music_assistant_exemption_mr05() {
        let mut mgr = SessionManager::new("sat-1", [2u8; 16]);
        let ma_session = mgr.request_direct_start(OwnershipDomain::MusicAssistant);
        let master_sid = SessionId::new("master-1", [9u8; 16], 1);
        assert!(mgr.request_group_takeover("master-1", master_sid).is_none());
        assert_eq!(mgr.active_session().map(|s| &s.id), Some(&ma_session));
    }

    #[test]
    fn satellite_entering_native_playback_applies_reference_volume_vol05() {
        let mut mgr = SessionManager::new("sat-1", [3u8; 16]);
        mgr.set_reference_volume(VolumePercent::new_clamped(60));
        mgr.apply_group_volume_snapshot(VolumePercent::new_clamped(50), true, false);
        // Playing Bluetooth locally at 90%:
        mgr.request_direct_start(OwnershipDomain::Standalone(StandaloneInput::Bluetooth));
        mgr.set_local_volume(VolumePercent::new_clamped(90));
        assert_eq!(mgr.local_volume().get(), 90);

        // Taken over by master -> starts native playback at 30% (50 * 60 / 100):
        let master_sid = SessionId::new("master-1", [8u8; 16], 1);
        assert!(mgr.request_group_takeover("master-1", master_sid).is_some());
        assert_eq!(mgr.local_volume().get(), 30);
    }
}
