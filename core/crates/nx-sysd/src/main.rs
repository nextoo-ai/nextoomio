//! Minimal privileged operations helper binary (`nx-sysd`).
//!
//! Exposes device identity, image versioning, persistent device ID,
//! and update staging, validation, and status operations over CLI
//! for Phase 0 bench and SSH operation (AD-20, UPD-04, UPD-05, AC1-AC4).

use nx_sysd::engine::DEFAULT_STAGING_RESERVATION;
use nx_sysd::identity::{
    get_or_create_device_id_with_path, query_device_identity, HardwareIdentity, VersionInfo,
    DEFAULT_DEVICE_ID_PATH,
};
use nx_sysd::journal::FileJournalStorage;
use nx_sysd::system::{FileStagingStorage, PhysicalSlotBackend, SlotBackend};
use nx_sysd::{UpdateEngine, UpdateEngineError};
use std::env;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const DEFAULT_UPDATE_DIR: &str = "/data/update";

fn print_usage() {
    println!(
        "nx-sysd: nextoomio privileged helper daemon\n\n\
        Usage:\n  \
        nx-sysd [COMMAND] [OPTIONS]\n\n\
        Identity commands:\n  \
        nx-sysd identity                                 Print complete device identity as JSON\n  \
        nx-sysd version [--json]                         Print image version, commit, build ID, and slot\n  \
        nx-sysd hardware                                 Print board model and hardware compatibility JSON\n  \
        nx-sysd init-device-id                           Initialize persistent device ID on /data\n\n\
        Update commands:\n  \
        nx-sysd update init-staging [--dir <path>] [--capacity <bytes>]\n  \
        nx-sysd update stage --file <path> [--declared-length <bytes>] [--dir <path>]\n  \
        nx-sysd update validate [--dir <path>] [--hardware <id>] [--schema <n>]\n  \
        nx-sysd update install [--dir <path>] [--hardware <id>] [--schema <n>] [--capacity <bytes>]\n  \
        nx-sysd update trial [--dir <path>] [--no-reboot] [--json]\n  \
        nx-sysd update health-check [--dir <path>] [--json]\n  \
        nx-sysd update trial-watchdog [--dir <path>] [--deadline <secs>] [--poll-interval <ms>]\n  \
        nx-sysd update status [--dir <path>] [--json]\n  \
        nx-sysd update abort [--dir <path>]\n\n\
        Options for update trial:\n  \
        --no-reboot         Arm trial boot without triggering immediate reboot\n"
    );
}

fn print_identity_json() -> ExitCode {
    match query_device_identity() {
        Ok(identity) => match serde_json::to_string_pretty(&identity) {
            Ok(json) => {
                println!("{json}");
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("Error serializing device identity: {e}");
                ExitCode::from(1)
            }
        },
        Err(e) => {
            eprintln!("Error querying device identity: {e}");
            ExitCode::from(1)
        }
    }
}

