# One-command deploy tool

Document version: **0.1**  
Date: **2026-11-16**  
Status: **Implemented (P0-F5.1)**  
Related rules: [AGENTS.md](../../AGENTS.md), [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, section 4.1.

## 1. Goal

Provide developers with a single command (`scripts/bench/deploy.py`) on their workstation to deploy a built `.swu` system update file to a reachable bench device, bringing it to a confirmed running state without manual intervention (P0-F5.1, [ROADMAP.md](../../ROADMAP.md) section 2.1).

## 2. Architecture and design

The tool automates the Phase 0 remote deployment flow described in [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1:

```
 workstation ── deploy ──► SSH (enabled by key file, AD-23) ──► update engine ──► inactive boot/root slot
      ▲                                                                        │ one-shot trial boot
      └────────── confirm within trial budget ◄── health check ◄───────────────┘   no confirmation: previous slot
 bench: Raspberry Pi 4 + HiFiBerry Amp+
```

### 2.1 Security baseline (AC6, AD-23)

- **No credentials stored:** The deploy tool stores no passwords, tokens, or private keys on disk.
- **No key material printed:** Standard output and error streams never display private keys or credential material.
- **System SSH & agent integration:** Authentication uses the workstation's system `ssh` binary and active `ssh-agent` (`SSH_AUTH_SOCK`) with the bench key (OQ-14). Batch mode (`BatchMode=yes`) is enforced to prevent interactive password prompts.

### 2.2 Sequence of operations (AC1, AC2)

1. **Pre-check and initial state discovery:**
   - Validates that the local `.swu` file exists and is readable.
   - Parses `sw-description` inside the `.swu` container to determine the expected target release version and hardware compatibility.
   - Connects to the bench device over SSH to determine the currently active slot (`root_a` or `root_b`) and running version (`nx-sysd version --json` or `/etc/nextoomio-release`).
   - Determines the target inactive slot (`root_b` if running on `root_a`, or `root_a` if running on `root_b`).
2. **Upload and staging:**
   - Streams the `.swu` update archive to `/data/update/staging.upload.swu` on the device.
   - Invokes `nx-sysd update stage --file ...` to write the payload into the preallocated staging area (`/data/update/staging.swu`, UPD-05, AR-08) and cleans up intermediate upload files.
3. **Validation:**
   - Invokes `nx-sysd update validate` over SSH.
   - If validation fails, aborts immediately, reports the failing step as `validate`, and suppresses remote confirmation (AC3).
4. **Inactive slot installation:**
   - Invokes `nx-sysd update install` to write the validated boot and root artifacts to the inactive slot.
   - If installation fails, aborts immediately, reports the failing step as `install`, and suppresses confirmation (AC3).
5. **Arm trial boot:**
   - Invokes `nx-sysd update trial --remote-confirm` to arm the tryboot selector and request remote-confirmed commit mode (AD-22).
   - Issues `systemctl reboot` to initiate the one-shot trial boot.
6. **Wait for device return:**
   - Polls SSH reachability at configured intervals (`--poll-interval`, default 2.0s) until the device responds or the trial timeout (`--timeout`, default 120s per UPD-06) expires.
   - If the device fails to return within the timeout, exits non-zero, names the failing step as `wait_for_device`, and sends no confirmation (AC3).
7. **Verification of running slot and version:**
   - Queries `nx-sysd version --json` and `nx-sysd update status --json` on the returned device.
   - **Rollback detection (AC4):** If the device booted back into the previous slot, reports rollback, outputs the update failure state from the device journal, exits non-zero, and sends no confirmation.
   - **Version verification (AC3):** If the running version does not match the expected target version, reports version mismatch, exits non-zero, and sends no confirmation.
8. **Remote confirmation (AC1, AD-22):**
   - Sends `nx-sysd update confirm` over SSH within the trial budget.
   - Verifies the update state transition to `committed`.
9. **Final summary and timing report (AC2):**
   - Exits with status zero.
   - Prints previous version, new version, running slot, and elapsed time of each step.

### 2.3 Slot alternation (AC5)

Deploying twice sequentially with the same (or new) image alternates slots automatically:
- First deploy: starts on slot A (`root_a`), installs into slot B (`root_b`), trial boots and commits slot B.
- Second deploy: starts on slot B (`root_b`), installs into slot A (`root_a`), trial boots and commits slot A.

## 3. CLI usage

### 3.1 Synopsis

```bash
python3 scripts/bench/deploy.py <device-host> <update-file.swu> [options]
```

Positional arguments may be given in either order.

### 3.2 Options

| Option | Default | Description |
| --- | --- | --- |
| `-d`, `--device` | – | Bench device hostname or IP address. |
| `-f`, `--file` | – | Path to the `.swu` update file. |
| `-u`, `--user` | `root` | SSH user for bench access. |
| `-p`, `--port` | `22` | SSH port on target device. |
| `--expected-version` | auto | Explicit target version (detected from `sw-description` if omitted). |
| `--timeout` | `120.0` | Timeout in seconds for device reboot and return. |
| `--poll-interval` | `2.0` | Interval in seconds between SSH reachability checks. |
| `--no-remote-confirm` | `false` | Do not request remote confirmation (rely on local health check alone). |
| `--json` | `false` | Output machine-readable JSON deploy summary. |
| `-v`, `--verbose` | `false` | Print progress details to stderr during execution. |
| `--test` | – | Run automated unit test suite and exit. |

### 3.3 Example output

```text
============================================================
Deploy Succeeded
============================================================
Previous Version:  0.0.1-dev-4054510
New Version:       0.0.1-dev-e89dbcf
Running Slot:      root_b
Final State:       committed
Confirmation Sent: Yes
------------------------------------------------------------
Step Elapsed Times:
  precheck           0.42s
  upload             3.18s
  validate           1.05s
  install            4.50s
  start_trial        0.80s
  wait_for_device   14.20s
  check_running      0.35s
  confirm            0.51s
  total             25.01s
============================================================
```
