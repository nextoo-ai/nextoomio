mod identity;

use identity::{get_or_create_device_id, query_device_identity, HardwareIdentity, VersionInfo};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let prog_name = args.first().map(|s| s.as_str()).unwrap_or("nx-sysd");

    // Check if invoked via nx-identity symlink
    let is_nx_identity = prog_name.ends_with("nx-identity");

    let subcmd = args.get(1).map(|s| s.as_str()).unwrap_or("");

    // Default or identity subcommand prints complete device identity as JSON (AC1, AC2, AC3)
    if is_nx_identity || subcmd == "identity" || subcmd == "--identity" || subcmd.is_empty() {
        let identity = query_device_identity();
        println!("{}", serde_json::to_string_pretty(&identity).unwrap());
        return;
    }

    match subcmd {
        "version" | "--version" | "-V" => {
            let want_json = args.iter().any(|a| a == "--json");
            let identity = query_device_identity();
            if want_json {
                let v = VersionInfo {
                    image_version: identity.image_version,
                    source_commit: identity.source_commit,
                    build_id: identity.build_id,
                    running_slot: identity.running_slot,
                };
                println!("{}", serde_json::to_string_pretty(&v).unwrap());
            } else {
                println!(
                    "nextoomio {} ({}, build {}, slot {})",
                    identity.image_version,
                    identity.source_commit,
                    identity.build_id,
                    identity.running_slot
                );
            }
        }
        "hardware" | "--hardware" => {
            let identity = query_device_identity();
            let hw = HardwareIdentity {
                board_model: identity.board_model,
                hardware_compatibility: identity.hardware_compatibility,
            };
            println!("{}", serde_json::to_string_pretty(&hw).unwrap());
        }
        "device-id" | "--device-id" => {
            let want_json = args.iter().any(|a| a == "--json");
            let dev_id = get_or_create_device_id();
            if want_json {
                println!("{{\"device_id\": \"{dev_id}\"}}");
            } else {
                println!("{dev_id}");
            }
        }
        "init-device-id" => {
            // One-shot initialization at boot (AC3)
            let id = get_or_create_device_id();
            eprintln!("nextoomio device-id initialized: {id}");
        }
        "help" | "--help" | "-h" => {
            println!("Usage: nx-sysd [COMMAND] [OPTIONS]");
            println!();
            println!("Commands:");
            println!("  identity          Print complete device identity as JSON (default)");
            println!("  version [--json]  Print image version, commit, build ID, and slot");
            println!("  hardware          Print board model and hardware compatibility JSON");
            println!("  device-id         Print random persistent device ID");
            println!("  init-device-id    Initialize persistent device ID on data partition");
            println!("  daemon            Run background privileged operations daemon");
        }
        "daemon" | "--daemon" => {
            let initial_state = nx_protocol::UpdateTransactionState::Idle;
            println!("nx-sysd v0.0.1-dev daemon running (update_state={initial_state:?})");
        }
        other => {
            eprintln!("Unknown command: '{other}'. Run 'nx-sysd --help' for usage.");
            std::process::exit(1);
        }
    }
}
