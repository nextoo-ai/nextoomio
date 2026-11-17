//! Tests for P0-F4.6: commit with remote confirmation on request (AD-22) and the
//! recovery of an interrupted commit (D-026).
//!
//! Acceptance criteria are named in the test names (`ac1` .. `ac5`); AC4 (ten reboots on
//! the bench) is a hardware criterion and is not covered here.

use crate::engine::{UpdateEngine, UpdateEngineError};
use crate::journal::{
    FileJournalStorage, JournalRecord, JournalStorage, MemoryJournalStorage, ReconcileOutcome,
};
use crate::system::{
    evaluate_health_criteria, parse_selector_default, selector_content, MockSlotBackend,
    MockStagingStorage, PhysicalSlotBackend, Slot, SlotBackend,
};
use nx_protocol::UpdateTransactionState;

const TX: &str = "tx-p0f46-0001";
const CAPACITY: u64 = 1024 * 1024;

type MockEngine = UpdateEngine<MockSlotBackend, MockStagingStorage, MemoryJournalStorage>;

fn record(state: UpdateTransactionState, remote_confirm: bool, attempts: u32) -> JournalRecord {
    let mut rec = JournalRecord::new_initial("root_b", "root_a");
    rec.state = state;
    rec.tx_id = Some(TX.to_string());
    rec.target_slot = Some("root_b".to_string());
    rec.payload_len = 4096;
    rec.payload_sha256 = Some("ab".repeat(32));
    rec.consumed_attempts = attempts;
    rec.remote_confirm = remote_confirm;
    rec.checksum = rec.compute_checksum();
    rec
}

fn engine_with(backend: MockSlotBackend, journal: MemoryJournalStorage) -> MockEngine {
    UpdateEngine::new(
        backend,
        MockStagingStorage::new(CAPACITY),
        journal,
        Some("test-hardware".to_string()),
        None,
        Some(CAPACITY),
    )
    .unwrap()
}

/// Engine in a trial boot of slot B (previous default A), attempt 1.
fn trial_engine(remote_confirm: bool) -> MockEngine {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Trial, remote_confirm, 1))
        .unwrap();
    let mut backend = MockSlotBackend::new(Slot::B, Slot::A);
    backend.uptime_secs = 30.0;
    engine_with(backend, journal)
}

/// Simulates a reboot: a new engine on the same journal with the given running slot and
/// the selector as the previous boot left it.
fn reboot(engine: &MockEngine, running: Slot) -> MockEngine {
    let journal = engine.journal.storage().clone();
    let backend = MockSlotBackend::new(running, engine.backend.default_slot);
    engine_with(backend, journal)
}

fn unhealthy() -> crate::system::HealthReport {
    evaluate_health_criteria(true, "", true, false, false)
}

/// Uptime that reaches the 120 s deadline after a few polls.
fn advance_uptime_quickly(engine: &mut MockEngine) {
    engine.backend.uptime_secs = 100.0;
    engine
        .backend
        .uptime_step
        .store(5_000, std::sync::atomic::Ordering::SeqCst);
}

fn newest_two(engine: &MockEngine) -> (JournalRecord, JournalRecord) {
    let storage = engine.journal.storage();
    let a = storage.records[0].clone().unwrap();
    let b = storage.records[1].clone().unwrap();
    if a.generation > b.generation {
        (b, a)
    } else {
        (a, b)
    }
}

#[test]
fn ac1_confirmed_trial_is_committed_and_next_ordinary_boot_runs_it() {
    let mut engine = trial_engine(true);

    let status = engine.confirm(None).unwrap();
    assert_eq!(status.state, UpdateTransactionState::Trial);
    assert!(status.remote_confirm);
    assert!(status.confirmed);

    engine.run_trial_watchdog(120, 50).unwrap();

    let status = engine.status();
    assert_eq!(status.state, UpdateTransactionState::Committed);
    assert_eq!(status.default_slot, "root_b");
    assert_eq!(status.tx_id.as_deref(), Some(TX));
    assert_eq!(engine.journal.current().default_slot, "root_b");
    assert_eq!(engine.backend.selector_writes, 1);
    assert_eq!(engine.backend.trial_armed, None);
    assert!(engine.backend.watchdog_disarmed);
    assert!(!engine.backend.reboot_triggered);

    // The next ordinary reboot boots the default slot, which is now B.
    let mut next = reboot(&engine, engine.backend.default_slot);
    assert_eq!(next.backend.get_active_slot().unwrap(), Slot::B);
    assert_eq!(next.reconcile_boot().unwrap(), ReconcileOutcome::Unchanged);
    assert_eq!(next.status().state, UpdateTransactionState::Committed);
    next.run_trial_watchdog(120, 50).unwrap();
    assert!(!next.backend.reboot_triggered);
}

