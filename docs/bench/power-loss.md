# Power-Loss Resilience Verification Procedure

Document version: **0.1**  
Date: **2026-11-16**  
Status: **Adopted / Automated Tooling Implemented (Hardware Execution Pending)**  
Related rules and documents: [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [docs/bench/first-flash.md](first-flash.md), [docs/bench/soak.md](soak.md), [docs/evidence/p0/P0-F4.8.md](../evidence/p0/P0-F4.8.md).

---

## 1. Overview and Purpose

In accordance with [ROADMAP.md](../../ROADMAP.md) section 2.1 exit gates and requirements SYS-05, UPD-05, and UPD-06, the nextoomio system must survive losing electrical power at any step of an update transaction without bricking or requiring physical site intervention.

This document defines the automated power-loss resilience verification procedure (**P0-F4.8**), executing controlled power interruptions via a remotely switched AC power outlet across four distinct phases of the update sequence on the reference Raspberry Pi 4 bench device:
1. **Upload Phase:** Power removed while the system update container (`.swu`) is streaming to `/data/update/staging.upload.swu`.
2. **Install Phase:** Power removed while writing raw rootfs and boot partition contents to the inactive system slot.
3. **Trial Boot Phase:** Power removed during the trial boot window before commit confirmation.
4. **Commit Phase:** Power removed during the selector commit operation when updating `autoboot.txt` and the durable transaction journal.

The automated tooling (`scripts/bench/power_loss.py`) orchestrates the power cuts using seeded pseudo-random delays to vary the exact moment of interruption across at least ten trials per case (40 total trials), verifying post-recovery boot, state consistency, zero physical access interventions, and subsequent update deployment capability.

---

## 2. Acceptance Criteria Coverage

| Criterion | Requirement Summary | Implementation & Verification |
| :--- | :--- | :--- |
| **AC1** | Power removed through switched outlet during upload, inactive slot writing, trial boot, and commit; in every case device boots, is reachable, and runs a complete image. | `PowerLossRunner` triggers power cuts across all 4 phases via `PowerController`. Upon power restoration, verifies device boots within timeout (120s), answers SSH, runs systemd PID 1, and mounts rootfs read-only on the active slot. |
| **AC2** | Update state read afterwards is consistent with running slot: interrupted upload/install is not installable or bootable, interrupted trial consumed its attempt, interrupted commit is completed or returned to previous slot, never undefined. | `_verify_ac2_state_consistency()` queries `nx-sysd update status --json` after reboot and checks state machine invariants: interrupted upload/install resets to `idle`/`failed` with inactive slot unbootable; interrupted trial increments `trial_attempts`; interrupted commit is atomically committed or rolled back, never undefined. |
| **AC3** | Each case repeated at least ten times at varied moments; none needed physical access; any that did is recorded and blocks phase exit. | 10 repetitions per case (40 total) executed with seeded random delays generated via `random.Random(seed)` over phase-specific delay windows. Physical access count is tracked; any non-zero value marks the run as NON-GATE / FAILED. |
| **AC4** | Given power loss in any case, when a new valid update is deployed afterwards, then it succeeds. | Following recovery and consistency verification in each trial, `_execute_subsequent_deploy()` deploys a valid `.swu` to completion with remote confirmation (`nx-sysd update confirm`), verifying active slot alternation and durable commit. |

---

## 3. Prerequisites and Equipment

### Hardware Setup

- **Reference Board:** Raspberry Pi 4 Model B ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1).
- **Sound Card:** HiFiBerry Amp+ powered by regulated 12V–18V DC power supply ([docs/bench/first-flash.md](first-flash.md)).
- **Network:** Wired Ethernet connection (`eth0`) with DHCP, on the same network subnet as the test workstation.
- **Switched Power Outlet:** Remotely controllable AC power outlet or smart plug (e.g., Shelly, Tasmota, or network PDU) feeding the 12V–18V power adapter.
- **SSH Authentication:** Dedicated bench key loaded in workstation SSH agent (`ssh-ed25519`, AD-23).

### Image Artifacts

A valid built system update image (`.swu`) compatible with `raspberrypi4-64`, containing signed or validated `sw-description` metadata and boot/root filesystem payloads.

---

## 4. Phase-Specific Invariants and Failure Mechanics

