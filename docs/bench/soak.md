# Ten-Cycle Update Soak Procedure

Document version: **0.1**  
Date: **2026-11-16**  
Status: **Adopted / Automated Tooling Implemented (Hardware Execution Pending)**  
Related rules and documents: [ROADMAP.md](../../ROADMAP.md) section 2.1, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, [docs/evidence/p0/P0-F5.3.md](../evidence/p0/P0-F5.3.md).

---

## 1. Overview and Purpose

In accordance with [ROADMAP.md](../../ROADMAP.md) section 2.1 exit gates, the A/B update path must be demonstrated as repeatable and robust, rather than lucky. This document defines the automated ten-cycle update soak procedure (**P0-F5.3**), executing ten consecutive unattended system updates between two distinguishable valid images (.swu) on the reference Raspberry Pi 4 bench device.

The soak run exercises the complete update engine stack ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6):
- Uploading and staging update containers over SSH.
- Container payload signature and metadata validation (`nx-sysd update validate`).
- Writing raw rootfs and boot assets to the inactive partition slot (`nx-sysd update install`).
- Arming trial boot with remote confirmation requested (`nx-sysd update trial --remote-confirm`, AD-22).
- Waiting for system reboot and recovery into the trial slot within the 120-second trial budget (UPD-06).
- Verifying active slot alternation and version match.
- Sending remote confirmation (`nx-sysd update confirm`) and verifying durable transition to `committed`.
- Preserving persistent user and configuration data on the `/data` partition across all ten update cycles (AC3, [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.5).

---

## 2. Acceptance Criteria Coverage

The automated tooling (`scripts/bench/soak.py`) directly implements and verifies the following criteria:

| Criterion | Requirement Summary | Implementation & Verification |
| :--- | :--- | :--- |
| **AC1** | Ten alternating deploys all committed, slot toggles each cycle, version matches deployed image. | `SoakRunner` alternates between Image A and Image B across cycles 1..10. Each cycle verifies active slot alternation (`root_a` <-> `root_b`), version match against SWU metadata, and commit confirmation. |
| **AC2** | Report written with per-cycle step times and final state; unattended execution. | Generated Markdown/JSON/text report records `upload`, `validate`, `install`, `wait_for_device`, `confirm`, `marker_check`, and `cycle_total` durations. The tool executes non-interactively over SSH without prompts; verifying that nobody touched the device during the 10 cycles is verified on the bench. |
| **AC3** | Marker file written to `/data` before the run remains completely unchanged after 10 cycles. | `MarkerManager` writes a unique token with timestamp and SHA-256 checksum to `/data/nextoomio-soak-marker.txt` before cycle 1. Validates checksum and content after each cycle and at run completion. |
| **AC4** | Any cycle failure halts the run immediately; report names cycle and step; run marked FAILED. | If any step fails (upload error, validation error, install error, device timeout, rollback, version mismatch, or marker corruption), execution halts immediately with exit code 1. |

---

## 3. Prerequisites and Equipment

### Hardware & Network Environment

- **Reference Board:** Raspberry Pi 4 Model B ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1).
- **Target Network:** Wired Ethernet connection with DHCP, reachable over IPv4 from the developer workstation.
- **SSH Access:** SSH key authentication configured during first-flash ([docs/bench/first-flash.md](first-flash.md), [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1). Active SSH agent loaded on workstation (AD-23).

### Image Artifacts

The soak test requires two distinguishable valid update images:
1. `image_a.swu`: Built release or test image reporting version $V_A$ (e.g., `0.0.1-soak-a`).
2. `image_b.swu`: Built release or test image reporting version $V_B$ (e.g., `0.0.1-soak-b`), where $V_A \neq V_B$.

Both images must have valid `sw-description` metadata compatible with `raspberrypi4-64`.

---

## 4. Tooling and CLI Usage

The soak test is executed via `scripts/bench/soak.py`.

```bash
# Standard 10-cycle update soak against bench device
python3 scripts/bench/soak.py 192.168.1.50 build/image-a.swu build/image-b.swu

# Explicit option flags with custom cycle count and report destination
python3 scripts/bench/soak.py \
  --device 192.168.1.50 \
  --image-a build/image-a.swu \
  --image-b build/image-b.swu \
  --cycles 10 \
  --user root \
  --port 22 \
  --timeout 120.0 \
  --poll-interval 2.0 \
  --marker-path /data/nextoomio-soak-marker.txt \
  --report-file soak-report.md \
  --verbose

# Automated self-tests (AC1–AC4 unit & integration suite)
python3 scripts/bench/soak.py --test
```

### 4.1 Security, Hygiene, and Option Compliance

- **Cycles Requirement (AC1):** The `--cycles` parameter defaults to 10 (the P0-F5.3 exit gate requirement). Specifying fewer than 10 cycles marks the execution as a non-gate result (`NON-GATE` / `FAILED`), while cycle counts `< 1` are rejected with exit code 2.
- **CQ-R7 (Safe Identifiers and Path Validation):** Host and user arguments are strictly validated against `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Any leading dash (`-`) or shell metacharacter is rejected with exit code 2 to prevent SSH option injection. In addition, `--marker-path` is strictly validated to ensure it is an absolute path residing under `/data/` containing only safe characters, preventing arbitrary filesystem access or shell injection.
- **P0-F5.1 (#25) AC6 / AD-23 (Credential Protection):** The runner uses the workstation's system `ssh` and active `ssh-agent`. It stores no passwords or private keys on disk and prints no key material in logs or reports.

---

## 5. Sequence of Operations

```
Workstation                                    Bench Device (Raspberry Pi 4)
    │                                                        │
    ├─── Step 0: Pre-flight checks ─────────────────────────►│  Verify SSH reachability
    │    Parse Image A and Image B metadata ($V_A \neq V_B$) │  Query initial slot & version
    │                                                        │
    ├─── Step 1: Initialize Persistent Marker (AC3) ────────►│  Write /data marker file
    │    Compute SHA-256 ($H_{marker}$)                     │  Verify write & checksum
    │                                                        │
    ├─── Step 2: Loop Cycles 1 to 10 (AC1)                   │
    │    ┌─ Select alternating image (A on odd, B on even)   │
    │    ├─ Upload .swu container payload ──────────────────►│  nx-sysd update stage
    │    ├─ Validate container signature & metadata ────────►│  nx-sysd update validate
    │    ├─ Install rootfs & boot to inactive slot ─────────►│  nx-sysd update install
    │    ├─ Arm trial boot with remote confirmation ────────►│  nx-sysd update trial
    │    ├─ Poll device return via boot_id & SSH ◄───────────┤  Firmware tryboot reboot
    │    ├─ Verify active slot alternated & version matches  │
    │    ├─ Send confirmation ──────────────────────────────►│  nx-sysd update confirm
    │    ├─ Verify committed update state ◄──────────────────┤  State: committed
    │    └─ Verify persistent /data marker SHA-256 ─────────►│  Check $H_{marker}$ intact
    │                                                        │
    └─── Step 3: Finalize Report (AC2)                       │
         Record per-cycle step times & final state           │
         Write soak-report.md and exit 0 (or exit 1 on fail) │
```

---

## 6. Failure Modes and Diagnostic Halting (AC4)

If any error occurs during any cycle, the soak run halts immediately without attempting subsequent cycles:

1. **Validation Failure:** Invalid CPIO container, missing `sw-description`, or signature failure halts at `validate`.
2. **Install Failure:** Flash write failure or block device error halts at `install`.
3. **Reboot Timeout:** If the device does not return within the 120-second trial budget, the run halts at `wait_for_device`.
4. **Rollback Detection:** If the device returns running the previous slot, rollback is diagnosed, confirmation is suppressed, and the run halts at `wait_for_device` / `slot_check`.
5. **Version Mismatch:** If the rebooted device reports an unexpected image version, the run halts at `version_check`.
6. **Marker Corruption / Data Loss:** If the marker file on `/data` is missing or its SHA-256 checksum does not match $H_{marker}$, the run halts at `marker_check`.

In all failure scenarios:
- The failing cycle index (e.g., Cycle 4) and failing step (e.g., `install`) are prominently highlighted in the report.
- The overall soak run status is recorded as `FAILED`.
- The process terminates with non-zero exit code 1.

---

## 7. Automated Self-Test Verification

The soak tool includes an automated in-memory test suite (`scripts/bench/test_soak.py`) simulating bench device SSH communication via `SoakMockDevice`:
- `test_ac1_and_ac2_ten_cycle_soak_success`: 10 full cycles alternating slots and versions, generating step times and final state.
- `test_ac3_marker_written_and_verified_unchanged`: Persistent marker creation and SHA-256 verification.
- `test_ac3_marker_corruption_causes_failure`: Marker tampering causes immediate halt at `marker_check`.
- `test_ac4_failure_during_install_step`: Halts at cycle 4 during install.
- `test_ac4_failure_during_device_timeout`: Halts at cycle 2 during reboot wait.
- `test_ac4_slot_alternation_failure`: Halts when slot fails to alternate.
- `test_ac4_version_mismatch_failure`: Halts on version mismatch.
- `test_cq_r7_unsafe_identifiers_rejected`: Host and user validation.
- `test_cli_parsing_positional_and_options`: Command-line interface argument handling.
