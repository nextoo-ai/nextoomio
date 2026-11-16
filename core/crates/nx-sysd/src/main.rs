//! Minimal privileged operations helper binary (`nx-sysd`).
//!
//! Exposes update staging, validation, and status operations over CLI
//! for Phase 0 bench and SSH operation (AD-20, UPD-04, UPD-05).

use nx_sysd::engine::DEFAULT_STAGING_RESERVATION;
use nx_sysd::journal::FileJournalStorage;
use nx_sysd::system::{FileStagingStorage, PhysicalSlotBackend};
use nx_sysd::UpdateEngine;
use std::env;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const DEFAULT_UPDATE_DIR: &str = "/data/update";

fn print_usage() {
    println!(
        "nx-sysd: nextoomio privileged helper daemon\n\n\
        Usage:\n  \
        nx-sysd update init-staging [--dir <path>] [--capacity <bytes>]\n  \
        nx-sysd update stage --file <path> [--declared-length <bytes>] [--dir <path>]\n  \
        nx-sysd update validate [--dir <path>] [--hardware <id>] [--schema <n>]\n  \
        nx-sysd update install [--dir <path>] [--hardware <id>] [--schema <n>] [--capacity <bytes>]\n  \
        nx-sysd update trial [--dir <path>] [--remote-confirm] [--no-reboot] [--json]\n  \
        nx-sysd update health-check [--dir <path>] [--json]\n  \
        nx-sysd update trial-watchdog [--dir <path>] [--deadline <secs>] [--poll-interval <ms>]\n  \
        nx-sysd update status [--dir <path>] [--json]\n  \
        nx-sysd update abort [--dir <path>]\n\n\
        Options for update trial:\n  \
        --remote-confirm    Require explicit remote confirmation before commit (deploy workflow)\n  \
        --no-reboot         Arm trial boot without triggering immediate reboot\n"
    );
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

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_usage();
        return ExitCode::SUCCESS;
    }

    match args[1].as_str() {
        "update" => handle_update(&args[2..]),
        "help" | "--help" | "-h" => {
            print_usage();
            ExitCode::SUCCESS
        }
        "version" => {
            println!("nx-sysd v0.0.1-dev");
            ExitCode::SUCCESS
        }
        unknown => {
            eprintln!("Unknown command '{unknown}'. Use 'nx-sysd help' for usage.");
            ExitCode::from(1)
        }
    }
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
    let mut remote_confirm = false;
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
            "--remote-confirm" => {
                remote_confirm = true;
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
                eprintln!("Error: unrecognized argument '{unknown}'");
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

            match engine.request_trial(remote_confirm, !no_reboot) {
                Ok(status) => {
                    if json_output {
                        match serde_json::to_string_pretty(&status) {
                            Ok(j) => println!("{j}"),
                            Err(e) => eprintln!("Serialization error: {e}"),
                        }
                    } else {
                        println!("Trial boot armed, rebooting");
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
            let mut engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };

            match engine.run_trial_watchdog(deadline_secs, poll_interval_ms) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("Trial watchdog error: {err}");
                    ExitCode::from(1)
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
