use nx_protocol::VolumePercent;

fn main() {
    // AD-18: Starts muted and applies gain only after nextoomio-core reconciles over Unix socket.
    let initial_gain = VolumePercent::SILENT.to_linear_gain();
    println!("nx-audio v0.0.1-dev initialized (muted, gain={initial_gain})");
}