#[test]
fn ac1_committing_is_recorded_before_the_switch_and_committed_after_read_back() {
    let mut engine = trial_engine(true);
    engine.confirm(Some(TX)).unwrap();
    engine.commit_trial().unwrap();

    let (before, newest) = newest_two(&engine);
    assert_eq!(before.state, UpdateTransactionState::Committing);
    assert_eq!(before.default_slot, "root_a");
    assert_eq!(newest.state, UpdateTransactionState::Committed);
    assert_eq!(newest.default_slot, "root_b");
    assert_eq!(newest.generation, before.generation + 1);
}

#[test]
fn ac1_read_back_mismatch_never_records_committed_and_returns_to_previous_slot() {
    let mut engine = trial_engine(true);
    engine.backend.selector_readback_override = Some(Slot::A);
    engine.confirm(None).unwrap();

    let err = engine.run_trial_watchdog(120, 50).unwrap_err();
    assert!(matches!(err, UpdateEngineError::CommitFailed(_)));
    assert_eq!(err.error_code_str(), "update_commit_failed");

    let status = engine.status();
    assert_eq!(status.state, UpdateTransactionState::Failed);
    assert_eq!(status.error_code.as_deref(), Some("update_commit_failed"));
    assert_eq!(engine.backend.default_slot, Slot::A);
    assert!(engine.backend.reboot_triggered);
    let (_, newest) = newest_two(&engine);
    assert_ne!(newest.state, UpdateTransactionState::Committed);
}

#[test]
fn ac1_read_back_failure_or_write_failure_never_records_committed() {
    let mut unreadable = trial_engine(false);
    unreadable.backend.selector_unreadable = true;
    let err = unreadable.commit_trial().unwrap_err();
    assert!(matches!(err, UpdateEngineError::CommitFailed(_)));
    assert_eq!(unreadable.status().state, UpdateTransactionState::Failed);

    let mut unwritable = trial_engine(false);
    unwritable.backend.fail_selector_write = true;
    let err = unwritable.commit_trial().unwrap_err();
    assert!(matches!(err, UpdateEngineError::CommitFailed(_)));
    assert_eq!(unwritable.status().state, UpdateTransactionState::Failed);
    assert_eq!(unwritable.backend.default_slot, Slot::A);
}

#[test]
fn ac2_without_confirmation_the_device_returns_and_the_attempt_counts_as_failed() {
    let mut engine = trial_engine(true);
    advance_uptime_quickly(&mut engine);

    let err = engine.run_trial_watchdog(120, 50).unwrap_err();
    assert_eq!(err, UpdateEngineError::TrialDeadlineExceeded);
    assert!(engine.backend.reboot_triggered);
    assert_eq!(engine.backend.selector_writes, 0);
    assert_eq!(engine.backend.default_slot, Slot::A);
    assert_eq!(engine.status().state, UpdateTransactionState::Trial);

    // Back on the previous slot: attempt 1 is counted as failed and attempt 2 is armed,
    // still requiring its own confirmation.
    let mut previous = reboot(&engine, Slot::A);
    assert_eq!(
        previous.reconcile_boot().unwrap(),
        ReconcileOutcome::SecondTrialRequested
    );
    let status = previous.status();
    assert_eq!(status.consumed_attempts, 2);
    assert!(status.remote_confirm);
    assert!(!status.confirmed);

    // Attempt 2 is not confirmed either: the transaction ends failed on the previous slot.
    let mut second = reboot(&previous, Slot::B);
    assert_eq!(
        second.reconcile_boot().unwrap(),
        ReconcileOutcome::TrialInProgress
    );
    advance_uptime_quickly(&mut second);
    assert_eq!(
        second.run_trial_watchdog(120, 50).unwrap_err(),
        UpdateEngineError::TrialDeadlineExceeded
    );
    let mut back = reboot(&second, Slot::A);
    assert_eq!(
        back.reconcile_boot().unwrap(),
        ReconcileOutcome::TrialExhausted
    );
    let status = back.status();
    assert_eq!(status.state, UpdateTransactionState::Failed);
    assert_eq!(status.error_code.as_deref(), Some("update_trial_exhausted"));
    assert_eq!(status.default_slot, "root_a");
}

