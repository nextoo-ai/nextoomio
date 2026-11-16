//! Minimal privileged operations helper daemon for nextoomio (`nx-sysd`).
//!
//! Owns update staging, validation, slot writes, hardware profile configuration,
//! NAS mounts, firewall rules, and reboot (AD-20, UPD-04, UPD-05, AR-01, AR-08).

pub mod cpio;
pub mod engine;
pub mod journal;
pub mod sha256;
pub mod sw_description;
pub mod system;

pub use engine::{
    UpdateEngine, UpdateEngineError, UpdateStatus, DEFAULT_HARDWARE_MODEL,
    DEFAULT_STAGING_RESERVATION,
};
pub use journal::{
    FileJournalStorage, JournalController, JournalRecord, JournalStorage, MemoryJournalStorage,
};
pub use system::{
    FileStagingStorage, MockSlotBackend, MockStagingStorage, PhysicalSlotBackend, Slot,
    SlotBackend, StagingStorage,
};

#[cfg(test)]
mod tests {
    use super::*;
    use cpio::create_cpio;
    use nx_protocol::UpdateTransactionState;
    use sha256::Sha256;
    use std::io::{Cursor, Read};

    fn make_valid_swu(
        hardware: &str,
        schema: u32,
        boot_content: &[u8],
        root_content: &[u8],
    ) -> Vec<u8> {
        let boot_sha = Sha256::hex(boot_content);
        let root_sha = Sha256::hex(root_content);

        let sw_desc = format!(
            r#"
software =
{{
    version = "0.0.1-test";
    description = "Test update package";
    hardware-compatibility: [ "{hardware}" ];
    config-schema-range = "{schema}..{schema}";

    stable = {{
        copy1 = {{
            images: (
                {{
                    filename = "nextoomio-boot.vfat.gz";
                    device = "/dev/mmcblk0p2";
                    type = "raw";
                    compressed = "zlib";
                    sha256 = "{boot_sha}";
                    installed-directly = true;
                }},
                {{
                    filename = "nextoomio-image.rootfs.ext4.gz";
                    device = "/dev/mmcblk0p5";
                    type = "raw";
                    compressed = "zlib";
                    sha256 = "{root_sha}";
                    installed-directly = true;
                }}
            );
        }};
    }};
}};
"#
        );

        create_cpio(&[
            ("sw-description", sw_desc.as_bytes()),
            ("nextoomio-boot.vfat.gz", boot_content),
            ("nextoomio-image.rootfs.ext4.gz", root_content),
        ])
    }

    #[test]
    fn test_ac1_first_boot_staging_preallocated_at_full_size_and_full_partition_does_not_prevent_upload(
    ) {
        let capacity = 50 * 1024 * 1024; // 50 MiB simulated staging reservation
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = MockStagingStorage::new(capacity);
        let journal = MemoryJournalStorage::default();

        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .expect("engine initialization failed");

        // 1. Verify staging area is allocated at its full reserved size on first boot (AC1)
        assert_eq!(
            engine.storage.get_allocated_size().unwrap(),
            capacity,
            "staging extent must be preallocated to full capacity"
        );

        // 2. Simulate filling the remainder of the data partition (0 bytes free unreserved)
        engine.storage.fill_unreserved_space(0).unwrap();
        assert_eq!(engine.storage.unreserved_free_bytes, 0);

        // 3. Stage valid update: preallocated extents allow upload to proceed normally without failing out of space (AC1)
        let swu_bytes = make_valid_swu("1.0", 1, b"boot payload data", b"rootfs payload data");
        let status = engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .expect("upload into preallocated staging must succeed even when unreserved data space is exhausted");

        assert_eq!(status.state, UpdateTransactionState::Staged);
        assert_eq!(status.payload_length, swu_bytes.len() as u64);
    }

    #[test]
    fn test_ac2_valid_update_staged_and_validated_active_and_default_slots_unchanged_and_inactive_untouched(
    ) {
        let capacity = 10 * 1024 * 1024;
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = MockStagingStorage::new(capacity);
        let journal = MemoryJournalStorage::default();

        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .expect("engine initialization failed");

        let swu_bytes = make_valid_swu(
            "1.0",
            1,
            b"boot partition content",
            b"root partition content",
        );
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .expect("stage failed");

        // Validate the staged update
        let status = engine.validate().expect("validation should succeed");

        // AC2: update state is validated
        assert_eq!(status.state, UpdateTransactionState::Validated);
        assert!(
            status.tx_id.is_some(),
            "validated transaction must be bound to a transaction ID"
        );
        assert_eq!(status.target_slot, Some("root_b".to_string()));

        // AC2: active and default slots are completely unchanged
        assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
        assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
        assert!(
            engine.backend.default_slot_changes.is_empty(),
            "boot selection must remain unchanged"
        );

        // AC2: nothing was written to the inactive slot
        assert!(
            engine.backend.inactive_writes.is_empty(),
            "validation alone must perform zero writes to the inactive slot"
        );
    }