fn handle_version(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    match query_device_identity() {
        Ok(identity) => {
            if want_json {
                let v = VersionInfo {
                    image_version: identity.image_version.clone(),
                    source_commit: identity.source_commit.clone(),
                    build_id: identity.build_id,
                    running_slot: identity.running_slot.clone(),
                };
                match serde_json::to_string_pretty(&v) {
                    Ok(json) => {
                        println!("{json}");
                        ExitCode::SUCCESS
                    }
                    Err(e) => {
                        eprintln!("Error serializing version info: {e}");
                        ExitCode::from(1)
                    }
                }
            } else {
                println!(
                    "nextoomio {} ({}, build {}, slot {})",
                    identity.image_version,
                    identity.source_commit,
                    identity.build_id,
                    identity.running_slot
                );
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("Error querying version: {e}");
            ExitCode::from(1)
        }
    }
}

fn handle_hardware() -> ExitCode {
    match query_device_identity() {
        Ok(identity) => {
            let hw = HardwareIdentity {
                board_model: identity.board_model,
                hardware_compatibility: identity.hardware_compatibility,
            };
            match serde_json::to_string_pretty(&hw) {
                Ok(json) => {
                    println!("{json}");
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("Error serializing hardware identity: {e}");
                    ExitCode::from(1)
                }
            }
        }
        Err(e) => {
            eprintln!("Error querying hardware identity: {e}");
            ExitCode::from(1)
        }
    }
}

fn handle_init_device_id() -> ExitCode {
    match get_or_create_device_id_with_path(Path::new(DEFAULT_DEVICE_ID_PATH)) {
        Ok(id) => {
            eprintln!("nextoomio device-id initialized: {id}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("Failed to initialize device ID: {e}");
            ExitCode::from(1)
        }
    }
}

fn get_engine(
    update_dir: &Path,
    hardware: Option<String>,
    schema: Option<u32>,
    capacity: Option<u64>,
) -> Result<UpdateEngine<PhysicalSlotBackend, FileStagingStorage, FileJournalStorage>, String> {
    let backend = PhysicalSlotBackend;
    let cap = capacity.unwrap_or(DEFAULT_STAGING_RESERVATION);
    let storage = FileStagingStorage::new(update_dir, cap);
    let journal_storage = FileJournalStorage::new(update_dir);

    UpdateEngine::new(
        backend,
        storage,
        journal_storage,
        hardware,
        schema,
        Some(cap),
    )
    .map_err(|e| format!("engine initialization error: {e}"))
}

fn handle_update(args: &[String]) -> ExitCode {
    if args.is_empty() {
        print_usage();
        return ExitCode::from(1);
    }

    let subcmd = args[0].as_str();
    let mut update_dir = PathBuf::from(DEFAULT_UPDATE_DIR);
    let mut capacity = DEFAULT_STAGING_RESERVATION;
    let mut hardware = None;
    let mut schema = None;
    let mut file_path = None;
    let mut declared_length = None;
    let mut json_output = false;
    let mut no_reboot = false;
    let mut deadline_secs = 120u64;
    let mut poll_interval_ms = 1000u64;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--dir" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --dir");
                    return ExitCode::from(1);
                }
                update_dir = PathBuf::from(&args[i + 1]);
                i += 2;
            }
            "--capacity" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --capacity");
                    return ExitCode::from(1);
                }
                match args[i + 1].parse::<u64>() {
                    Ok(c) => capacity = c,
                    Err(e) => {
                        eprintln!("Error: invalid value for --capacity '{}': {e}", args[i + 1]);
                        return ExitCode::from(1);
                    }
                }
                i += 2;
            }
            "--file" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --file");
                    return ExitCode::from(1);
                }
                file_path = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--declared-length" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --declared-length");
                    return ExitCode::from(1);
                }
                match args[i + 1].parse::<u64>() {
                    Ok(len) => declared_length = Some(len),
                    Err(e) => {
                        eprintln!(
                            "Error: invalid value for --declared-length '{}': {e}",
                            args[i + 1]
                        );
                        return ExitCode::from(1);
                    }
                }
                i += 2;
            }
            "--hardware" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --hardware");
                    return ExitCode::from(1);
                }
                hardware = Some(args[i + 1].clone());
                i += 2;
            }
            "--schema" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --schema");
                    return ExitCode::from(1);
                }
                match args[i + 1].parse::<u32>() {
                    Ok(s) => schema = Some(s),
                    Err(e) => {
                        eprintln!("Error: invalid value for --schema '{}': {e}", args[i + 1]);
                        return ExitCode::from(1);
                    }
                }
                i += 2;
            }
            "--json" => {
                json_output = true;
                i += 1;
            }
            "--no-reboot" => {
                no_reboot = true;
                i += 1;
            }
            "--deadline" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --deadline");
                    return ExitCode::from(1);
                }
                match args[i + 1].parse::<u64>() {
                    Ok(d) => deadline_secs = d,
                    Err(e) => {
                        eprintln!("Error: invalid value for --deadline '{}': {e}", args[i + 1]);
                        return ExitCode::from(1);
                    }
                }
                i += 2;
            }
            "--poll-interval" => {
                if i + 1 >= args.len() {
                    eprintln!("Error: missing value for --poll-interval");
                    return ExitCode::from(1);
                }
                match args[i + 1].parse::<u64>() {
                    Ok(p) => poll_interval_ms = p,
                    Err(e) => {
                        eprintln!(
                            "Error: invalid value for --poll-interval '{}': {e}",
                            args[i + 1]
                        );
                        return ExitCode::from(1);
                    }
                }
                i += 2;
            }
            unknown => {
                eprintln!("Error: unknown option '{unknown}'");
                return ExitCode::from(1);
            }
        }
    }

    match subcmd {
        "init-staging" => {
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };
            if let Err(err) = engine.reconcile_boot() {
                eprintln!("Reconciliation error: {err}");
                return ExitCode::from(1);
            }
            let status = engine.status();
            match serde_json::to_string_pretty(&status) {
                Ok(j) => println!("{j}"),
                Err(e) => eprintln!("Serialization error: {e}"),
            }
            ExitCode::SUCCESS
        }
        "stage" => {
            let file = match file_path {
                Some(p) => p,
                None => {
                    eprintln!("Error: --file argument is required for stage");
                    return ExitCode::from(1);
                }
            };

            let f = match File::open(&file) {
                Ok(f) => f,
                Err(e) => {
                    eprintln!("Error opening {}: {e}", file.display());
                    return ExitCode::from(1);
                }
            };

            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.stage_upload(f, declared_length) {
                Ok(status) => {
                    match serde_json::to_string_pretty(&status) {
                        Ok(j) => println!("{j}"),
                        Err(e) => eprintln!("Serialization error: {e}"),
                    }
                    ExitCode::SUCCESS
                }
                Err(err) => {
                    eprintln!("Stage failed: {err}");
                    let status = engine.status();
                    let _ = serde_json::to_string_pretty(&status).map(|j| eprintln!("{j}"));
                    ExitCode::from(1)
                }
            }
        }
        "validate" => {
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.validate() {
                Ok(status) => {
                    match serde_json::to_string_pretty(&status) {
                        Ok(j) => println!("{j}"),
                        Err(e) => eprintln!("Serialization error: {e}"),
                    }
                    ExitCode::SUCCESS
                }
                Err(err) => {
                    eprintln!("Validation failed: {err}");
                    let mut status = engine.status();
                    status.error_code = Some(err.error_code_str().to_string());
                    status.message = Some(err.to_string());
                    let _ = serde_json::to_string_pretty(&status).map(|j| eprintln!("{j}"));
                    ExitCode::from(1)
                }
            }
        }
        "install" => {
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.install() {
                Ok(status) => {
                    match serde_json::to_string_pretty(&status) {
                        Ok(j) => println!("{j}"),
                        Err(e) => eprintln!("Serialization error: {e}"),
                    }
                    ExitCode::SUCCESS
                }
                Err(err) => {
                    eprintln!("Install failed: {err}");
                    let mut status = engine.status();
                    status.error_code = Some(err.error_code_str().to_string());
                    status.message = Some(err.to_string());
                    let _ = serde_json::to_string_pretty(&status).map(|j| eprintln!("{j}"));
                    ExitCode::from(1)
                }
            }
        }
        "trial" => {
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.request_trial(!no_reboot) {
                Ok(status) => {
                    if json_output {
                        match serde_json::to_string_pretty(&status) {
                            Ok(j) => println!("{j}"),
                            Err(e) => eprintln!("Serialization error: {e}"),
                        }
                    } else {
                        println!(
                            "{}",
                            if no_reboot {
                                "Trial boot armed; reboot deferred"
                            } else {
                                "Trial boot armed, rebooting"
                            }
                        );
                    }
                    ExitCode::SUCCESS
                }
                Err(err) => {
                    eprintln!("Trial request failed: {err}");
                    let mut status = engine.status();
                    status.error_code = Some(err.error_code_str().to_string());
                    status.message = Some(err.to_string());
                    let _ = serde_json::to_string_pretty(&status).map(|j| eprintln!("{j}"));
                    ExitCode::from(1)
                }
            }
        }
        "health-check" => {
            let engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.check_health() {
                Ok(report) => {
                    if json_output {
                        match serde_json::to_string_pretty(&report) {
                            Ok(j) => println!("{j}"),
                            Err(e) => eprintln!("Serialization error: {e}"),
                        }
                    } else {
                        println!("Healthy: {}", report.healthy);
                        if let Some(ref d) = report.details {
                            println!("Details: {d}");
                        }
                    }
                    if report.healthy {
                        ExitCode::SUCCESS
                    } else {
                        ExitCode::from(1)
                    }
                }
                Err(err) => {
                    eprintln!("Health check error: {err}");
                    ExitCode::from(1)
                }
            }
        }
        "trial-watchdog" => {
            let backend = PhysicalSlotBackend;
            let active_slot = match backend.get_active_slot() {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Error getting active slot: {e}");
                    return ExitCode::from(1);
                }
            };
            let default_slot = match backend.get_default_slot() {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Error getting default slot: {e}");
                    return ExitCode::from(1);
                }
            };

            // If active slot is default slot, this is not a trial boot
            if active_slot == default_slot {
                return ExitCode::SUCCESS;
            }

            // We are running on a non-default slot (trial boot)!
            match get_engine(&update_dir, hardware.clone(), schema, Some(capacity)) {
                Ok(mut engine) => {
                    match engine.run_trial_watchdog(deadline_secs, poll_interval_ms) {
                        Ok(()) => ExitCode::SUCCESS,
                        Err(UpdateEngineError::TrialDeadlineExceeded) => {
                            eprintln!("Trial deadline exceeded; triggered reboot to default slot");
                            ExitCode::from(2)
                        }
                        Err(err) => {
                            eprintln!("Trial watchdog error: {err}");
                            let mut b = backend;
                            let _ = b.trigger_reboot();
                            ExitCode::from(1)
                        }
                    }
                }
                Err(err) => {
                    eprintln!(
                        "Cannot initialize update engine (e.g. /data not mounted: {err}); arming fallback watchdog"
                    );
                    let mut b = backend;
                    let initial_uptime = b.get_uptime_secs().unwrap_or(0.0);
                    let remaining = if initial_uptime < deadline_secs as f64 {
                        (deadline_secs as f64 - initial_uptime).ceil().max(1.0) as u64
                    } else {
                        1
                    };
                    if let Err(e) = b.arm_watchdog(remaining) {
                        eprintln!("Cannot arm fallback watchdog: {e}");
                        let _ = b.trigger_reboot();
                        return ExitCode::from(1);
                    }

                    let start = std::time::Instant::now();
                    loop {
                        let cur_uptime = b.get_uptime_secs().unwrap_or(0.0);
                        let elapsed = if cur_uptime >= initial_uptime && cur_uptime > 0.0 {
                            cur_uptime
                        } else {
                            initial_uptime + start.elapsed().as_secs_f64()
                        };
                        if elapsed >= deadline_secs as f64 {
                            eprintln!(
                                "Trial deadline exceeded without /data; rebooting to default slot"
                            );
                            let _ = b.trigger_reboot();
                            return ExitCode::from(2);
                        }

                        // Check if /data mounted and journal became available
                        if let Ok(mut engine) =
                            get_engine(&update_dir, hardware.clone(), schema, Some(capacity))
                        {
                            match engine.run_trial_watchdog(deadline_secs, poll_interval_ms) {
                                Ok(()) => return ExitCode::SUCCESS,
                                Err(UpdateEngineError::TrialDeadlineExceeded) => {
                                    return ExitCode::from(2);
                                }
                                Err(e) => {
                                    eprintln!("Watchdog error: {e}");
                                    let _ = b.trigger_reboot();
                                    return ExitCode::from(1);
                                }
                            }
                        }
                        if let Err(e) = b.feed_watchdog() {
                            eprintln!("Cannot feed fallback watchdog: {e}");
                            let _ = b.trigger_reboot();
                            return ExitCode::from(1);
                        }
                        std::thread::sleep(std::time::Duration::from_millis(
                            poll_interval_ms.clamp(50, 1000),
                        ));
                    }
                }
            }
        }
        "status" => {
            let engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            let status = engine.status();
            if json_output {
                match serde_json::to_string_pretty(&status) {
                    Ok(j) => println!("{j}"),
                    Err(e) => eprintln!("Serialization error: {e}"),
                }
            } else {
                println!(
                    "State: {:?}\nActive Slot: {}\nDefault Slot: {}\nPayload: {} bytes (Reservation: {} bytes)",
                    status.state,
                    status.active_slot,
                    status.default_slot,
                    status.payload_length,
                    status.reserved_capacity
                );
                if let Some(ref tx) = status.tx_id {
                    println!("Transaction ID: {tx}");
                }
                if let Some(ref err) = status.error_code {
                    println!("Error Code: {err}");
                }
            }
            ExitCode::SUCCESS
        }
        "abort" => {
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.abort() {
                Ok(status) => {
                    match serde_json::to_string_pretty(&status) {
                        Ok(j) => println!("{j}"),
                        Err(e) => eprintln!("Serialization error: {e}"),
                    }
                    ExitCode::SUCCESS
                }
                Err(err) => {
                    eprintln!("Abort failed: {err}");
                    ExitCode::from(1)
                }
            }
        }
        unknown => {
            eprintln!("Unknown update subcommand '{unknown}'. Use 'nx-sysd update' without arguments for usage.");
            ExitCode::from(1)
        }
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_usage();
        return ExitCode::SUCCESS;
    }

    match args[1].as_str() {
        "identity" => print_identity_json(),
        "version" => handle_version(&args[2..]),
        "hardware" => handle_hardware(),
        "init-device-id" => handle_init_device_id(),
        "update" => handle_update(&args[2..]),
        "help" | "--help" | "-h" => {
            print_usage();
            ExitCode::SUCCESS
        }
        unknown => {
            eprintln!("Unknown command '{unknown}'. Use 'nx-sysd help' for usage.");
            ExitCode::from(1)
        }
    }
}
