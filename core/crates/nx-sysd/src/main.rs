//! Minimal privileged operations helper binary (`nx-sysd`).
//!
//! Exposes device identity, image versioning, persistent device ID,
//! and update staging, validation, and status operations over CLI
//! for Phase 0 bench and SSH operation (AD-20, UPD-04, UPD-05, AC1-AC4).

mod identity;

use identity::{
    get_or_create_device_id_with_path, query_device_identity, HardwareIdentity, VersionInfo,
    DEFAULT_DEVICE_ID_PATH,
};
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
        nx-sysd [COMMAND] [OPTIONS]\n\n\
        Identity commands:\n  \
        nx-sysd identity                                 Print complete device identity as JSON\n  \
        nx-sysd version [--json]                         Print image version, commit, build ID, and slot\n  \
        nx-sysd hardware                                 Print board model and hardware compatibility JSON\n  \
        nx-sysd device-id [--json]                       Print random persistent device ID\n  \
        nx-sysd init-device-id                           Initialize persistent device ID on /data\n  \
        nx-sysd daemon                                   Run privileged operations helper (stub)\n\n\
        Update commands:\n  \
        nx-sysd update init-staging [--dir <path>] [--capacity <bytes>]\n  \
        nx-sysd update stage --file <path> [--declared-length <bytes>] [--dir <path>]\n  \
        nx-sysd update validate [--dir <path>] [--hardware <id>] [--schema <n>]\n  \
        nx-sysd update install [--dir <path>] [--hardware <id>] [--schema <n>] [--capacity <bytes>]\n  \
        nx-sysd update status [--dir <path>] [--json]\n  \
        nx-sysd update abort [--dir <path>]\n"
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
                    version: identity.version.clone(),
                    source_commit: identity.source_commit.clone(),
                    commit: identity.commit.clone(),
                    build_id: identity.build_id,
                    running_slot: identity.running_slot.clone(),
                    slot: identity.slot.clone(),
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

fn handle_device_id(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    match get_or_create_device_id_with_path(Path::new(DEFAULT_DEVICE_ID_PATH)) {
        Ok(dev_id) => {
            if want_json {
                println!("{{\"device_id\": \"{dev_id}\"}}");
            } else {
                println!("{dev_id}");
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("Error obtaining device ID: {e}");
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

fn handle_daemon() -> ExitCode {
    let initial_state = nx_protocol::UpdateTransactionState::Idle;
    println!(
        "nx-sysd v0.0.1-dev stub (daemon mode not yet implemented, update_state={initial_state:?})"
    );
    ExitCode::SUCCESS
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
    let prog_name = args.first().map(|s| s.as_str()).unwrap_or("nx-sysd");

    // Check if invoked via nx-identity symlink
    if prog_name.ends_with("nx-identity") {
        return print_identity_json();
    }

    if args.len() < 2 {
        print_usage();
        return ExitCode::SUCCESS;
    }

    match args[1].as_str() {
        "identity" | "--identity" => print_identity_json(),
        "version" | "--version" | "-V" => handle_version(&args[2..]),
        "hardware" | "--hardware" => handle_hardware(),
        "device-id" | "--device-id" => handle_device_id(&args[2..]),
        "init-device-id" => handle_init_device_id(),
        "daemon" | "--daemon" => handle_daemon(),
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
