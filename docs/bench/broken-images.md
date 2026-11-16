# Broken Image Testing and Automatic Rollback Procedure

Document version: **0.2**  
Date: **2026-11-16**  
Status: **Draft / Static Specification (Runtime Rollback Unverified on Hardware)**  
Related rules and documents: [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [docs/bench/first-flash.md](first-flash.md), [docs/evidence/p0/P0-F4.7.md](../evidence/p0/P0-F4.7.md).

---

## 1. Goal and Overview

This document defines the procedure and build options for verifying that the three ways an update image can fail all end safely on the previous slot ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, **P0-F4.7**):

1. **AC1:** Kernel hangs before systemd starts (early init hang).
2. **AC2:** System boots into systemd but fails local health check prerequisites (PipeWire readiness and audio output).
3. **AC3:** System boots and is locally healthy but has no working network or SSH service (remote confirmation timeout).
4. **AC4:** Preserving the `/data` persistent partition across rollback scenarios.
5. **AC5:** Allowing immediate deployment and commit of a valid image after recovery.
6. **AC6:** Ensuring each test image can be rebuilt from a documented build option while keeping normal release images completely isolated from test fault injection.

---

## 2. Documented Build Options (AC6)

Each test image is built using a dedicated [kas](https://kas.readthedocs.io/) configuration file. None of the broken configurations is reachable from the normal release configuration (`kas/base.yml`, `kas/rpi4-64.yml`, or `meta-nextoomio/recipes-core/images/nextoomio-image.bb`):

| Test Case | Build Command | Image Recipe | Target Description |
| :--- | :--- | :--- | :--- |
| **AC1 Early Hang** | `kas build kas/test-broken-hang.yml` | `nextoomio-image-broken-hang.bb` | Kernel/init hang before systemd starts |
| **AC2 Failing Health** | `kas build kas/test-broken-health.yml` | `nextoomio-image-broken-health.bb` | Boots systemd but breaks required health check inputs |
| **AC3 Unreachable Net** | `kas build kas/test-broken-network.yml` | `nextoomio-image-broken-network.bb` | Boots locally healthy without network/SSH services |

### 2.1 Isolation Invariant

- Release builds invoked via `kas build kas/rpi4-64.yml` or `kas build kas/ci-hosted.yml` target `nextoomio-image`.
- `nextoomio-image.bb` contains **no** test fault packages.
- Fault package `nextoomio-test-faults-hang` in `meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/` is only pulled into `nextoomio-image-broken-hang.bb` via `IMAGE_INSTALL:append`.
- The health and network test images (`nextoomio-image-broken-health.bb` and `nextoomio-image-broken-network.bb`) perform fault injection exclusively via `ROOTFS_POSTPROCESS_COMMAND` service masking, without modifying base package groups.

---

## 3. Failure Mechanisms and Planned Rollback Behavior

### 3.1 AC1: Early Kernel / Init Hang before systemd

- **Mechanism:** The image recipe replaces `/sbin/init` with a symlink to `/sbin/nextoomio-hang-early`. When the Linux kernel finishes device initialization and mounts the root filesystem on the trial slot, it launches `/sbin/init` as PID 1. The script writes a marker banner to `/dev/console` and loops indefinitely without starting systemd.
- **Watchdog Dependency (AR-01):** Automatic board reset in this failure mode requires pre-systemd watchdog arming (`kernel_watchdog_timeout` or bootloader watchdog per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 AR-01 and spike S1 / #17). Because pre-systemd watchdog arming is not yet configured in the layer, this image currently halts early and would hang indefinitely until watchdog coverage is configured.
- **Planned Rollback Sequence (Once Watchdog Armed):**
  1. Boot attempt 1 resets via hardware watchdog timeout.
  2. Raspberry Pi firmware tryboot reboots into the trial slot for attempt 2.
  3. Attempt 2 hangs and resets via watchdog.
  4. Two attempts are consumed. The tryboot selection falls back to the previous good slot (slot A).
  5. The previous slot boots cleanly, mounts `/data`, starts SSH, and marks the transaction as `failed`.

### 3.2 AC2: Failing Local Health Check Prerequisites

- **Mechanism:** The image recipe masks `pipewire.service` and `nextoomio-audio-profile.service` to `/dev/null` in rootfs via `ROOTFS_POSTPROCESS_COMMAND`. This directly breaks the local health check prerequisites specified in [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 step 5 (PipeWire readiness and configured ALSA audio output initialization).
- **Planned Health Check Evaluation:**
  1. The trial slot boots into systemd `multi-user.target`.
  2. The health check monitor detects that PipeWire is not ready and audio hardware is not configured.
  3. The 120-second boot-to-health budget expires or health failure is flagged.
  4. If attempt count $< 2$, a second trial attempt is executed.
  5. After attempt 2 fails, the updater reverts the selector to the previous good slot.
  6. The previous slot boots, provides SSH access, and reports transaction status `failed`.

### 3.3 AC3: Unreachable Network or No SSH Service

- **Mechanism:** The image recipe masks `NetworkManager.service`, `sshd.service`, and `nextoomio-ssh-keys.service` to `/dev/null` in rootfs via `ROOTFS_POSTPROCESS_COMMAND`.
- **Planned Behavior:**
  1. The local system boots completely healthy: kernel initializes, systemd reaches `multi-user.target`, `/data` mounts, and ALSA audio initializes. Local health check passes.
  2. From the developer workstation, network connectivity (`eth0`) and SSH on port 22 are unreachable because no networking daemon or SSH server is running.
  3. Under remote confirmation mode (AD-22, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 step 6), the trial slot waits for remote confirmation from the workstation within the 120-second trial deadline.
  4. Because no SSH session can connect to confirm the update, the 120-second trial timer expires.
  5. The board cancels the trial, switches the selector back to the previous good slot, and reboots.
  6. The previous slot boots with fully functional network and SSH, and reports transaction status `failed`.

---

## 4. Verification and Data Preservation Procedures

### 4.1 AC4: Data Partition Integrity

Before each test deployment on bench hardware:
```sh
ssh root@nextoomio.local 'echo "pre-update-data-checkpoint" > /data/rollback_verification_marker.txt'
```
After the device recovers to the previous slot from any of the three failure modes:
```sh
ssh root@nextoomio.local 'cat /data/rollback_verification_marker.txt'
```
The file content must be identical, and all `/data/config/` schemas must remain unmodified ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.5).

### 4.2 AC5: Immediate Valid Image Deployment

Following recovery from each failure case on bench hardware:
1. Verify the updater is in terminal state `failed` on the active slot:
   ```sh
   ssh root@nextoomio.local 'cat /run/nextoomio/diagnostics/update.json'
   ```
2. Upload and install a valid standard release image (`nextoomio-image.bb` built via `kas build kas/rpi4-64.yml`).
3. Deploy into the inactive slot and reboot into trial.
4. Verify local health check passes and confirm the update.
5. Verify slot commit is successful and the new image becomes the permanent default.

---

## 5. Automated Static Validation Suite

To run automated static validation across recipes, kas build configurations, and isolation invariants:
```sh
python3 scripts/bench/test_broken_images.py --test
```

To validate the Yocto layer metadata, recipe syntax, and build option isolation:
```sh
python3 scripts/check_yocto_layer.py --test
make check
```

Note: Runtime rollback execution, watchdog reset timing, and slot switching require a physical bench session on hardware with SWUpdate and are tracked in the evidence file [docs/evidence/p0/P0-F4.7.md](../evidence/p0/P0-F4.7.md).