In accordance with [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6:

### 1. Interrupted Upload (Staging)

- **Mechanics:** The update payload streams over SSH into `/data/update/staging.upload.swu`. Power is cut mid-stream.
- **Expected Invariant:**
  - Upon reboot, `/data` filesystem journal recovers cleanly.
  - Incomplete upload artifact is discarded; transaction metadata is invalidated.
  - Active running slot remains the original slot ($S_{orig}$).
  - Inactive slot ($S_{target}$) remains unbootable and untouched.
  - Device update state is `idle` or `failed`; never `staged`, `installed`, `trial`, or `committed`.

### 2. Interrupted Install (Partition Writing)

- **Mechanics:** Update container is validated and `nx-sysd update install` begins writing raw image streams to inactive boot and root partitions (`boot_b`/`root_b` or `boot_a`/`root_a`). Power is cut while blocks are being written.
- **Expected Invariant:**
  - Firmware default boot selector still points to $S_{orig}$.
  - Upon reboot, system boots cleanly into $S_{orig}$.
  - Inactive slot holds corrupted/partial data and must not be trial-bootable.
  - Device update state reports `failed` or invalidated transaction.

### 3. Interrupted Trial Boot

- **Mechanics:** Update is installed to inactive slot $S_{target}$, trial boot is armed (`nx-sysd update trial --remote-confirm`), and reboot is initiated. Power is cut while trial kernel/systemd boots or during trial runtime before remote confirmation is received.
- **Expected Invariant:**
  - Raspberry Pi firmware `tryboot` mechanism is one-shot: cold power-cycling after trial arming falls back to default slot ($S_{orig}$) unless explicitly re-armed.
  - Durable attempt counter is incremented (`trial_attempts >= 1`).
  - If attempt limit (2 attempts per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 UPD-06) is reached, state transitions to `failed` and system stays on $S_{orig}$.
  - Active running slot is $S_{orig}$ (or $S_{target}$ if trial attempt allows), holding a complete healthy image.

### 4. Interrupted Commit

- **Mechanics:** Trial slot is running and healthy. `nx-sysd update commit` is invoked to durably switch the default boot selector on the FAT partition (`autoboot.txt`) and write `committed` to the transaction journal. Power is cut during this write.
- **Expected Invariant:**
  - Two-state durable resolution: either the selector write and journal flush completed before power dropped (device boots $S_{target}$, state `committed`), or it did not complete atomically and falls back cleanly to previous good slot $S_{orig}$ (state rolled back/failed).
  - State is **never undefined**, corrupted, or unbootable.

---

## 5. Tooling and CLI Usage

Power-loss resilience tests are orchestrated via `scripts/bench/power_loss.py`.

```bash
# Full 4-case resilience run (10 trials per case, 40 total) with mock power controller
python3 scripts/bench/power_loss.py 192.168.1.50 \
  --swu build/nextoomio-image-raspberrypi4-64.swu \
  --mock-power \
  --trials 10 \
  --seed 42 \
  --report power-loss-report.md

# Real bench hardware execution using external power command
python3 scripts/bench/power_loss.py 192.168.1.50 \
  --swu build/nextoomio-image-raspberrypi4-64.swu \
  --power-cmd "scripts/bench/power.sh {action}" \
  --power-cycle-delay 5.0 \
  --trials 10 \
  --seed 42 \
  --timeout 120.0 \
  --poll-interval 2.0 \
  --report power-loss-report.md \
  --verbose

# Run specific phase subset (e.g. trial and commit only)
python3 scripts/bench/power_loss.py 192.168.1.50 \
  --swu build/nextoomio-image.swu \
  --cases trial commit \
  --trials 10 \
  --mock-power

# Automated self-tests (AC1–AC4 in-memory verification suite)
python3 scripts/bench/power_loss.py --test
```

---

## 6. Seeded Random Delay Algorithm

To satisfy AC3 ("repeated at least ten times at varied moments"), power cut timings are computed deterministically from a user-specified PRNG seed:

$$\Delta t_{case, i} = \text{round}\left(\text{uniform}(t_{min}, t_{max}), 3\right)$$

Default delay intervals $[t_{min}, t_{max}]$:
- `upload`: $[0.5, 4.0]$ seconds.
- `install`: $[1.0, 10.0]$ seconds.
- `trial`: $[2.0, 15.0]$ seconds.
- `commit`: $[0.05, 0.8]$ seconds.

The seed (default: `42`) and each trial's exact delay value are recorded in the verification report to allow complete auditability and reproducibility.

---

## 7. Acceptance Criteria Evaluation and Gate Rules

In accordance with [ROADMAP.md](../../ROADMAP.md) section 2.1:
1. **Zero Brick Guarantee:** Every power cut trial must recover automatically without requiring physical on-site intervention (e.g., reflashing SD card, serial console recovery). Any trial requiring physical access sets `physical_access_needed = True` and **blocks phase exit**.
2. **AC1 Image Integrity:** Recovered slot must be running Linux kernel, systemd PID 1, and read-only rootfs mount.
3. **AC2 State Machine Integrity:** The updater state must never be undefined or contradictory.
4. **AC4 Deployability:** The system must be fully receptive to a subsequent deployment following any power cut.
