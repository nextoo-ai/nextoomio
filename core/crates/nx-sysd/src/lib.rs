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
    FileStagingStorage, MockSlotBackend, MockStagingStorage, PartitionType, PhysicalSlotBackend,
    Slot, SlotBackend, StagingError, StagingStorage,
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
        copy2 = {{
            images: (
                {{
                    filename = "nextoomio-boot.vfat.gz";
                    device = "/dev/mmcblk0p3";
                    type = "raw";
                    compressed = "zlib";
                    sha256 = "{boot_sha}";
                    installed-directly = true;
                }},
                {{
                    filename = "nextoomio-image.rootfs.ext4.gz";
                    device = "/dev/mmcblk0p6";
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

    #[test]
    fn test_ac1_constrained_space_fixed_size_in_place_writes_never_grow_or_truncate_files() {
        let temp_dir =
            std::env::temp_dir().join(format!("nx-test-ac1-inplace-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        let capacity = 5 * 1024 * 1024; // 5 MiB test reservation
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

        let staging_file = temp_dir.join("staging.swu");
        let meta_file = temp_dir.join("staging.meta");
        let j0_file = temp_dir.join("journal.0");
        let j1_file = temp_dir.join("journal.1");

        // Verify all 4 files are preallocated with their exact fixed lengths
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        let swu_bytes = make_valid_swu("1.0", 1, b"boot test content", b"root test content");

        // 1. Begin upload -> transitions to Receiving
        engine.begin_upload(Some(swu_bytes.len() as u64)).unwrap();
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        // Abort upload -> transitions to Idle
        engine.abort().unwrap();
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        // 2. Stage payload -> transitions to Staged
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        // 3. Validate -> transitions to Validated
        let st = engine.validate().unwrap();
        assert_eq!(st.state, UpdateTransactionState::Validated);
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        // 4. Abort -> resets payload and returns to Idle
        engine.abort().unwrap();
        assert_eq!(std::fs::metadata(&staging_file).unwrap().len(), capacity);
        assert_eq!(std::fs::metadata(&meta_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j0_file).unwrap().len(), 4096);
        assert_eq!(std::fs::metadata(&j1_file).unwrap().len(), 4096);

        // Verify that no temporary files were created during the lifecycle
        let entries: Vec<String> = std::fs::read_dir(&temp_dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        for name in &entries {
            assert!(
                !name.ends_with(".tmp"),
                "no temporary files allowed: found {name}"
            );
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_staging_storage_write_error_reports_data_unavailable_distinct_from_update_truncated() {
        #[derive(Debug, Clone)]
        struct FailingWriteStorage;
        impl StagingStorage for FailingWriteStorage {
            fn init_staging(&mut self, _capacity: u64) -> Result<(), String> {
                Ok(())
            }
            fn get_allocated_size(&self) -> Result<u64, String> {
                Ok(1048576)
            }
            fn get_payload_size(&self) -> Result<u64, String> {
                Ok(0)
            }
            fn write_payload<R: Read>(
                &mut self,
                _reader: R,
                _declared_len: Option<u64>,
            ) -> Result<u64, StagingError> {
                Err(StagingError::Storage(
                    "simulated disk write failure / ENOSPC".to_string(),
                ))
            }
            fn read_payload(&self) -> Result<Vec<u8>, String> {
                Ok(vec![])
            }
            fn open_payload_reader(&self) -> Result<Box<dyn Read + Send>, String> {
                Ok(Box::new(Cursor::new(vec![])))
            }
            fn cleanup(&mut self) -> Result<(), String> {
                Ok(())
            }
        }

        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = FailingWriteStorage;
        let journal = MemoryJournalStorage::default();
        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(1048576),
        )
        .unwrap();

        let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
        let err = engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .expect_err("write storage failure must fail");

        // Storage errors must report data_unavailable, NOT update_truncated
        assert_eq!(err.error_code_str(), "data_unavailable");
        assert_eq!(engine.status().state, UpdateTransactionState::Failed);
        assert_eq!(
            engine.status().error_code,
            Some("data_unavailable".to_string())
        );
    }

    // =========================================================================
    // Acceptance tests for P0-F4.4: Install into the inactive slot (issue #20)
    // =========================================================================

    #[test]
    fn test_p0_f4_4_ac1_install_writes_only_inactive_slot_and_preserves_active_slot_checksums() {
        let capacity = 10 * 1024 * 1024;
        let backend = MockSlotBackend::new(Slot::A, Slot::A);
        let storage = MockStagingStorage::new(capacity);
        let journal = MemoryJournalStorage::default();

        // Record initial checksums of active slot A partitions
        let active_boot_before = backend
            .checksum_partition(Slot::A, PartitionType::Boot)
            .unwrap();
        let active_root_before = backend
            .checksum_partition(Slot::A, PartitionType::Root)
            .unwrap();

        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        let boot_data = b"new-boot-payload-vfat-partition-data";
        let root_data = b"new-rootfs-payload-ext4-partition-data";
        let swu_bytes = make_valid_swu("1.0", 1, boot_data, root_data);

        // Stage and validate
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        let val_status = engine.validate().unwrap();
        assert_eq!(val_status.state, UpdateTransactionState::Validated);
        assert_eq!(val_status.target_slot, Some("root_b".to_string()));

        // Execute install
        let inst_status = engine.install().unwrap();
        assert_eq!(inst_status.state, UpdateTransactionState::Installed);
        assert_eq!(inst_status.active_slot, "root_a");
        assert_eq!(inst_status.default_slot, "root_a");
        assert_eq!(inst_status.target_slot, Some("root_b".to_string()));

        // AC1: Checksums of active slot's partitions are identical before and after
        let active_boot_after = engine
            .backend
            .checksum_partition(Slot::A, PartitionType::Boot)
            .unwrap();
        let active_root_after = engine
            .backend
            .checksum_partition(Slot::A, PartitionType::Root)
            .unwrap();
        assert_eq!(
            active_boot_before, active_boot_after,
            "active boot slot checksum must be identical before and after install"
        );
        assert_eq!(
            active_root_before, active_root_after,
            "active root slot checksum must be identical before and after install"
        );

        // AC1: Only the inactive boot and root partitions are written
        assert_eq!(
            engine.backend.written_partitions,
            vec![
                (Slot::B, PartitionType::Boot),
                (Slot::B, PartitionType::Root)
            ],
            "only the inactive slot (B) boot and root partitions must be written"
        );

        // Negative test: write glitch modifying active slot partition during install triggers failure
        let mut glitch_backend = MockSlotBackend::new(Slot::A, Slot::A);
        glitch_backend.write_active_slot_glitch = true;
        let glitch_storage = MockStagingStorage::new(capacity);
        let glitch_journal = MemoryJournalStorage::default();
        let mut glitch_engine = UpdateEngine::new(
            glitch_backend,
            glitch_storage,
            glitch_journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        glitch_engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        glitch_engine.validate().unwrap();
        let err = glitch_engine
            .install()
            .expect_err("write glitch to active slot must cause install failure");
        assert!(
            matches!(err, UpdateEngineError::Storage(ref msg) if msg.contains("active slot partition checksum changed")),
            "expected active slot checksum change error, got: {err:?}"
        );
        assert_eq!(glitch_engine.status().state, UpdateTransactionState::Failed);
    }

    #[test]
    fn test_p0_f4_4_ac2_readback_checksum_verified_before_installed_state() {
        let capacity = 10 * 1024 * 1024;
        let mut backend = MockSlotBackend::new(Slot::A, Slot::A);
        // Force readback corruption
        backend.corrupt_readback = true;

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

        let swu_bytes = make_valid_swu("1.0", 1, b"boot-content", b"root-content");
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        engine.validate().unwrap();

        // AC2: Readback mismatch prevents state from becoming 'installed'
        let err = engine
            .install()
            .expect_err("readback checksum mismatch must fail install");

        assert!(
            matches!(err, UpdateEngineError::CorruptedArtifact { .. }),
            "expected CorruptedArtifact, got {err:?}"
        );
        assert_eq!(err.error_code_str(), "update_corrupted_artifact");

        let status = engine.status();
        assert_ne!(
            status.state,
            UpdateTransactionState::Installed,
            "state must not become installed when readback checksum mismatches"
        );
        assert_eq!(status.state, UpdateTransactionState::Failed);
        assert_eq!(
            status.error_code,
            Some("update_corrupted_artifact".to_string())
        );
    }

    #[test]
    fn test_p0_f4_4_ac3_install_refused_without_validated_transaction() {
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

        // 1. Refused from Idle (AC3)
        assert_eq!(engine.status().state, UpdateTransactionState::Idle);
        let err1 = engine
            .install()
            .expect_err("install from idle must be refused");
        assert!(matches!(
            err1,
            UpdateEngineError::InvalidStateTransition {
                from: UpdateTransactionState::Idle,
                to: UpdateTransactionState::Installing
            }
        ));
        assert!(engine.backend.written_partitions.is_empty());

        // 2. Refused from Receiving (AC3, AC5: busy)
        engine.begin_upload(None).unwrap();
        assert_eq!(engine.status().state, UpdateTransactionState::Receiving);
        let err2 = engine
            .install()
            .expect_err("install while receiving must be refused as busy");
        assert_eq!(err2, UpdateEngineError::Busy);
        assert!(engine.backend.written_partitions.is_empty());

        // 3. Refused from Staged (AC3)
        let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
        engine.abort().unwrap();
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        assert_eq!(engine.status().state, UpdateTransactionState::Staged);
        let err3 = engine
            .install()
            .expect_err("install from staged without validation must be refused");
        assert!(matches!(
            err3,
            UpdateEngineError::InvalidStateTransition {
                from: UpdateTransactionState::Staged,
                to: UpdateTransactionState::Installing
            }
        ));
        assert!(engine.backend.written_partitions.is_empty());

        // 4. Refused from Failed (AC3)
        let _ = engine.validate(); // Will validate
                                   // Now force abort and then failed
        let _ = engine.abort();
        let _ = engine.journal.commit_state(
            UpdateTransactionState::Failed,
            None,
            None,
            0,
            None,
            Some("test_error".to_string()),
        );
        assert_eq!(engine.status().state, UpdateTransactionState::Failed);
        let err4 = engine
            .install()
            .expect_err("install from failed state must be refused");
        assert!(matches!(
            err4,
            UpdateEngineError::InvalidStateTransition {
                from: UpdateTransactionState::Failed,
                to: UpdateTransactionState::Installing
            }
        ));
        assert!(engine.backend.written_partitions.is_empty());
    }

    #[test]
    fn test_p0_f4_4_ac4_sound_card_selection_preserved_in_new_boot_slot() {
        let capacity = 10 * 1024 * 1024;
        let mut backend = MockSlotBackend::new(Slot::A, Slot::A);

        // AC4: Running slot has sound-card selection configured
        let expected_hw_cfg = "dtoverlay=hifiberry-amp\n";
        backend
            .set_sound_card_selection(Slot::A, expected_hw_cfg)
            .unwrap();
        // Inactive slot B starts with no selection
        backend.sound_card_selections.insert(Slot::B, None);

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

        let swu_bytes = make_valid_swu("1.0", 1, b"boot-data", b"root-data");
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        engine.validate().unwrap();

        // Perform install
        let status = engine.install().unwrap();
        assert_eq!(status.state, UpdateTransactionState::Installed);

        // AC4: When install completes, then the same selection is present in the newly written boot slot
        let target_cfg = engine
            .backend
            .get_sound_card_selection(Slot::B)
            .unwrap()
            .expect("sound-card selection must be present in newly written boot slot");
        assert_eq!(
            target_cfg, expected_hw_cfg,
            "sound-card selection in newly written boot slot must match running slot"
        );
        assert_eq!(
            engine.backend.get_sound_card_selection(Slot::B).unwrap(),
            engine.backend.get_sound_card_selection(Slot::A).unwrap()
        );
    }

    #[test]
    fn test_p0_f4_4_ac5_interrupted_install_reboots_to_previous_slot_not_installed() {
        let temp_dir = std::env::temp_dir().join(format!("nx-test-ac5-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        let capacity = 10 * 1024 * 1024;
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
        .unwrap();

        let swu_bytes = make_valid_swu("1.0", 1, b"boot", b"root");
        engine
            .stage_upload(Cursor::new(&swu_bytes), Some(swu_bytes.len() as u64))
            .unwrap();
        engine.validate().unwrap();

        // Simulate install beginning (persisting 'installing' state)
        engine
            .journal
            .commit_state(
                UpdateTransactionState::Installing,
                Some("tx-test-ac5".to_string()),
                Some("root_b".to_string()),
                swu_bytes.len() as u64,
                Some(Sha256::hex(&swu_bytes)),
                None,
            )
            .unwrap();

        // Simulate sudden reboot or power loss: a fresh process starts and runs boot reconciliation
        let reboot_backend = MockSlotBackend::new(Slot::A, Slot::A);
        let reboot_storage = FileStagingStorage::new(&temp_dir, capacity);
        let reboot_journal = FileJournalStorage::new(&temp_dir);

        let mut reboot_engine = UpdateEngine::new(
            reboot_backend,
            reboot_storage,
            reboot_journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        // Boot reconciliation runs (via nx-sysd update init-staging / nextoomio-update-init.service)
        reboot_engine.reconcile_boot().unwrap();

        let status = reboot_engine.status();
        // AC5: State is not installed
        assert_ne!(
            status.state,
            UpdateTransactionState::Installed,
            "interrupted install must not result in installed state"
        );
        assert_eq!(status.state, UpdateTransactionState::Failed);

        // AC5: Device runs the previous slot
        assert_eq!(status.active_slot, "root_a");
        assert_eq!(status.default_slot, "root_a");

        // AC5: Trial boot cannot be requested from failed/interrupted install
        let install_err = reboot_engine.begin_install().expect_err(
            "trial boot or install of incomplete slot cannot be requested after interrupted install",
        );
        assert!(matches!(
            install_err,
            UpdateEngineError::InvalidStateTransition {
                from: UpdateTransactionState::Failed,
                to: UpdateTransactionState::Installing
            }
        ));

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_p0_f4_4_cli_end_to_end_file_storage_and_physical_backend() {
        let temp_dir = std::env::temp_dir().join(format!("nx-test-cli-e2e-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp_dir);
        std::fs::create_dir_all(&temp_dir).unwrap();

        // Setup mock partition files and boot directories for PhysicalSlotBackend
        let slot_a_boot_file = temp_dir.join("slot_a_boot.img");
        let slot_a_root_file = temp_dir.join("slot_a_root.img");
        let slot_b_boot_file = temp_dir.join("slot_b_boot.img");
        let slot_b_root_file = temp_dir.join("slot_b_root.img");

        std::fs::write(&slot_a_boot_file, b"initial-slot-a-boot-content").unwrap();
        std::fs::write(&slot_a_root_file, b"initial-slot-a-root-content").unwrap();
        std::fs::write(&slot_b_boot_file, b"initial-slot-b-boot-content").unwrap();
        std::fs::write(&slot_b_root_file, b"initial-slot-b-root-content").unwrap();

        let active_boot_dir = temp_dir.join("boot_active");
        let inactive_boot_dir = temp_dir.join("boot_inactive");
        std::fs::create_dir_all(&active_boot_dir).unwrap();
        std::fs::create_dir_all(&inactive_boot_dir).unwrap();

        let hw_cfg = "dtoverlay=hifiberry-amp\n";
        std::fs::write(active_boot_dir.join("nextoomio-hardware.txt"), hw_cfg).unwrap();

        std::env::set_var("NEXTOOMIO_SLOT", "root_a");
        std::env::set_var("NEXTOOMIO_SLOT_A_BOOT", &slot_a_boot_file);
        std::env::set_var("NEXTOOMIO_SLOT_A_ROOT", &slot_a_root_file);
        std::env::set_var("NEXTOOMIO_SLOT_B_BOOT", &slot_b_boot_file);
        std::env::set_var("NEXTOOMIO_SLOT_B_ROOT", &slot_b_root_file);
        std::env::set_var("NEXTOOMIO_ACTIVE_BOOT_DIR", &active_boot_dir);
        std::env::set_var("NEXTOOMIO_INACTIVE_BOOT_DIR", &inactive_boot_dir);

        let capacity = 10 * 1024 * 1024;
        let update_dir = temp_dir.join("update");

        // 1. Process 1: init-staging
        let backend = PhysicalSlotBackend;
        let storage = FileStagingStorage::new(&update_dir, capacity);
        let journal = FileJournalStorage::new(&update_dir);
        let mut engine = UpdateEngine::new(
            backend,
            storage,
            journal,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();
        engine.reconcile_boot().unwrap();

        // 2. Process 2: stage
        let new_boot = b"new-swu-boot-payload-data";
        let new_root = b"new-swu-root-payload-data";
        let swu_bytes = make_valid_swu("1.0", 1, new_boot, new_root);
        let swu_path = temp_dir.join("test.swu");
        std::fs::write(&swu_path, &swu_bytes).unwrap();

        let backend2 = PhysicalSlotBackend;
        let storage2 = FileStagingStorage::new(&update_dir, capacity);
        let journal2 = FileJournalStorage::new(&update_dir);
        let mut engine2 = UpdateEngine::new(
            backend2,
            storage2,
            journal2,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();

        let stage_file = std::fs::File::open(&swu_path).unwrap();
        let status2 = engine2
            .stage_upload(stage_file, Some(swu_bytes.len() as u64))
            .unwrap();
        assert_eq!(status2.state, UpdateTransactionState::Staged);

        // 3. Process 3: validate
        let backend3 = PhysicalSlotBackend;
        let storage3 = FileStagingStorage::new(&update_dir, capacity);
        let journal3 = FileJournalStorage::new(&update_dir);
        let mut engine3 = UpdateEngine::new(
            backend3,
            storage3,
            journal3,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();
        let status3 = engine3.validate().unwrap();
        assert_eq!(status3.state, UpdateTransactionState::Validated);
        assert_eq!(status3.target_slot, Some("root_b".to_string()));

        // Active slot checksums before install
        let active_boot_before = std::fs::read(&slot_a_boot_file).unwrap();
        let active_root_before = std::fs::read(&slot_a_root_file).unwrap();

        // 4. Process 4: install
        let backend4 = PhysicalSlotBackend;
        let storage4 = FileStagingStorage::new(&update_dir, capacity);
        let journal4 = FileJournalStorage::new(&update_dir);
        let mut engine4 = UpdateEngine::new(
            backend4,
            storage4,
            journal4,
            Some("1.0".to_string()),
            Some(1),
            Some(capacity),
        )
        .unwrap();
        let status4 = engine4.install().unwrap();
        assert_eq!(status4.state, UpdateTransactionState::Installed);
        assert_eq!(status4.target_slot, Some("root_b".to_string()));

        // Active slot partitions must be completely unchanged (AC1)
        let active_boot_after = std::fs::read(&slot_a_boot_file).unwrap();
        let active_root_after = std::fs::read(&slot_a_root_file).unwrap();
        assert_eq!(active_boot_before, active_boot_after);
        assert_eq!(active_root_before, active_root_after);

        // Inactive slot partitions must have received the new artifacts
        let inactive_boot_data = std::fs::read(&slot_b_boot_file).unwrap();
        let inactive_root_data = std::fs::read(&slot_b_root_file).unwrap();
        assert_eq!(&inactive_boot_data[..new_boot.len()], new_boot);
        assert_eq!(&inactive_root_data[..new_root.len()], new_root);

        // Sound-card selection must be present in inactive boot directory (AC4)
        let inactive_hw_cfg =
            std::fs::read_to_string(inactive_boot_dir.join("nextoomio-hardware.txt")).unwrap();
        assert_eq!(inactive_hw_cfg, hw_cfg);

        // Clean up environment variables
        std::env::remove_var("NEXTOOMIO_SLOT");
        std::env::remove_var("NEXTOOMIO_SLOT_A_BOOT");
        std::env::remove_var("NEXTOOMIO_SLOT_A_ROOT");
        std::env::remove_var("NEXTOOMIO_SLOT_B_BOOT");
        std::env::remove_var("NEXTOOMIO_SLOT_B_ROOT");
        std::env::remove_var("NEXTOOMIO_ACTIVE_BOOT_DIR");
        std::env::remove_var("NEXTOOMIO_INACTIVE_BOOT_DIR");

        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
