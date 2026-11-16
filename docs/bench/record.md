# Bench Record: Phase 0 Hardware Setup

Document version: **1.0**  
Date: **2026-11-16**  
Status: **Specified baseline (pending physical hardware assembly)**  
Related specifications: [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1.

This document is the official bench record for the nextoomio remote test bench, required by [ROADMAP.md](../../ROADMAP.md) section 2.1 and issue #31 (P0-F7.1). It records the reference hardware inventory, component models, revisions, wiring topology, and network configuration. Per the project owner's decision of 2026-11-15, all Phase 0 software is implemented and verified first; specific physical board serial numbers and silkscreen production batch revisions are populated when the bench hardware is assembled.

---

## 1. Bench Hardware Inventory

In accordance with acceptance criterion AC1 of issue #31, the table below lists the seven mandatory bench components with their models, revisions, interfaces, and specifications.

| Item | Component | Model | Revision / Rating | Interface / Connection | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | **Board** | Raspberry Pi 4 Model B | Rev 1.4 / 1.5, 2GB / 4GB RAM | 40-pin GPIO header, Gigabit Ethernet | Reference hardware closed ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1) |
| 2 | **Sound card** | HiFiBerry Amp+ (selected as `hifiberry-amp`) | PCB Rev 1.0 (TAS5713 stereo DAC/amp) | Stamped on 40-pin GPIO; powers Pi from DC input | Reference hardware closed; confirmed from invoice 2026-11-13 |
| 3 | **Speaker** | Passive reference test speaker | 4–8 $\Omega$, 10W–25W continuous rating | Screw terminals on HiFiBerry Amp+ card | Required for tone listening check (P0-F6.3) |
| 4 | **Storage card** | MicroSD card | SanDisk Ultra 32GB microSDHC, UHS-I Class 10 / A1 | MicroSD slot on Raspberry Pi 4 | P0 partition layout: select, boot A/B, root A/B, data |
| 5 | **Power supply** | Dedicated DC bench power supply | 12V–18V DC, $\ge 3.0$A ($\ge 36$W–54W), center-positive | 5.5mm $\times$ 2.1mm barrel connector to Amp+ | Powers both Amp+ and Pi 4 via card ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1) |
| 6 | **Network connection** | Wired Ethernet connection | Gigabit Ethernet (`bcmgenet` / `eth0`) | Cat5e/Cat6 cable to local bench router/switch | Wired Ethernet with DHCP; workstation on same subnet |
| 7 | **Switched outlet** | Remotely switched AC smart outlet | HTTP/REST or RPC API (e.g., Shelly Plug S / Plus) | AC mains outlet controlling bench power supply | Controlled via `scripts/bench/power.sh` (AC2, AC3) |

---

## 2. Component Details and Physical Specifications

### 2.1 Board (Raspberry Pi 4 Model B)

- **Model:** Raspberry Pi 4 Model B
- **SoC:** Broadcom BCM2711, quad-core Cortex-A72 (ARM v8) 64-bit SoC @ 1.5 GHz
- **RAM:** 2 GB or 4 GB LPDDR4-3200 SDRAM
- **Board Revisions:** Model B Rev 1.4 (OTP `c03114` / `d03114`) or Rev 1.5
- **Bootloader:** Raspberry Pi EEPROM firmware with `tryboot` A/B selection support ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6)
- **Assembly Verification Field:**
  - Board Serial: *(to be populated on physical assembly via `/proc/cpuinfo`)*
  - Effective OTP Revision: *(to be populated on physical assembly)*
  - MAC Address (`eth0`): *(to be populated on physical assembly)*

### 2.2 Sound Card (HiFiBerry Amp+)

