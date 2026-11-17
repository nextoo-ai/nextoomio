//! Tests of the single updater lock between real processes (D-065).
//!
//! Each test starts this test executable again as separate operating-system processes.
//! A child selected by `NX_SYSD_TEST_CHILD` runs one of:
//!
//! - `cli`: the `nx-sysd` command line in `NX_SYSD_TEST_ARGS` through [`crate::run`], the
//!   same dispatch `main` uses, and exits with its exit code;
//! - `hold-lock`: takes the updater lock, optionally records the remote confirmation under
//!   it, reports through a marker file, holds the lock for a while, and exits;
//! - `watchdog`: the trial service, [`nx_sysd::UpdateEngine::run_trial_watchdog`] on the
//!   shared journal, with a mock slot backend whose local health check passes (the
//!   physical health check needs a device: `/data` mounted and an openable sound card).
//!
//! The processes share only the temporary update directory, so the lock, the journal, and
//! the staging bytes are shared exactly as between `nx-sysd` invocations on the device.

use nx_protocol::UpdateTransactionState;
use nx_sysd::journal::{FileJournalStorage, JournalController, JournalRecord, JournalStorage};
use nx_sysd::sha256::Sha256;
use nx_sysd::{FileStagingStorage, MockSlotBackend, Slot, UpdateEngine};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitCode, Output, Stdio};
use std::time::{Duration, Instant};

const CHILD_MODE: &str = "NX_SYSD_TEST_CHILD";
const CHILD_ARGS: &str = "NX_SYSD_TEST_ARGS";
const HOLD_MS: &str = "NX_SYSD_TEST_HOLD_MS";
const HOLD_CONFIRM: &str = "NX_SYSD_TEST_HOLD_CONFIRM";
const MARKER: &str = "NX_SYSD_TEST_MARKER";
const UPDATE_DIR: &str = "NX_SYSD_TEST_UPDATE_DIR";
const ARG_SEPARATOR: char = '\u{1f}';
const CAPACITY: &str = "65536";
const TX: &str = "tx-d065";

/// Entry point of every child process; does nothing when run as an ordinary test.
#[test]
fn child_process_entry() {
    let Ok(mode) = std::env::var(CHILD_MODE) else {
        return;
    };
    let code = match mode.as_str() {
        "cli" => {
            let line = std::env::var(CHILD_ARGS).unwrap();
            let args: Vec<String> = std::iter::once("nx-sysd".to_string())
                .chain(line.split(ARG_SEPARATOR).map(str::to_string))
                .collect();
            let code = crate::run(&args);
            (0..=255u8)
                .find(|n| ExitCode::from(*n) == code)
                .map_or(1, i32::from)
        }
        "hold-lock" => hold_lock(),
        "watchdog" => watchdog(),
        other => panic!("unknown child mode {other}"),
    };
    let _ = std::io::stdout().flush();
    std::process::exit(code);
}

fn hold_lock() -> i32 {
    let dir = PathBuf::from(std::env::var(UPDATE_DIR).unwrap());
    let hold = Duration::from_millis(std::env::var(HOLD_MS).unwrap().parse().unwrap());
    let storage = FileJournalStorage::new(&dir);
    // The trial service may still be initializing the directory under the lock.
    let start = Instant::now();
    let lock = loop {
        match storage.try_lock_updater() {
            Ok(lock) => break lock,
            Err(_) if start.elapsed() < Duration::from_secs(10) => {
                std::thread::sleep(Duration::from_millis(10))
            }
            Err(e) => panic!("cannot take the updater lock: {e}"),
        }
    };
    if std::env::var(HOLD_CONFIRM).is_ok() {
        // The confirmation is recorded under the lock, which then stays held, so that the
        // trial service sees the confirmation while the lock is still busy.
        let mut journal = JournalController::load_existing(storage).unwrap().unwrap();
        journal
            .commit_with(|r| r.confirmed_attempt = r.consumed_attempts)
            .unwrap();
    }
    std::fs::write(std::env::var(MARKER).unwrap(), b"held").unwrap();
    std::thread::sleep(hold);
    std::fs::write(
        format!("{}.released", std::env::var(MARKER).unwrap()),
        b"released",
    )
    .unwrap();
    drop(lock);
    0
}

fn watchdog() -> i32 {
    let dir = PathBuf::from(std::env::var(UPDATE_DIR).unwrap());
    let mut backend = MockSlotBackend::new(Slot::B, Slot::A);
    // Uptime 0 makes the watchdog measure the budget with the monotonic clock.
    backend.uptime_secs = 0.0;
    let mut engine = UpdateEngine::new(
        backend,
        FileStagingStorage::new(&dir, CAPACITY.parse().unwrap()),
        FileJournalStorage::new(&dir),
        Some("test-hardware".to_string()),
        None,
        Some(CAPACITY.parse().unwrap()),
    )
    .unwrap();
    match engine.run_trial_watchdog(60, 50) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("watchdog error: {e}");
            if engine.backend.reboot_triggered {
                eprintln!("watchdog requested reboot");
            }
            2
        }
    }
}