#[test]
fn ac2_confirmation_of_a_previous_attempt_does_not_commit_the_next_attempt() {
    let mut engine = trial_engine(true);
    engine.backend.health_report = Some(unhealthy());
    engine.confirm(None).unwrap();
    advance_uptime_quickly(&mut engine);
    assert_eq!(
        engine.run_trial_watchdog(120, 50).unwrap_err(),
        UpdateEngineError::TrialDeadlineExceeded
    );

    let mut previous = reboot(&engine, Slot::A);
    previous.reconcile_boot().unwrap();
    let mut second = reboot(&previous, Slot::B);
    second.reconcile_boot().unwrap();
    assert!(!second.status().confirmed);
    let err = second.commit_trial().unwrap_err();
    assert_eq!(err.error_code_str(), "update_confirm_rejected");
    assert_eq!(second.status().state, UpdateTransactionState::Trial);
}

#[test]
fn ac2_health_passed_but_unconfirmed_is_not_committed_before_the_deadline() {
    let mut engine = trial_engine(true);
    let err = engine.commit_trial().unwrap_err();
    assert!(matches!(err, UpdateEngineError::ConfirmRejected(_)));
    assert_eq!(engine.status().state, UpdateTransactionState::Trial);
    assert_eq!(engine.backend.selector_writes, 0);
}

#[test]
fn ac3_confirmation_for_another_transaction_is_rejected() {
    let mut engine = trial_engine(true);
    let generation = engine.journal.current().generation;

    let err = engine.confirm(Some("tx-other-0002")).unwrap_err();
    assert_eq!(
        err,
        UpdateEngineError::TransactionMismatch {
            current: Some(TX.to_string()),
            received: "tx-other-0002".to_string(),
        }
    );
    assert_eq!(err.error_code_str(), "update_transaction_mismatch");
    assert_eq!(engine.journal.current().generation, generation);
    assert!(!engine.status().confirmed);

    // Nothing was confirmed, so the trial is not committed and returns at the deadline.
    advance_uptime_quickly(&mut engine);
    assert_eq!(
        engine.run_trial_watchdog(120, 50).unwrap_err(),
        UpdateEngineError::TrialDeadlineExceeded
    );
    assert_eq!(engine.backend.selector_writes, 0);

    // The current transaction's own id is accepted.
    let mut engine = trial_engine(true);
    assert!(engine.confirm(Some(TX)).unwrap().confirmed);
}

#[test]
fn ac3_confirmation_without_a_trial_on_the_trial_slot_is_rejected() {
    // No trial in progress.
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Installed, false, 0))
        .unwrap();
    let mut installed = engine_with(MockSlotBackend::new(Slot::A, Slot::A), journal);
    let err = installed.confirm(None).unwrap_err();
    assert_eq!(err.error_code_str(), "update_confirm_rejected");

    // Running the previous slot, not the trial slot.
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Trial, true, 1))
        .unwrap();
    let mut on_previous = engine_with(MockSlotBackend::new(Slot::A, Slot::A), journal);
    let err = on_previous.confirm(None).unwrap_err();
    assert_eq!(err.error_code_str(), "update_confirm_rejected");
    assert!(!on_previous.status().confirmed);

    // Remote confirmation was not requested for this transaction.
    let mut local_only = trial_engine(false);
    let err = local_only.confirm(None).unwrap_err();
    assert_eq!(err.error_code_str(), "update_confirm_rejected");
}

