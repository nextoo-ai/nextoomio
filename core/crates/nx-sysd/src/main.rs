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
        nx-sysd update status [--dir <path>] [--json]\n  \
        nx-sysd update abort [--dir <path>]\n"
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

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--dir" => {
                if i + 1 < args.len() {
                    update_dir = PathBuf::from(&args[i + 1]);
                    i += 1;
                }
            }
            "--capacity" => {
                if i + 1 < args.len() {
                    if let Ok(c) = args[i + 1].parse::<u64>() {
                        capacity = c;
                    }
                    i += 1;
                }
            }
            "--file" => {
                if i + 1 < args.len() {
                    file_path = Some(PathBuf::from(&args[i + 1]));
                    i += 1;
                }
            }
            "--declared-length" => {
                if i + 1 < args.len() {
                    if let Ok(len) = args[i + 1].parse::<u64>() {
                        declared_length = Some(len);
                    }
                    i += 1;
                }
            }
            "--hardware" => {
                if i + 1 < args.len() {
                    hardware = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--schema" => {
                if i + 1 < args.len() {
                    if let Ok(s) = args[i + 1].parse::<u32>() {
                        schema = Some(s);
                    }
                    i += 1;
                }
            }
            "--json" => {
                json_output = true;
            }
            _ => {}
        }
        i += 1;
    }

    match subcmd {
        "init-staging" => {
            let engine = match get_engine(&update_dir, hardware, schema, Some(capacity)) {
                Ok(e) => e,
                Err(err) => {
                    eprintln!("Error: {err}");
                    return ExitCode::from(1);
                }
            };
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