/// Temporary test area with a selector directory and an update directory.
struct Area {
    root: PathBuf,
    update: PathBuf,
    select: PathBuf,
    slot: &'static str,
}

impl Area {
    fn new(name: &str, slot: &'static str) -> Self {
        let root = std::env::temp_dir().join(format!("nx-d065-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let select = root.join("select");
        std::fs::create_dir_all(&select).unwrap();
        let selector = if slot == "root_b" {
            "[all]\ntryboot_a_b=1\nboot_partition=2\n\n[tryboot]\nboot_partition=3\n"
        } else {
            "[all]\ntryboot_a_b=1\nboot_partition=2\n"
        };
        std::fs::write(select.join("autoboot.txt"), selector).unwrap();
        Self {
            update: root.join("update"),
            root,
            select,
            slot,
        }
    }

    fn command(&self, mode: &str) -> Command {
        let mut cmd = Command::new(std::env::current_exe().unwrap());
        cmd.args([
            "process_tests::child_process_entry",
            "--exact",
            "--nocapture",
            "--test-threads=1",
            "-q",
        ])
        .env(CHILD_MODE, mode)
        .env(UPDATE_DIR, &self.update)
        .env("NEXTOOMIO_SELECT_DIR", &self.select)
        .env("NEXTOOMIO_SLOT", self.slot)
        .env_remove("NEXTOOMIO_AUTOBOOT_TXT")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
        cmd
    }

    /// An `nx-sysd update <args> --dir <update> --capacity <CAPACITY> ...` command.
    fn update_cmd(&self, args: &[&str]) -> Command {
        let mut line: Vec<String> = vec!["update".to_string()];
        line.extend(args.iter().map(|a| a.to_string()));
        line.extend([
            "--dir".to_string(),
            self.update.display().to_string(),
            "--capacity".to_string(),
            CAPACITY.to_string(),
        ]);
        if args[0] != "status" {
            line.extend(["--hardware".to_string(), "test-hardware".to_string()]);
        }
        let mut cmd = self.command("cli");
        cmd.env(CHILD_ARGS, line.join(&ARG_SEPARATOR.to_string()));
        cmd
    }

    fn update(&self, args: &[&str]) -> Output {
        self.update_cmd(args).output().unwrap()
    }

    fn hold_lock(&self, hold_ms: u64, confirm: bool) -> (Child, PathBuf) {
        let marker = self.root.join(format!("held-{hold_ms}-{confirm}"));
        let mut cmd = self.command("hold-lock");
        cmd.env(HOLD_MS, hold_ms.to_string()).env(MARKER, &marker);
        if confirm {
            cmd.env(HOLD_CONFIRM, "1");
        }
        let child = cmd.spawn().unwrap();
        assert!(
            wait_until(Duration::from_secs(10), || marker.exists()),
            "lock holder did not report"
        );
        (child, marker)
    }

    fn journal(&self) -> Option<JournalRecord> {
        JournalController::load_existing(FileJournalStorage::new(&self.update))
            .unwrap()
            .map(|j| j.current().clone())
    }

    fn write_trial_record(&self) {
        let mut rec = JournalRecord::new_initial("root_b", "root_a");
        rec.state = UpdateTransactionState::Trial;
        rec.tx_id = Some(TX.to_string());
        rec.target_slot = Some("root_b".to_string());
        rec.payload_len = 4096;
        rec.payload_sha256 = Some("ab".repeat(32));
        rec.consumed_attempts = 1;
        rec.remote_confirm = true;
        rec.checksum = rec.compute_checksum();
        FileJournalStorage::new(&self.update)
            .write_record(0, &rec)
            .unwrap();
    }
}

impl Drop for Area {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn wait_until(timeout: Duration, mut cond: impl FnMut() -> bool) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if cond() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    cond()
}

fn stderr(out: &Output) -> String {
    String::from_utf8_lossy(&out.stderr).into_owned()
}

/// The JSON object a `--json` command printed; the test harness adds its own lines.
fn json(out: &Output) -> serde_json::Value {
    let text = String::from_utf8_lossy(&out.stdout);
    let start = text.find('{').expect("JSON output");
    let end = text.rfind('}').expect("JSON output");
    serde_json::from_str(&text[start..=end]).unwrap()
}

/// Every path below `dir` with its bytes (`None` for a directory).
fn snapshot(dir: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
    let mut out = BTreeMap::new();
    if !dir.exists() {
        return out;
    }
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for entry in std::fs::read_dir(&d).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                out.insert(path.clone(), None);
                stack.push(path);
            } else {
                out.insert(path.clone(), Some(std::fs::read(&path).unwrap()));
            }
        }
    }
    out
}

fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    h.hex_digest()
}

/// Starts `update stage --file /dev/stdin` and keeps the upload open after `first` bytes.
fn open_upload(area: &Area, first: &[u8]) -> Child {
    let mut cmd = area.update_cmd(&["stage", "--file", "/dev/stdin"]);
    cmd.stdin(Stdio::piped());
    let mut child = cmd.spawn().unwrap();
    let stdin = child.stdin.as_mut().unwrap();
    stdin.write_all(first).unwrap();
    stdin.flush().unwrap();
    assert!(
        wait_until(Duration::from_secs(20), || {
            area.journal()
                .is_some_and(|r| r.state == UpdateTransactionState::Receiving)
        }),
        "first upload did not reach receiving"
    );
    let staging = area.update.join("staging.swu");
    assert!(
        wait_until(Duration::from_secs(20), || {
            std::fs::read(&staging).is_ok_and(|b| b.starts_with(first))
        }),
        "first upload did not write its first bytes"
    );
    child
}

/// AC1: while the first upload is open, a second upload in another process is refused as
/// busy and changes nothing; the staged bytes and the journal are those of the first.
#[test]
fn ac1_second_upload_process_is_refused_while_the_first_is_open() {
    let area = Area::new("ac1", "root_a");
    let first_part = vec![0xA1u8; 3000];
    let rest = vec![0xA2u8; 2000];
    let second_file = area.root.join("second.swu");
    std::fs::write(&second_file, vec![0xB2u8; 4000]).unwrap();

    let mut first = open_upload(&area, &first_part);
    let receiving = area.journal().unwrap();
    let before = snapshot(&area.update);

    let second = area.update(&["stage", "--file", second_file.to_str().unwrap()]);
    assert_eq!(second.status.code(), Some(1), "{}", stderr(&second));
    assert!(
        stderr(&second).contains("update_busy"),
        "{}",
        stderr(&second)
    );
    assert_eq!(
        snapshot(&area.update),
        before,
        "the refused upload changed the update directory"
    );

    let mut stdin = first.stdin.take().unwrap();
    stdin.write_all(&rest).unwrap();
    drop(stdin);
    let out = first.wait_with_output().unwrap();
    assert!(out.status.success(), "{}", stderr(&out));

    let mut payload = first_part.clone();
    payload.extend_from_slice(&rest);
    let staged = area.journal().unwrap();
    assert_eq!(staged.state, UpdateTransactionState::Staged);
    assert_eq!(staged.generation, receiving.generation + 1);
    assert_eq!(staged.payload_len, payload.len() as u64);
    assert_eq!(staged.payload_sha256, Some(sha256_hex(&payload)));
    let staging = std::fs::read(area.update.join("staging.swu")).unwrap();
    assert_eq!(&staging[..payload.len()], payload.as_slice());
}

/// AC2: after the lock holder is killed, a new command obtains the lock and finds the
/// journal state recorded before the kill.
#[test]
fn ac2_killed_lock_holder_releases_the_lock_and_keeps_the_journal() {
    let area = Area::new("ac2", "root_a");
    let mut first = open_upload(&area, &[0xC3u8; 1500]);
    let before_kill = area.journal().unwrap();
    assert!(FileJournalStorage::new(&area.update)
        .try_lock_updater()
        .is_err());

    first.kill().unwrap();
    first.wait().unwrap();

    // The kernel released the lock with the process.
    drop(
        FileJournalStorage::new(&area.update)
            .try_lock_updater()
            .expect("lock released when its holder died"),
    );

    let status = area.update(&["status", "--json"]);
    assert!(status.status.success(), "{}", stderr(&status));
    assert_eq!(json(&status)["state"], "receiving");
    assert_eq!(area.journal().unwrap(), before_kill);

    // A state-changing command takes the lock and continues from the recorded state:
    // boot reconciliation ends the interrupted upload.
    let reconcile = area.update(&["init-staging"]);
    assert!(reconcile.status.success(), "{}", stderr(&reconcile));
    let after = area.journal().unwrap();
    assert_eq!(after.generation, before_kill.generation + 1);
    assert_eq!(after.state, UpdateTransactionState::Failed);
    assert_eq!(after.error_code.as_deref(), Some("update_truncated"));
}

