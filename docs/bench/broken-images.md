# Broken Image Testing and Automatic Rollback Procedure

Document version: **0.1**  
Date: **2026-11-16**  
Status: **Draft / Verified Statically**  
Related rules and documents: [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [docs/bench/first-flash.md](first-flash.md), [docs/evidence/p0/P0-F4.7.md](../evidence/p0/P0-F4.7.md).

---

## 1. Goal and Overview

This document defines the procedure and build options for verifying that the three ways an update image can fail all end safely on the previous slot ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, **P0-F4.7**):

1. **AC1:** Kernel hangs before systemd starts (early hang, hardware watchdog recovery).
2. **AC2:** System boots into systemd but fails the local health check.
3. **AC3:** System boots and is locally healthy but has no working network or no SSH service (remote confirmation timeout).
4. **AC4:** Preserving the `/data` persistent partition across all three rollback scenarios.
5. **AC5:** Allowing immediate deployment and commit of a valid image after recovery.
6. **AC6:** Ensuring each test image can be rebuilt from a documented build option while keeping normal release images completely isolated from test fault injection.

---

## 2. Documented Build Options (AC6)

Each test image is built using a dedicated [kas](https://kas.readthedocs.io/) configuration file. None of the broken configurations is reachable from the normal release configuration (`kas/base.yml`, `kas/rpi4-64.yml`, or `meta-nextoomio/recipes-core/images/nextoomio-image.bb`):

| Test Case | Build Command | Image Recipe | Target Description |
| :--- | :--- | :--- | :--- |
| **AC1 Early Hang** | `kas build kas/test-broken-hang.yml` | `nextoomio-image-broken-hang.bb` | Kernel/init hang before systemd starts |
| **AC2 Failing Health** | `kas build kas/test-broken-health.yml` | `nextoomio-image-broken-health.bb` | System boots but fails local health check |
| **AC3 Unreachable Net** | `kas build kas/test-broken-network.yml` | `nextoomio-image-broken-network.bb` | System boots locally healthy without network/SSH |

### 2.1 Isolation Invariant

- Release builds invoked via `kas build kas/rpi4-64.yml` or `kas build kas/ci-hosted.yml` target `nextoomio-image`.
- `nextoomio-image.bb` contains **no** test fault packages.
- Fault packages (`nextoomio-test-faults-hang`, `nextoomio-test-faults-health`, `nextoomio-test-faults-network`) in `meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/` are only pulled into their respective test image recipes via `IMAGE_INSTALL:append`.

---

## 3. Failure Mechanisms and Rollback Behavior

### 3.1 AC1: Early Kernel / Init Hang before systemd

- **Mechanism:** The image recipe replaces `/sbin/init` with a symlink to `/sbin/nextoomio-hang-early`. When the Linux kernel finishes device initialization and mounts the root filesystem on the trial slot, it launches `/sbin/init` as PID 1. The script writes a marker banner to `/dev/console` and blocks indefinitely in a loop without starting systemd.
- **Watchdog Trigger:** Because PID 1 never initializes systemd, systemd's watchdog pet pinging is never armed. The BCM2835 hardware watchdog armed during bootloader/kernel handoff (`kernel_watchdog_timeout` per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 continuous watchdog coverage) expires and resets the board.
- **Rollback Sequence:**
  1. Boot attempt 1 resets via hardware watchdog.
  2. Raspberry Pi firmware tryboot reboots into the trial slot for attempt 2.
  3. Attempt 2 hangs and resets via watchdog.
  4. Two attempts are consumed. The tryboot selection falls back to the previous good slot (slot A).
  5. The previous slot boots cleanly, mounts `/data`, starts SSH, and marks the transaction as `failed`.

### 3.2 AC2: Failing Local Health Check

- **Mechanism:** The image recipe installs `nextoomio-test-faults-health`, which includes `nextoomio-broken-health.service` running before `basic.target`. At boot, the service writes failure diagnostics to `/run/nextoomio/diagnostics/health.json` and `/run/nextoomio/diagnostics/health.txt`, touches `/etc/nextoomio/force-health-failure`, and exits with status 1.
- **Health Check Evaluation:** The local health check monitor in `nx-sysd` checks local service health against the 120-second trial budget ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 step 5). Upon detecting health check failure or budget expiration:
  1. The trial attempt is recorded as failed.
  2. If attempt count $< 2$, a second trial is executed.
  3. After attempt 2 fails, the updater reverts the selector to the previous good slot.
  4. The previous slot boots, provides SSH access, and reports transaction status `failed`.

### 3.3 AC3: Unreachable Network or No SSH Service

- **Mechanism:** The image recipe installs `nextoomio-test-faults-network` and masks `NetworkManager.service`, `sshd.service`, and `nextoomio-ssh-keys.service` to `/dev/null` in the rootfs via `ROOTFS_POSTPROCESS_COMMAND`, and installs `/etc/nftables.d/99-block-network-test.nft`.
- **Behavior:**
  - The local system boots completely healthy: kernel initializes, systemd reaches `multi-user.target`, `/data` mounts, and ALSA audio initializes. Local health check passes.
  - From the developer workstation, network access (`eth0`) and SSH on port 22 are unreachable.
  - Under remote confirmation mode (AD-22, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 step 6), the trial slot waits for remote confirmation from the workstation within the 120-second trial deadline.
  - Because no SSH session can connect to confirm the update, the 120-second trial timer expires.
  - The board cancels the trial, switches the selector back to the previous good slot, and reboots.
  - The previous slot boots with fully functional network and SSH, and reports transaction status `failed`.

---

## 4. Verification and Data Preservation

### 4.1 AC4: Data Partition Integrity

Before each test deployment, write verification data to the persistent data partition:
```sh
ssh root@nextoomio.local 'echo "pre-update-data-checkpoint" > /data/rollback_verification_marker.txt'
```
After the device recovers to the previous slot from any of the three failure modes:
```sh
ssh root@nextoomio.local 'cat /data/rollback_verification_marker.txt'
```
The file content must be identical, and all `/data/config/` schemas must remain unmodified ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.5).

### 4.2 AC5: Immediate Valid Image Deployment

Following recovery from each failure case:
1. Verify the updater is in terminal state `failed` on the active slot:
   ```sh
   ssh root@nextoomio.local 'cat /run/nextoomio/diagnostics/update.json'
   ```
2. Upload and install a valid standard release image (`nextoomio-image.bb` built via `kas build kas/rpi4-64.yml`).
3. Deploy into the inactive slot and reboot into trial.
4. Verify local health check passes and confirm the update.
5. Verify slot commit is successful and the new image becomes the permanent default.

---

## 5. Automated Verification Suite

To run automated static validation and state-machine simulation across all criteria:
```sh
python3 scripts/bench/test_broken_images.py --test
```

To run dry-run deployment simulation:
```sh
python3 scripts/bench/test_broken_images.py --dry-run
```

To validate the Yocto layer, recipes, and kas configurations:
```sh
python3 scripts/check_yocto_layer.py --test
make check
```
