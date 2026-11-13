# Hardware Compatibility Matrix (`SYS-01`, `SYS-02`)

> **Status notice:** No board or sound-card combination has been verified on physical hardware yet. A combination may be marked **Verified** only after passing the automated per-combination hardware checks defined in [SPECIFICATION.md](../../SPECIFICATION.md) (section 13.2) and [ARCHITECTURE.md](../../ARCHITECTURE.md) (section 9.2), with exact board revision, RAM size, and sound-card PCB revision recorded.

## 1. v0.0.1 Closed Reference Combination

| Board | Board Revision / RAM | Profile ID | Display Name (Volumio-aligned) | Physical Card | Card PCB Revision | Overlay | Hardware Mixer | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Raspberry Pi 4 Model B | *To record at v0.0.1 gate* | `hifiberry-amp` | HiFiBerry Amp | HiFiBerry Amp+ (powers Pi from amp supply) | *To record at v0.0.1 gate* | `hifiberry-amp` | `Master` | **Planned (v0.0.1 reference, unverified)** |

## 2. Target Raspberry Pi 3+ Board Family (`SYS-01`, `v0.0.2–v0.0.5`)

| Board Model | Architecture | Target Yocto Machine | Status |
| --- | --- | --- | --- |
| Raspberry Pi 4 Model B | `aarch64` | `raspberrypi4-64` | Planned (`v0.0.1` reference) |
| Raspberry Pi 3 Model B / B+ / A+ | `aarch64` | `raspberrypi3-64` / unified 64-bit family | Planned (`S1` & `v0.0.2–v0.0.5`) |
| Raspberry Pi 400 / CM4 (40-pin carrier) | `aarch64` | `raspberrypi4-64` | Planned (`v0.0.5`) |
| Raspberry Pi 5 | `aarch64` | `raspberrypi5` / unified 64-bit family | Planned (`S1` & `v0.0.5`) |

## 3. Candidate HiFiBerry Sound Cards (`SYS-02`, Volumio-aligned identifiers)

See [ARCHITECTURE.md](../../ARCHITECTURE.md) section 9.2 for the full 23-entry candidate profile table (`hifiberry-amp` through `hifiberry-digi`). All candidate profiles remain **Unverified** until measured on physical hardware.