/// AC3: `status` answers while another process holds the lock.
#[test]
fn ac3_status_answers_while_another_process_holds_the_lock() {
    let area = Area::new("ac3-held", "root_a");
    let init = area.update(&["init-staging"]);
    assert!(init.status.success(), "{}", stderr(&init));

    let (holder, _) = area.hold_lock(3000, false);
    let before = snapshot(&area.update);
    let started = Instant::now();
    let status = area.update(&["status", "--json"]);
    let took = started.elapsed();
    assert!(status.status.success(), "{}", stderr(&status));
    assert_eq!(json(&status)["state"], "idle");
    assert_eq!(json(&status)["initialized"], true);
    assert!(took < Duration::from_millis(2500), "status waited {took:?}");

    // Any other command is refused at once and changes nothing.
    let abort = area.update(&["abort"]);
    assert_eq!(abort.status.code(), Some(1));
    assert!(stderr(&abort).contains("update_busy"), "{}", stderr(&abort));
    assert_eq!(snapshot(&area.update), before);

    let out = holder.wait_with_output().unwrap();
    assert!(out.status.success());
}

/// AC3: `status` leaves an uninitialized update directory byte-for-byte unchanged.
#[test]
fn ac3_status_leaves_an_uninitialized_directory_unchanged() {
    let area = Area::new("ac3-uninit", "root_a");

    // A directory that does not exist is not created.
    let status = area.update(&["status", "--json"]);
    assert!(status.status.success(), "{}", stderr(&status));
    assert_eq!(json(&status)["initialized"], false);
    assert!(!area.update.exists());

    // An existing directory with a zero-filled journal reservation stays as it is.
    std::fs::create_dir_all(&area.update).unwrap();
    std::fs::write(area.update.join("journal.0"), vec![0u8; 4096]).unwrap();
    let before = snapshot(&area.update);
    for args in [&["status", "--json"][..], &["status"][..]] {
        let status = area.update(args);
        assert!(status.status.success(), "{}", stderr(&status));
        assert_eq!(snapshot(&area.update), before);
    }
    let text = area.update(&["status"]);
    assert!(String::from_utf8_lossy(&text.stdout).contains("State: not initialized"));
}

/// AC4: the trial service and a separate `update confirm` commit as real processes, and
/// `confirm` waits through a lock held briefly by a third process.
#[test]
fn ac4_watchdog_and_separate_confirm_process_commit() {
    let area = Area::new("ac4-confirm", "root_b");
    area.write_trial_record();
    let watchdog = area.command("watchdog").spawn().unwrap();
    assert!(wait_until(Duration::from_secs(20), || area
        .update
        .join("staging.meta")
        .exists()));

    let (holder, marker) = area.hold_lock(1000, false);
    let confirm = area.update(&["confirm", "--transaction", TX, "--wait", "30", "--json"]);
    assert!(confirm.status.success(), "{}", stderr(&confirm));
    assert!(
        stderr(&confirm).contains("Updater lock busy"),
        "{}",
        stderr(&confirm)
    );
    assert_eq!(json(&confirm)["state"], "committed");
    assert!(PathBuf::from(format!("{}.released", marker.display())).exists());

    let out = watchdog.wait_with_output().unwrap();
    assert!(out.status.success(), "{}", stderr(&out));
    assert!(holder.wait_with_output().unwrap().status.success());

    let rec = area.journal().unwrap();
    assert_eq!(rec.state, UpdateTransactionState::Committed);
    assert_eq!(rec.default_slot, "root_b");
    assert!(rec.is_confirmed());
}

/// AC4: a lock held by a third process when the trial service is ready to commit makes it
/// retry, not fail; it commits after the lock is released.
#[test]
fn ac4_watchdog_retries_while_a_third_process_holds_the_lock() {
    let area = Area::new("ac4-busy", "root_b");
    area.write_trial_record();
    let watchdog = area.command("watchdog").spawn().unwrap();
    assert!(wait_until(Duration::from_secs(20), || area
        .update
        .join("staging.meta")
        .exists()));
    // Let the service take its first polls while waiting for the confirmation.
    std::thread::sleep(Duration::from_millis(300));

    let (holder, marker) = area.hold_lock(1500, true);
    let released = PathBuf::from(format!("{}.released", marker.display()));
    // The confirmation is visible while the lock is held; the commit must wait.
    assert!(area.journal().unwrap().is_confirmed());
    std::thread::sleep(Duration::from_millis(500));
    assert!(!released.exists());
    assert_eq!(area.journal().unwrap().state, UpdateTransactionState::Trial);

    let out = watchdog.wait_with_output().unwrap();
    assert!(out.status.success(), "{}", stderr(&out));
    assert!(
        stderr(&out).contains("Updater lock busy"),
        "{}",
        stderr(&out)
    );
    assert!(released.exists());
    assert!(holder.wait_with_output().unwrap().status.success());

    let rec = area.journal().unwrap();
    assert_eq!(rec.state, UpdateTransactionState::Committed);
    assert_eq!(rec.default_slot, "root_b");
}
