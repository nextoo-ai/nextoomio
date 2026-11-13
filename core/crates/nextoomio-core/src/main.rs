use nextoomio_core::SessionManager;

fn main() {
    let _manager = SessionManager::new("nextoomio-dev", [0u8; 16]);
    println!("nextoomio-core v0.0.1-dev initialized (idle, awaiting setup/reconciliation)");
}
