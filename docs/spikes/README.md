# Feasibility Spikes (`S1–S6`)

This directory tracks the technical feasibility spikes required by [ROADMAP.md](../../ROADMAP.md) (section 3) and [ARCHITECTURE.md](../../ARCHITECTURE.md) (section 5.4).

> **Rule:** A documented design is not spike completion. Each spike report must record exact pinned component versions, reproducible setup instructions, measured hardware/protocol outputs, failure tests, and the selected scope-preserving path.

| Spike | Target Area | Dependent Gate | Status |
| --- | --- | --- | --- |
| **S1** | `tryboot` / U-Boot A/B selection, continuous bootloader→kernel→systemd watchdog (`AR-01`), slot/staging size budgets (`AR-08`), power-loss commit on Pi 3B+, Pi 4, Pi 5 | `v0.0.1` | Pending execution |
| **S2** | Snapcast vs. Sendspin (`OD-03`) electrical skew (`≤ 2 ms`), PipeWire drift interaction, authenticated WSS tunnel & TLS exporter proofs (`AR-02`, `AR-03`), group-mute isolation (`AR-04`) | `v0.0.2` | Pending execution |
| **S3** | Shairport Sync 5.x + NQPTP, librespot capture-sink confinement (`R-10`), BlueZ A2DP admission without AVRCP player state (`AR-06`) | `v0.0.3` | Pending execution |
| **S4** | `sendspin-cpp` (`AD-07`) Music Assistant endpoint lifecycle, intent vs. stream separation, player-local `client/leave` barrier (`AR-05`) | `v0.0.3` | Pending execution |
| **S5** | OHF Linux Voice Assistant + `microWakeWord` (`Apache-2.0` models) through PipeWire echo-cancel and stage-2 `-20 dB` ducking | `v0.0.4` | Pending execution |
| **S6** | `rs-matter` Speaker commissioning and stable group-mute endpoint with central volume disabled (`AR-07`) | `v0.0.5` | Pending execution |
