# Bench Record: Phase 0 Hardware Setup

Document version: **1.0**  
Date: **2026-11-16**  
Status: **Hardware form (pending physical hardware assembly)**  
Related specifications: [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1.

This document is the official bench record form for the nextoomio remote test bench, required by [ROADMAP.md](../../ROADMAP.md) section 2.1 and issue #31 (P0-F7.1). It provides the structured record for all seven mandatory components across models, revisions, interfaces, and electrical specifications. In accordance with the Architect's instructions for issue #31, all fields required by acceptance criterion AC1 are provided in this form and marked `not yet recorded` until populated from physical hardware assembly readings.

---

## 1. Bench Hardware Inventory

In accordance with acceptance criterion AC1 of issue #31, the table below lists the seven mandatory bench components with their models, revisions, interfaces, and specifications as an assembly record form.

| Item | Component | Model | Revision / Rating | Interface / Connection | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | **Board** | *not yet recorded* (reference target: Raspberry Pi 4 Model B per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1) | *not yet recorded* | 40-pin GPIO header, Gigabit Ethernet | not yet recorded (pending physical hardware assembly) |
| 2 | **Sound card** | *not yet recorded* (reference target: HiFiBerry Amp+ / `hifiberry-amp` per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3) | *not yet recorded* | 40-pin GPIO; DC power input | not yet recorded (pending physical hardware assembly) |
| 3 | **Speaker** | *not yet recorded* (reference target: passive test speaker per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1 and [ROADMAP.md](../../ROADMAP.md) section 2.1) | *not yet recorded* | Speaker terminals on sound card | not yet recorded (pending physical hardware assembly) |
| 4 | **Storage card** | *not yet recorded* (reference target: MicroSD card, minimum 16 GB per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6) | *not yet recorded* | MicroSD slot on board | not yet recorded (pending physical hardware assembly) |
| 5 | **Power supply** | *not yet recorded* (reference target: dedicated DC bench supply per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1) | *not yet recorded* | DC power connection to sound card | not yet recorded (pending physical hardware assembly) |
| 6 | **Network connection** | *not yet recorded* (reference target: wired Gigabit Ethernet `eth0` per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1) | *not yet recorded* | Ethernet cable to local network | not yet recorded (pending physical hardware assembly) |
| 7 | **Switched outlet** | *not yet recorded* (reference target: smart outlet controlled via `scripts/bench/power.sh` per [ROADMAP.md](../../ROADMAP.md) section 2.1) | *not yet recorded* | AC mains outlet to bench power supply | not yet recorded (pending physical hardware assembly) |

---

## 2. Component Details and Physical Specifications

### 2.1 Board (Raspberry Pi 4 Model B)

- **Model:** *not yet recorded* (reference target: Raspberry Pi 4 Model B per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1)
- **SoC:** *not yet recorded*
- **RAM Capacity:** *not yet recorded*
- **Board Revision:** *not yet recorded*
- **Board Serial:** *not yet recorded*
- **Effective OTP Revision:** *not yet recorded*
- **MAC Address (`eth0`):** *not yet recorded*
- **Bootloader:** *not yet recorded* (Raspberry Pi EEPROM firmware with `tryboot` A/B selection support per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6)

### 2.2 Sound Card (HiFiBerry Amp+)

- **Model:** *not yet recorded* (reference target: HiFiBerry Amp+, profile ID `hifiberry-amp`, display name "HiFiBerry Amp", overlay `hifiberry-amp` per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3)
- **DAC/Amplifier Chipset:** *not yet recorded*
- **ALSA Card Identifier:** *not yet recorded*
- **PCB Silkscreen Revision:** *not yet recorded*
- **Sampling Capabilities:** *not yet recorded* (reference target: 48 kHz graph clock, S24_LE or S32_LE per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.2)
- **Hardware Mixer:** *not yet recorded* (reference target: mixer control `Master` per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3; default local volume 30% per [SPECIFICATION.md](../../SPECIFICATION.md) section 5.1 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3)

### 2.3 Passive Speaker

- **Model:** *not yet recorded* (reference target: passive test speaker per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1 and [ROADMAP.md](../../ROADMAP.md) section 2.1)
- **Impedance:** *not yet recorded*
- **Power Handling:** *not yet recorded*
- **Connection:** *not yet recorded* (reference target: wired directly to speaker terminals on the sound card)

### 2.4 Storage Card

- **Model:** *not yet recorded*
- **Speed Class:** *not yet recorded*
- **Capacity:** *not yet recorded* (requires a card of at least 16 GB per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6)
- **Partition Layout (MBR layout from [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 and section 4):**

| # | Label | Type | Size (ARCHITECTURE.md section 3.6) | Content | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | `select` | FAT | 64 MiB | `autoboot.txt`, optional SSH key file (AD-23), mounted read-only at runtime | Defined in layout |
| 2 | `boot_a` | FAT | 256 MiB | Firmware files, `config.txt`, kernel, device trees, overlays for slot A | Defined in layout |
| 3 | `boot_b` | FAT | 256 MiB | Same for slot B | Defined in layout |
| 4 | - | extended container | Remainder | Extended partition container holding logical partitions 5–7 | Defined in layout |
| 5 | `root_a` | ext4, read-only | 3 GiB | System slot A | Defined in layout |
| 6 | `root_b` | ext4, read-only | 3 GiB | System slot B | Defined in layout |
| 7 | `data` | ext4 | Remainder, grown on first boot | Persistent writable configuration and data ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.5) | Defined in layout |

### 2.5 Power Supply

- **Model:** *not yet recorded*
- **Input Voltage:** *not yet recorded*
- **Output Voltage & Current:** *not yet recorded* (reference target: dedicated DC supply powering the board through the sound card per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3)
- **Connector:** *not yet recorded*

### 2.6 Network Connection

- **Interface:** *not yet recorded* (reference target: built-in wired Gigabit Ethernet controller `eth0` per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1)
- **Cabling:** *not yet recorded*
- **IP Configuration:** *not yet recorded* (reference target: Ethernet/DHCP per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.2)
- **Remote Access:** *not yet recorded* (reference target: key-only OpenSSH server active when key file is present on partition 1 per [ROADMAP.md](../../ROADMAP.md) section 2.1 and decision AD-23 in [ARCHITECTURE.md](../../ARCHITECTURE.md) section 2 and section 4.1)

### 2.7 Switched Outlet

- **Model:** *not yet recorded*
- **Driver / Control Protocol:** *not yet recorded* (supported drivers: `http`, `shelly`, `tasmota`, `mock`)
- **Control Script:** `scripts/bench/power.sh` (wraps `scripts/bench/power.py`, AC2)
- **Credentials & Addressing:** Loaded strictly from uncommitted local file (`scripts/bench/outlet.json` or `~/.config/nextoomio/outlet.json`), complying with AC3 and rule CQ-R6

---

## 3. Power Wiring and Interconnection Diagram

```
[ AC Mains Wall Socket ]
          │
          ▼
[ Switched Outlet (Smart Plug / PDU) ]  <─── Control: scripts/bench/power.sh (via HTTP)
          │ (AC Power)
          ▼
[ Dedicated DC Bench Power Supply ]
          │ (DC Power Connection)
          ▼
┌─────────────────────────────────────────────────────────┐
│ HiFiBerry Amp+ Sound Card                                │
│   - DC Power Input                                      │
│   - Amplifier Stage                                     │
│   - Speaker Terminals ────► [ Passive Test Speaker ]    │
└──────────────────────────┬──────────────────────────────┘
                           │ (40-pin GPIO bus: audio & power)
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Raspberry Pi 4 Model B                                  │
│   - Wired Ethernet (eth0) ──► [ Local Bench Network ]   │
│   - MicroSD Card ──────────► [ Storage Card (A/B) ]     │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Bench Operation Safety Rules (OQ-14)

In accordance with [docs/engineering/agent-team.md](../engineering/agent-team.md) section 9 (decision OQ-14):

1. **Permitted without asking project owner:**
   - Read-only diagnostics over SSH (`uptime`, `uname -a`, `dmesg`, `/proc/asound/cards`, `vcgencmd`).
   - Deploying images through the update engine.
   - Rebooting via software (`reboot`).
   - Switching the outlet via `scripts/bench/power.sh off|on|cycle` when the procedure requires it or to recover an unreachable device.
   - **Requirement:** Every outlet action must be recorded into the corresponding evidence file.

2. **Always requires explicit project owner approval beforehand:**
   - Raw writes to block devices (`dd` to `/dev/mmcblk0`).
   - Writes to the `select` partition outside the update engine.
   - Changes to the SSH key file (`authorized_keys`) or network configuration.
   - Data partition reset (`mkfs.ext4` on partition 7).
   - Any destructive action whose failure requires physical presence to recover.
   - Any network connection to a host other than the bench device and the switched outlet.
