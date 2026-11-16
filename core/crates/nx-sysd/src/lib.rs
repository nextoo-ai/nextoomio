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
    SlotBackend, StagingError, StagingStorage,
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
    fn test_ac1_staging_preallocated_at_full_reserved_size_file_storage() {
        let temp_dir = std::env::temp_dir().join(format!("nx-test-ac1-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        let capacity = 10 * 1024 * 1024; // 10 MiB test reservation
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = FileStagingStorage::new(&temp_dir, capacity);
        let journal = FileJournalStorage::new(&temp_dir);

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
        let allocated = engine.storage.get_allocated_size().unwrap();
        assert!(
            allocated >= capacity,
            "staging extent must be preallocated to full capacity: allocated {allocated} < {capacity}"
        );

        // 2. Stage valid update into preallocated staging file (AC1)
        let swu_bytes = make_valid_swu("1.0", 1, b"boot payload data", b"rootfs payload data");
        let status = engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .expect("upload into preallocated staging must succeed");

        assert_eq!(status.state, UpdateTransactionState::Staged);
        assert_eq!(status.payload_length, swu_bytes.len() as u64);

        // Extent remains allocated after staging
        let allocated_after = engine.storage.get_allocated_size().unwrap();
        assert!(
            allocated_after >= capacity,
            "staging extent must remain allocated after upload: {allocated_after} < {capacity}"
        );

        let _ = std::fs::remove_dir_all(&temp_dir);
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
    }

    #[test]
    fn test_ac2_stage_and_validate_across_separate_engine_processes() {
        let temp_dir = std::env::temp_dir().join(format!("nx-test-proc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        let capacity = 10 * 1024 * 1024;
        let swu_bytes = make_valid_swu(
            "1.0",
            1,
            b"boot partition content",
            b"root partition content",
        );

        // Process 1: nx-sysd update stage
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = FileStagingStorage::new(&temp_dir, capacity);
            let journal = FileJournalStorage::new(&temp_dir);

            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("process 1 initialization failed");

            let status = engine
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .expect("process 1 stage failed");

            assert_eq!(status.state, UpdateTransactionState::Staged);
            assert_eq!(status.payload_length, swu_bytes.len() as u64);
        }

        // Process 2: nx-sysd update validate (new instance over the same directory)
        {
            let backend = MockSlotBackend::new(Slot::A, Slot::A);
            let storage = FileStagingStorage::new(&temp_dir, capacity);
            let journal = FileJournalStorage::new(&temp_dir);

            let mut engine = UpdateEngine::new(
                backend,
                storage,
                journal,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("process 2 initialization failed");

            // Verify payload metadata was not wiped by init_staging on process start
            assert_eq!(engine.status().state, UpdateTransactionState::Staged);
            assert_eq!(engine.status().payload_length, swu_bytes.len() as u64);

            let status = engine
                .validate()
                .expect("validation in second process must succeed");

            assert_eq!(status.state, UpdateTransactionState::Validated);
            assert!(status.tx_id.is_some());
            assert_eq!(status.target_slot, Some("root_b".to_string()));
            assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
            assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
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
            assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
            assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
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
            assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
            assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
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
            assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
            assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
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
            assert_eq!(engine.backend.get_active_slot().unwrap(), Slot::A);
            assert_eq!(engine.backend.get_default_slot().unwrap(), Slot::A);
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

            // On real boot, init-staging calls reconcile_boot()
            engine
                .reconcile_boot()
                .expect("reconcile_boot must succeed");

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
    fn test_ac5_upload_in_progress_refuses_second_upload_and_install_as_busy() {
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

        // 3. Request install while upload is in progress (AC5)
        let err_install = engine
            .begin_install()
            .expect_err("install must be refused as busy while upload is in progress");
        assert_eq!(err_install, UpdateEngineError::Busy);
        assert_eq!(err_install.error_code_str(), "update_busy");
    }

    #[test]
    fn test_ac5_separate_engine_processes_over_same_journal_sees_receiving_and_refuses_busy() {
        let temp_dir =
            std::env::temp_dir().join(format!("nx-test-ac5-proc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        let capacity = 10 * 1024 * 1024;

        // Process 1: Starts an upload over the shared update directory (state -> Receiving)
        {
            let backend1 = MockSlotBackend::new(Slot::A, Slot::A);
            let storage1 = FileStagingStorage::new(&temp_dir, capacity);
            let journal1 = FileJournalStorage::new(&temp_dir);
            let mut engine1 = UpdateEngine::new(
                backend1,
                storage1,
                journal1,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("engine 1 init failed");

            engine1
                .begin_upload(Some(5000))
                .expect("begin_upload must succeed");
            assert_eq!(engine1.status().state, UpdateTransactionState::Receiving);
        }

        // Process 2: Invoked concurrently over the same journal directory (e.g. nx-sysd update status or stage)
        {
            let backend2 = MockSlotBackend::new(Slot::A, Slot::A);
            let storage2 = FileStagingStorage::new(&temp_dir, capacity);
            let journal2 = FileJournalStorage::new(&temp_dir);
            let mut engine2 = UpdateEngine::new(
                backend2,
                storage2,
                journal2,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("engine 2 init failed");

            // Process 2 status sees Receiving (not clobbered by construction!)
            assert_eq!(engine2.status().state, UpdateTransactionState::Receiving);

            // Process 2 attempts second upload while Process 1 is receiving -> refused as busy (AC5)
            let err_upload = engine2
                .begin_upload(Some(5000))
                .expect_err("concurrent upload must be refused as busy");
            assert_eq!(err_upload, UpdateEngineError::Busy);
            assert_eq!(err_upload.error_code_str(), "update_busy");

            // Process 2 attempts install while Process 1 is receiving -> refused as busy (AC5)
            let err_install = engine2
                .begin_install()
                .expect_err("concurrent install must be refused as busy");
            assert_eq!(err_install, UpdateEngineError::Busy);
            assert_eq!(err_install.error_code_str(), "update_busy");
        }

        // Process 3: Simulates device reboot where init-staging runs reconcile_boot()
        {
            let backend3 = MockSlotBackend::new(Slot::A, Slot::A);
            let storage3 = FileStagingStorage::new(&temp_dir, capacity);
            let journal3 = FileJournalStorage::new(&temp_dir);
            let mut engine3 = UpdateEngine::new(
                backend3,
                storage3,
                journal3,
                Some("1.0".to_string()),
                Some(1),
                Some(capacity),
            )
            .expect("engine 3 init failed");

            engine3
                .reconcile_boot()
                .expect("reconcile_boot on boot must succeed");
            assert_eq!(engine3.status().state, UpdateTransactionState::Failed);
            assert_eq!(
                engine3.status().error_code,
                Some("update_truncated".to_string())
            );

            // After reboot reconciliation, a new upload can start and succeed (AC4)
            let swu_bytes = make_valid_swu("1.0", 1, b"boot payload", b"rootfs payload");
            let status = engine3
                .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
                .expect("upload after boot reconciliation must succeed");
            assert_eq!(status.state, UpdateTransactionState::Staged);
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_journal_durability_and_corruption_handling() {
        let temp_dir = std::env::temp_dir().join(format!("nx-test-jnl-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        // 1. Fresh boot initializes Idle record in slot 0
        {
            let storage = FileJournalStorage::new(&temp_dir);
            let ctrl = JournalController::load_or_init(storage, "root_a", "root_a").unwrap();
            assert_eq!(ctrl.current().state, UpdateTransactionState::Idle);
            assert_eq!(ctrl.current().generation, 1);
        }

        // 2. Commit a new state: writes slot 1
        {
            let storage = FileJournalStorage::new(&temp_dir);
            let mut ctrl = JournalController::load_or_init(storage, "root_a", "root_a").unwrap();
            ctrl.commit_state(
                UpdateTransactionState::Staged,
                None,
                None,
                100,
                Some("sha".to_string()),
                None,
            )
            .unwrap();
            assert_eq!(ctrl.current().state, UpdateTransactionState::Staged);
            assert_eq!(ctrl.current().generation, 2);
        }

        // 3. Simulate torn write on slot 0 (slot 1 remains valid)
        std::fs::write(temp_dir.join("journal.0"), b"corrupted garbage").unwrap();
        {
            let storage = FileJournalStorage::new(&temp_dir);
            let ctrl = JournalController::load_or_init(storage, "root_a", "root_a").unwrap();
            assert_eq!(ctrl.current().state, UpdateTransactionState::Staged);
            assert_eq!(ctrl.current().generation, 2);
        }

        // 4. Simulate corruption on both slots: load_or_init must fail explicitly, never reset to Idle
        std::fs::write(temp_dir.join("journal.1"), b"corrupted garbage").unwrap();
        {
            let storage = FileJournalStorage::new(&temp_dir);
            let err = JournalController::load_or_init(storage, "root_a", "root_a")
                .expect_err("both corrupted records must fail explicit initialization");
            assert!(err.contains("refusing to discard state"));
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
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