#[test]
fn ac3_repeated_confirmation_after_commit_changes_nothing() {
    let mut engine = trial_engine(true);
    engine.confirm(None).unwrap();
    engine.commit_trial().unwrap();
    let generation = engine.journal.current().generation;

    let status = engine.confirm(Some(TX)).unwrap();
    assert_eq!(status.state, UpdateTransactionState::Committed);
    assert_eq!(engine.journal.current().generation, generation);
}

#[test]
fn ac5_without_remote_confirmation_the_healthy_trial_commits_without_waiting() {
    let mut engine = trial_engine(false);
    engine.run_trial_watchdog(120, 50).unwrap();

    let status = engine.status();
    assert_eq!(status.state, UpdateTransactionState::Committed);
    assert!(!status.remote_confirm);
    assert_eq!(status.default_slot, "root_b");
    assert!(!engine.backend.reboot_triggered);
}

#[test]
fn ac5_trial_without_health_is_not_committed_even_when_confirmed() {
    let mut engine = trial_engine(true);
    engine.backend.health_report = Some(unhealthy());
    engine.confirm(None).unwrap();
    advance_uptime_quickly(&mut engine);

    assert_eq!(
        engine.run_trial_watchdog(120, 50).unwrap_err(),
        UpdateEngineError::TrialDeadlineExceeded
    );
    assert_eq!(engine.backend.selector_writes, 0);
    assert_eq!(engine.status().state, UpdateTransactionState::Trial);
}

#[test]
fn ac5_remote_confirmation_is_refused_while_ssh_is_disabled() {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Installed, false, 0))
        .unwrap();
    let mut backend = MockSlotBackend::new(Slot::A, Slot::A);
    backend.ssh_enabled = false;
    let mut engine = engine_with(backend, journal);
    let generation = engine.journal.current().generation;

    let err = engine.request_trial_with(true, true).unwrap_err();
    assert_eq!(err.error_code_str(), "update_remote_confirm_unavailable");
    assert_eq!(engine.journal.current().generation, generation);
    assert_eq!(engine.status().state, UpdateTransactionState::Installed);
    assert_eq!(engine.status().consumed_attempts, 0);
    assert_eq!(engine.backend.trial_armed, None);
    assert!(!engine.backend.tryboot_triggered);

    // Without remote confirmation the same trial is armed.
    let status = engine.request_trial_with(false, false).unwrap();
    assert_eq!(status.state, UpdateTransactionState::Trial);
    assert!(!status.remote_confirm);
}

#[test]
fn ac5_remote_confirmation_is_recorded_with_the_trial_while_ssh_is_enabled() {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Installed, false, 0))
        .unwrap();
    let mut engine = engine_with(MockSlotBackend::new(Slot::A, Slot::A), journal);

    let status = engine.request_trial_with(false, true).unwrap();
    assert_eq!(status.state, UpdateTransactionState::Trial);
    assert!(status.remote_confirm);
    assert!(!status.confirmed);
    assert_eq!(status.consumed_attempts, 1);
    assert_eq!(engine.backend.trial_armed, Some(Slot::B));
}

#[test]
fn d026_interrupted_commit_with_switched_selector_is_finished() {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Committing, true, 1))
        .unwrap();
    // The selector already names B, so the ordinary boot after the interruption runs B.
    let mut engine = engine_with(MockSlotBackend::new(Slot::B, Slot::B), journal);

    assert_eq!(
        engine.reconcile_boot().unwrap(),
        ReconcileOutcome::CommitFinished
    );
    let status = engine.status();
    assert_eq!(status.state, UpdateTransactionState::Committed);
    assert_eq!(engine.journal.current().default_slot, "root_b");
    assert_eq!(engine.backend.selector_writes, 0);
}

