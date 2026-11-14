# nextoomio

**nextoomio** is an open-source, cloud-free audio player and multiroom operating system for **Raspberry Pi 3+** and **HiFiBerry** sound cards, built on **Yocto Linux**.

> **Project status:** Specification (`v1.15`), cumulative delivery roadmap (`v1.10`), and system architecture (`v0.10`) are established. Repository scaffolding, core state-machine contracts, WebUI i18n catalog foundation, Yocto layer structure, and CI/CD pipelines are initialized for **v0.0.1** development. Hardware measurements and full Yocto target builds are not yet verified on physical hardware.

---

## Core Capabilities (Planned Series `v0.0.1–v0.0.5`)

- **Cloud-Free Local Operation:** Full local WebUI, REST/WebSocket API, and native synchronized multiroom operate without a project cloud.
- **Standalone & Network Inputs:** Local files (`WAV/PCM`, `FLAC`, `MP3`, `AAC/M4A`, `Ogg Vorbis`), read-only NAS (`SMB 2/3`, `NFSv4`), Internet Radio (`HTTP/HTTPS`, `M3U/PLS`), Spotify Connect, AirPlay 2, Bluetooth A2DP sink, and Music Assistant endpoint.
- **Deterministic Playback Arbitration:** Single shared playback-session manager per device (`SEL-01–SEL-07`). Last newly playing input wins; connections without playback and stale session callbacks never seize the output.
- **Native Multiroom & Reference Volume:** One master and up to 3 satellites with authenticated control and generation-bound audio tunnels, optional central volume control (`V = G × R / 100`) with per-satellite stored reference volume `R`, and ownership-scoped mute.
- **Music Assistant Isolation:** Native multiroom never redistributes Music Assistant streams (`MA-01–MA-08`); local takeover affects only the local endpoint.
- **Home Assistant Voice Layer:** USB microphone voice satellite (`VOICE-01–VOICE-08`) operating as a temporary local audio layer (`-20 dB` local ducking after the multiroom distribution branch) without altering playback ownership or group synchronization.
- **Automation & Control:** REST + WebSocket API, MQTT (`TLS`), follow-up actions (up to 10 ordered local/HTTP/MQTT actions per event with causal loop protection), and Matter speaker control.
- **Read-Only System & Manual A/B Updates:** Read-only root/boot slots with separate `/data` partition (`v<N>` versioned schemas for rollback safety), manual `.swu` image upload, continuous hardware watchdog handoff, and two-attempt trial boot recovery.

---

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete system design.

```text
 browser / API / MQTT / Matter / HA / MA
                   │
                   ▼
           ┌───────────────┐
           │ nextoomio-core│  (Rust policy daemon: session manager, router, API, update coordinator)
           └───┬───────┬───┘
               │       └──► nx-sysd (privileged helper: slots, mounts, nftables, hardware profile)
               ▼
        PipeWire Audio Graph (48 kHz / 24-bit path, explicit core-owned links)
  [in.files | in.spotify | in.airplay | in.bluetooth] ──► [music.bus] ──► [mr.send] (native multiroom)
  [in.master | in.ma] ─────────────────────────────────────────┤
                                                               ▼
                                                        [local.music] (nx-audio: stage 1 volume/mute, stage 2 voice ducking)
                                                               ▼
  [voice.out] ──────────────────────────────────────────► [out.mix] ──► ALSA (HiFiBerry)
```

---

## Repository Layout

| Path | Description | License |
| --- | --- | --- |
| [SPECIFICATION.md](SPECIFICATION.md) | Authoritative functional specification (`v1.15`, 78 requirement IDs) | Apache-2.0 |
| [ROADMAP.md](ROADMAP.md) | Cumulative delivery plan and release gates (`v0.0.1–v0.0.5`) | Apache-2.0 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical architecture, component register, and traceability | Apache-2.0 |
| [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) | Architecture review dispositions (`AR-01–AR-08`) | Apache-2.0 |
| [AGENTS.md](AGENTS.md) | Repository rules, language policy, and licensing guidelines | Apache-2.0 |
| [`core/`](core/) | Rust workspace: `nx-protocol`, `nextoomio-core`, `nx-audio`, `nx-sysd` | Apache-2.0 |
| [`matter/`](matter/) | `nx-matter` daemon workspace (`v0.0.5`) | Apache-2.0 |
| [`webui/`](webui/) | Vue 3 + TypeScript + `vue-i18n` single-page application | Apache-2.0 |
| [`meta-nextoomio/`](meta-nextoomio/) | OpenEmbedded / Yocto 6.0 LTS layer, images, Wic A/B layout, hardware profiles | MIT (metadata) |
| [`kas/`](kas/) | `kas` configuration files for reproducible Yocto builds (local & CI) | Apache-2.0 |
| [`docs/`](docs/) | OpenAPI specification, hardware compatibility matrix, and spike reports | Apache-2.0 |
| [`scripts/`](scripts/) | Automated traceability, i18n catalog, and layer verification tools | Apache-2.0 |
| [`SK/`](SK/) | Deprecated, frozen historical Slovak archive (do not modify) | Historical |
| [docs/backlog/](docs/backlog/README.md) | Conventions for features, stories, and defects, which are tracked as [GitHub issues](https://github.com/nextoo-ai/nextoomio/issues) | Apache-2.0 |

---

## Release Roadmap Summary

| Release | Scope | Status |
| --- | --- | --- |
| **v0.0.1** | Standalone Yocto player for **Raspberry Pi 4 + HiFiBerry Amp+** (`hifiberry-amp`), local files, HTTP/HTTPS radio, API, English catalog-based WebUI, manual A/B updates, backup/restore | In progress (scaffolding & S1 prep) |
| **v0.0.2** | Read-only NAS (`SMB 2/3`, `NFSv4`), WebUI Wi-Fi config, native multiroom (1 master + up to 3 satellites), central volume & reference volumes | Planned |
| **v0.0.3** | Spotify Connect, AirPlay 2, Bluetooth A2DP sink, Music Assistant endpoint, full arbitration & group isolation | Planned |
| **v0.0.4** | MQTT (`TLS`), events & up to 10 follow-up actions, Home Assistant Voice satellite with local ducking layer | Planned |
| **v0.0.5** | Matter speaker endpoint, full Raspberry Pi 3+ / common HiFiBerry matrix verification, complete regression | Planned |

---

## Quick Verification Commands

```bash
# Verify 78 requirement IDs across SPECIFICATION.md, ROADMAP.md, and ARCHITECTURE.md
python3 scripts/check_traceability.py

# Verify WebUI translation catalogs (en.json + pseudo-dev.json) and key rules
node scripts/check_i18n.mjs

# Run Rust workspace checks and unit tests (requires Rust toolchain)
cargo test --manifest-path core/Cargo.toml
```

---

## Licensing

Original code and documentation are licensed under the [Apache License 2.0](LICENSE) (see also [NOTICE](NOTICE)). Yocto layer metadata under `meta-nextoomio/` is licensed under the [MIT License](meta-nextoomio/COPYING.MIT). Built system images are aggregate works containing third-party components under their own licenses (see [ARCHITECTURE.md](ARCHITECTURE.md), sections 10–11).