    #[test]
    fn test_ac3_validation_fails_with_distinct_machine_readable_codes() {
        let capacity = 10 * 1024 * 1024;

        // Case 1: Truncated update file -> update_truncated
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(capacity);
            let journal = MemoryJournalStorage::default();
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .unwrap();

            let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
            // Truncate CPIO archive halfway through root image
            let truncated = &swu_bytes[..swu_bytes.len() - 20];
            engine
                .stage_upload(Cursor::new(truncated), Some(truncated.len() as u64))
                .unwrap();

            let err = engine
                .validate()
                .expect_err("truncated archive must fail validation");
            assert_eq!(err.error_code_str(), "update_truncated");
            let st = engine.status();
            assert_eq!(st.state, UpdateTransactionState::Failed);
            assert_eq!(st.error_code, Some("update_truncated".to_string()));
            assert!(
                engine.backend.default_slot_changes.is_empty(),
                "boot selection unchanged"
            );
        }

        // Case 2: Corrupted artifact -> update_corrupted_artifact
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(capacity);
            let journal = MemoryJournalStorage::default();
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .unwrap();

            // Prepare sw-description with hash of b"original boot", but provide b"tampered boot" in CPIO
            let sw_desc = format!(
                r#"
software = {{
    version = "0.0.1";
    description = "Tampered artifact";
    hardware-compatibility: [ "1.0" ];
    images: (
        {{
            filename = "nextoomio-boot.vfat.gz";
            device = "/dev/mmcblk0p2";
            sha256 = "{}";
        }}
    );
}};
"#,
                Sha256::hex(b"original boot")
            );

            let corrupted_cpio = create_cpio(&[
                ("sw-description", sw_desc.as_bytes()),
                ("nextoomio-boot.vfat.gz", b"tampered boot content"),
            ]);