#[test]
fn d026_interrupted_commit_without_switched_selector_returns_to_previous_slot() {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Committing, true, 1))
        .unwrap();
    let mut engine = engine_with(MockSlotBackend::new(Slot::A, Slot::A), journal);

    assert_eq!(
        engine.reconcile_boot().unwrap(),
        ReconcileOutcome::CommitReturned
    );
    let status = engine.status();
    assert_eq!(status.state, UpdateTransactionState::Failed);
    assert_eq!(
        status.error_code.as_deref(),
        Some("update_commit_interrupted")
    );
    assert_eq!(engine.backend.default_slot, Slot::A);

    // A further start leaves the ended transaction alone.
    let mut again = reboot(&engine, Slot::A);
    assert_eq!(again.reconcile_boot().unwrap(), ReconcileOutcome::Unchanged);
    assert_eq!(again.status().state, UpdateTransactionState::Failed);
}

#[test]
fn d026_interrupted_commit_with_unreadable_selector_is_never_left_committing() {
    let mut journal = MemoryJournalStorage::default();
    journal
        .write_record(0, &record(UpdateTransactionState::Committing, false, 1))
        .unwrap();
    let mut backend = MockSlotBackend::new(Slot::A, Slot::A);
    backend.selector_unreadable = true;
    let mut engine = engine_with(backend, journal);

    assert_eq!(
        engine.reconcile_boot().unwrap(),
        ReconcileOutcome::CommitReturned
    );
    assert_eq!(engine.status().state, UpdateTransactionState::Failed);
    assert_eq!(engine.backend.selector_writes, 1);
}

#[test]
fn journal_records_without_remote_confirmation_keep_the_format_1_layout() {
    let rec = record(UpdateTransactionState::Trial, false, 1);
    let json = serde_json::to_string(&rec).unwrap();
    assert!(!json.contains("remote_confirm"));
    assert!(!json.contains("confirmed_attempt"));

    let mut confirmed = record(UpdateTransactionState::Trial, true, 1);
    confirmed.confirmed_attempt = 1;
    confirmed.checksum = confirmed.compute_checksum();
    let parsed: JournalRecord =
        serde_json::from_str(&serde_json::to_string(&confirmed).unwrap()).unwrap();
    assert!(parsed.is_valid());
    assert!(parsed.is_confirmed());
}

#[test]
fn selector_parsing_is_strict() {
    assert_eq!(parse_selector_default(&selector_content(3)), Ok(Slot::B));
    assert_eq!(
        parse_selector_default(
            "[all]\ntryboot_a_b=1\nboot_partition=2\n\n[tryboot]\nboot_partition=3\n"
        ),
        Ok(Slot::A)
    );
    assert!(parse_selector_default("").is_err());
    assert!(parse_selector_default("[all]\nboot_partition=7\n").is_err());
    assert!(parse_selector_default("[all]\nboot_partition=2\nboot_partition=3\n").is_err());
    assert!(parse_selector_default("[tryboot]\nboot_partition=3\n").is_err());
}

