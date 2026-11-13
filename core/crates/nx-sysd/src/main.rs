use nx_protocol::UpdateTransactionState;

fn main() {
    let initial_state = UpdateTransactionState::Idle;
    println!("nx-sysd v0.0.1-dev initialized (update_state={initial_state:?})");
}