            engine
                .stage_upload(
                    Cursor::new(&corrupted_cpio),
                    Some(corrupted_cpio.len() as u64),
                )
                .unwrap();
            let err = engine
                .validate()
                .expect_err("corrupted artifact must fail validation");
            assert_eq!(err.error_code_str(), "update_corrupted_artifact");
            let st = engine.status();
            assert_eq!(st.state, UpdateTransactionState::Failed);
            assert_eq!(st.error_code, Some("update_corrupted_artifact".to_string()));
            assert!(
                engine.backend.default_slot_changes.is_empty(),
                "boot selection unchanged"
            );
        }

        // Case 3: Declares other hardware -> update_incompatible_hardware
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(capacity);
            let journal = MemoryJournalStorage::default();
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .unwrap();

            // Declares hardware "2.0" while target device is "1.0"
            let swu_bytes = make_valid_swu("2.0", 1, b"boot", b"root");
            engine
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .unwrap();

            let err = engine
                .validate()
                .expect_err("incompatible hardware must fail validation");
            assert_eq!(err.error_code_str(), "update_incompatible_hardware");
            let st = engine.status();
            assert_eq!(st.state, UpdateTransactionState::Failed);
            assert_eq!(
                st.error_code,
                Some("update_incompatible_hardware".to_string())
            );
            assert!(
                engine.backend.default_slot_changes.is_empty(),
                "boot selection unchanged"
            );
        }

        // Case 4: Exceeds reservation -> update_oversized
        {
            let small_cap = 500; // 500 bytes reservation
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(small_cap);
            let journal = MemoryJournalStorage::default();
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(small_cap),
            )
            .unwrap();

            let swu_bytes = make_valid_swu(
                "1.0",
                1,
                b"boot payload larger than reservation",
                b"root payload",
            );
            assert!(swu_bytes.len() as u64 > small_cap);

            let err = engine
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .expect_err("oversized upload must be rejected");
            assert_eq!(err.error_code_str(), "update_oversized");
            let st = engine.status();
            assert_eq!(st.state, UpdateTransactionState::Failed);
            assert_eq!(st.error_code, Some("update_oversized".to_string()));
            assert!(
                engine.backend.default_slot_changes.is_empty(),
                "boot selection unchanged"
            );
        }
    }

    #[test]
    fn test_ac4_interrupted_upload_by_dropped_connection_or_reboot() {
        let capacity = 10 * 1024 * 1024;

        // Subcase A: Interrupted connection mid-upload
        {
            struct FaultyReader<'a> {
                slice: &'a [u8],
                pos: usize,
                fail_at: usize,
            }
            impl<'a> Read for FaultyReader<'a> {
                fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
                    if self.pos >= self.fail_at {
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::ConnectionReset,
                            "dropped connection",
                        ));
                    }
                    let avail = &self.slice[self.pos..self.fail_at.min(self.slice.len())];
                    let to_read = avail.len().min(buf.len());
                    buf[..to_read].copy_from_slice(&avail[..to_read]);
                    self.pos += to_read;
                    Ok(to_read)
                }
            }

            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(capacity);
            let journal = MemoryJournalStorage::default();
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .unwrap();

            let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
            let faulty = FaultyReader {
                slice: &swu_bytes,
                pos: 0,
                fail_at: swu_bytes.len() / 2,
            };

            let err = engine
                .stage_upload(faulty, Some(swu_bytes.len() as u64))
                .expect_err("dropped connection must fail");
            assert_eq!(err.error_code_str(), "update_truncated");

            // AC4: when queried, no transaction is installable
            let status = engine.status();
            assert_eq!(status.state, UpdateTransactionState::Failed);
            assert!(status.tx_id.is_none());

            // AC4: a new upload can start
            let retry = engine
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .expect("retry must succeed");
            assert_eq!(retry.state, UpdateTransactionState::Staged);
        }

        // Subcase B: Reboot while Receiving
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = MockStagingStorage::new(capacity);
            let mut journal_storage = MemoryJournalStorage::default();

            // Simulate partial run that set state to Receiving and rebooted
            let mut unfinalized_rec = JournalRecord::new_initial("root_a", "root_a");
            unfinalized_rec.state = UpdateTransactionState::Receiving;
            unfinalized_rec.generation = 5;
            unfinalized_rec.checksum = unfinalized_rec.compute_checksum();
            journal_storage.records[0] = Some(unfinalized_rec);

            // Device boots up: engine initializes
            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal_storage,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("boot recovery init must succeed");

            // AC4: when queried, no transaction is installable
            let st = engine.status();
            assert_eq!(st.state, UpdateTransactionState::Failed);
            assert!(st.tx_id.is_none());

            // AC4: a new upload can start
            let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
            let retry = engine
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .expect("new upload after reboot must succeed");
            assert_eq!(retry.state, UpdateTransactionState::Staged);
        }
    }

    #[test]
    fn test_ac5_upload_in_progress_refuses_second_upload_as_busy() {
        let capacity = 10 * 1024 * 1024;
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = MockStagingStorage::new(capacity);
        let journal = MemoryJournalStorage::default();
        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        // 1. Begin first upload (transitions to Receiving)
        engine
            .begin_upload(Some(5000))
            .expect("first begin_upload must succeed");
        assert_eq!(engine.status().state, UpdateTransactionState::Receiving);

        // 2. Request second upload while first is in progress (AC5)
        let err = engine
            .begin_upload(Some(5000))
            .expect_err("second upload must be refused as busy");
        assert_eq!(err, UpdateEngineError::Busy);
        assert_eq!(err.error_code_str(), "update_busy");
    }

    #[test]
    fn test_ac6_staging_allocated_size_unchanged_across_failed_or_completed_transactions() {
        let capacity = 20 * 1024 * 1024; // 20 MiB
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = MockStagingStorage::new(capacity);
        let journal = MemoryJournalStorage::default();
        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        let initial_alloc = engine.storage.get_allocated_size().unwrap();
        assert_eq!(initial_alloc, capacity);

        // 1. Failed upload (truncated)
        let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
        let truncated = &swu_bytes[..swu_bytes.len() / 2];
        let _ = engine.stage_upload(Cursor::new(truncated), Some(truncated.len() as u64));
        let _ = engine.validate();
        assert_eq!(
            engine.storage.get_allocated_size().unwrap(),
            initial_alloc,
            "allocated extent must remain unchanged after failed transaction"
        );

        // 2. Completed / validated upload
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        let status = engine.validate().unwrap();
        assert_eq!(status.state, UpdateTransactionState::Validated);
        assert_eq!(
            engine.storage.get_allocated_size().unwrap(),
            initial_alloc,
            "allocated extent must remain unchanged after validated transaction"
        );

        // 3. Abort / cleanup
        engine.abort().unwrap();
        assert_eq!(
            engine.storage.get_allocated_size().unwrap(),
            initial_alloc,
            "allocated extent must remain unchanged after abort"
        );
    }
}