struct EnvGuard(Vec<&'static str>);

impl EnvGuard {
    fn set(vars: &[(&'static str, &std::path::Path)]) -> Self {
        for (k, v) in vars {
            std::env::set_var(k, v);
        }
        Self(vars.iter().map(|(k, _)| *k).collect())
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        for k in &self.0 {
            std::env::remove_var(k);
        }
    }
}

fn temp_dir(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("nx-test-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn ac1_physical_selector_content_before_and_after_commit() {
    let _env = crate::tests::ENV_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let dir = temp_dir("p0f46-selector");
    let select_dir = dir.join("select");
    let update_dir = dir.join("update");
    std::fs::create_dir_all(&select_dir).unwrap();
    let _vars = EnvGuard::set(&[
        ("NEXTOOMIO_SELECT_DIR", &select_dir),
        ("NEXTOOMIO_SLOT", std::path::Path::new("root_b")),
    ]);

    // Selector as armed for the trial of slot B by P0-F4.5.
    let before = "[all]\ntryboot_a_b=1\nboot_partition=2\n\n[tryboot]\nboot_partition=3\n";
    std::fs::write(select_dir.join("autoboot.txt"), before).unwrap();

    let mut journal = FileJournalStorage::new(&update_dir);
    journal
        .write_record(0, &record(UpdateTransactionState::Trial, true, 1))
        .unwrap();

    // Process 1: the workstation's confirmation over SSH.
    {
        let mut engine = UpdateEngine::new(
            PhysicalSlotBackend,
            MockStagingStorage::new(CAPACITY),
            FileJournalStorage::new(&update_dir),
            Some("test-hardware".to_string()),
            None,
            Some(CAPACITY),
        )
        .unwrap();
        assert!(engine.confirm(None).unwrap().confirmed);
    }

    // Process 2: the trial service commits after the local health check passed.
    let mut engine = UpdateEngine::new(
        PhysicalSlotBackend,
        MockStagingStorage::new(CAPACITY),
        FileJournalStorage::new(&update_dir),
        Some("test-hardware".to_string()),
        None,
        Some(CAPACITY),
    )
    .unwrap();
    let status = engine.commit_trial().unwrap();
    assert_eq!(status.state, UpdateTransactionState::Committed);
    assert_eq!(status.default_slot, "root_b");

    let after = std::fs::read_to_string(select_dir.join("autoboot.txt")).unwrap();
    assert_eq!(after, "[all]\ntryboot_a_b=1\nboot_partition=3\n");
    assert_eq!(engine.backend.read_back_default_slot(), Ok(Slot::B));
    // No temporary selector file is left behind.
    let leftovers: Vec<_> = std::fs::read_dir(&select_dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name() != "autoboot.txt")
        .collect();
    assert!(leftovers.is_empty());

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn ac5_physical_ssh_access_follows_the_key_file_on_the_selector_partition() {
    let _env = crate::tests::ENV_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let dir = temp_dir("p0f46-ssh");
    let _vars = EnvGuard::set(&[("NEXTOOMIO_SELECT_DIR", &dir)]);
    let backend = PhysicalSlotBackend;

    assert_eq!(backend.ssh_access_enabled(), Ok(false));

    std::fs::write(dir.join("authorized_keys"), "not-a-key\n").unwrap();
    assert_eq!(backend.ssh_access_enabled(), Ok(false));

    std::fs::write(
        dir.join("authorized_keys"),
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderKeyMaterialForTests bench\n",
    )
    .unwrap();
    assert_eq!(backend.ssh_access_enabled(), Ok(true));

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn confirmation_from_another_process_is_committed_by_the_running_trial_service() {
    let dir = temp_dir("p0f46-multiprocess");
    let update_dir = dir.join("update");
    let mut journal = FileJournalStorage::new(&update_dir);
    journal
        .write_record(0, &record(UpdateTransactionState::Trial, true, 1))
        .unwrap();

    let service_dir = update_dir.clone();
    let service = std::thread::spawn(move || {
        let mut backend = MockSlotBackend::new(Slot::B, Slot::A);
        backend.uptime_secs = 30.0;
        // Bounded: the deadline ends the service even if the confirmation never arrives.
        backend
            .uptime_step
            .store(100, std::sync::atomic::Ordering::SeqCst);
        let mut engine = UpdateEngine::new(
            backend,
            MockStagingStorage::new(CAPACITY),
            FileJournalStorage::new(&service_dir),
            Some("test-hardware".to_string()),
            None,
            Some(CAPACITY),
        )
        .unwrap();
        let result = engine.run_trial_watchdog(120, 50);
        (result, engine.backend.default_slot)
    });

    std::thread::sleep(std::time::Duration::from_millis(200));
    let mut workstation = UpdateEngine::new(
        MockSlotBackend::new(Slot::B, Slot::A),
        MockStagingStorage::new(CAPACITY),
        FileJournalStorage::new(&update_dir),
        Some("test-hardware".to_string()),
        None,
        Some(CAPACITY),
    )
    .unwrap();
    assert_eq!(
        workstation.confirm(None).unwrap().state,
        UpdateTransactionState::Trial
    );
    let status = workstation.wait_for_commit(10, 50);
    assert_eq!(status.state, UpdateTransactionState::Committed);

    let (result, selected) = service.join().unwrap();
    assert!(result.is_ok());
    assert_eq!(selected, Slot::B);

    let _ = std::fs::remove_dir_all(&dir);
}