- **Model:** HiFiBerry Amp+ (recognized by kernel as `hifiberry-amp`, ALSA card ID `sndrpihifiberry`)
- **DAC/Amplifier Chipset:** Texas Instruments TAS5713 (digital input class-D stereo amplifier)
- **PCB Revision:** Rev 1.0 (confirmed from invoice on 2026-11-13; distinct from original Amp and Amp2 per [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1)
- **Sampling Capabilities:** Stereo 44.1 kHz / 48 kHz, S16_LE, S24_LE, S32_LE
- **Hardware Mixer:** ALSA `Master` playback volume control; fixed at safe 30% level during boot (D-012, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 5.3)
- **Power Delivery:** Back-powers the Raspberry Pi 4 through GPIO 5V pins (pin 2 & pin 4); external DC barrel jack is the sole power input for the entire device.

### 2.3 Passive Speaker

- **Model:** Passive reference bench speaker
- **Impedance:** 4 $\Omega$ to 8 $\Omega$ nominal
- **Power Handling:** 10 W to 25 W continuous RMS
- **Connection:** Wired directly to Left/Right screw terminals of the HiFiBerry Amp+
- **Protection:** All test tones generated for remote verification enforce $\le 30\%$ logical gain ($-31.37$ dBFS, peak amplitude $\le 885$ in S16_LE) to prevent speaker damage or unattenuated acoustic blasts (D-012, P0-F6.2).

### 2.4 Storage Card

- **Model:** SanDisk Ultra MicroSDHC 32 GB (or comparable Class 10 / A1 card)
- **Speed Class:** UHS-I Class 10 (U1), Video Speed Class V10, Application Performance Class A1
- **Capacity:** 32 GB (exceeds 8 GB minimum requirement for A/B layout, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1)
- **Partition Layout (P0):**
  - Partition 1 (`select`): 64 MB FAT, contains `autoboot.txt` selector and optional `authorized_keys`
  - Partition 2 (`boot_a`): 128 MB FAT, kernel & device tree for slot A
  - Partition 3 (`boot_b`): 128 MB FAT, kernel & device tree for slot B
  - Partition 4 (extended partition container)
  - Partition 5 (`root_a`): 2 GB ext4, read-only rootfs slot A
  - Partition 6 (`root_b`): 2 GB ext4, read-only rootfs slot B
  - Partition 7 (`data`): ext4, persistent writable configuration/data, grown on first boot to fill card

### 2.5 Power Supply

- **Model:** 12V–18V DC regulated bench power adapter
- **Input Voltage:** 100–240 V AC, 50/60 Hz
- **Output Voltage & Current:** 18.0 V DC @ 3.0 A (54 W) or 12.0 V DC @ 3.5 A (42 W), regulated
- **Connector:** 5.5 mm outer diameter $\times$ 2.1 mm inner diameter center-positive barrel plug
- **Safety Note:** The DC supply connects directly to the HiFiBerry Amp+ DC barrel jack. The Raspberry Pi 4 USB-C power port must NOT be connected simultaneously, as the Amp+ provides onboard regulation to power the Pi 4 via the GPIO bus.

### 2.6 Network Connection

- **Interface:** Built-in Broadcom Gigabit Ethernet controller (`eth0`)
- **Cabling:** Category 5e or Category 6 unshielded twisted pair (UTP) cable
- **Configuration:** DHCP client enabled on boot; static DHCP reservation configured on the bench router/switch
- **Subnet:** Dedicated local development network segment shared by the workstation and bench device
- **Remote Access:** Key-only OpenSSH server on port 22; active only when `authorized_keys` is staged on partition 1 (`select`) per decision AD-23 ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 2).

### 2.7 Switched Outlet

- **Model:** Network-connected switched power outlet / smart plug (e.g., Shelly Plug S / Plus Plug, Tasmota, or network PDU)
- **Control Interface:** Local HTTP/REST or JSON-RPC API
- **Power Capacity:** 230 V AC / 10 A–16 A
- **Control Script:** `scripts/bench/power.sh` (wraps `scripts/bench/power.py`)
- **Credentials & Addressing:** Loaded strictly from uncommitted local file (`scripts/bench/outlet.json` or `~/.config/nextoomio/outlet.json`), complying with AC3 and rule CQ-R6.

---

## 3. Power Wiring and Interconnection Diagram

```
[ AC Mains Wall Socket ]
          │
          ▼
[ Switched Outlet (Shelly / Smart Plug) ]  <─── Control: scripts/bench/power.sh (via HTTP)
          │ (AC Power)
          ▼
[ 18V / 3A DC Bench Power Supply ]
          │ (18V DC Barrel Jack)
          ▼
┌─────────────────────────────────────────────────────────┐
│ HiFiBerry Amp+ Sound Card                                │
│   - DC Power Input (18V)                                │
│   - TAS5713 Class-D Stereo Amp                          │
│   - Speaker Terminals ────► [ Passive Stereo Speaker ]  │
│   - 5V Power Delivery (GPIO Pins 2 & 4)                 │
└──────────────────────────┬──────────────────────────────┘
                           │ (40-pin GPIO bus: I2S audio & 5V power)
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Raspberry Pi 4 Model B (Rev 1.4/1.5)                    │
│   - Powered entirely via GPIO from Amp+                 │
│   - Wired Ethernet (eth0) ──► [ Local Bench Switch ]    │
│   - MicroSD Card ──────────► [ SanDisk 32GB (A/B) ]    │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Bench Operation Safety Rules (OQ-14)

In accordance with [docs/engineering/agent-team.md](../engineering/agent-team.md) section 9 (decision OQ-14):

1. **Permitted without asking project owner:**
   - Read-only diagnostics over SSH (`uptime`, `uname -a`, `dmesg`, `/proc/asound/cards`, `vcgencmd`).
   - Deploying images through the update engine (`scripts/bench/deploy.sh`).
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
