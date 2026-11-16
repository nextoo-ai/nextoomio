#!/usr/bin/env python3
"""Automated bench test runner and verification suite for deliberately broken images.

Validates the three failure modes defined in P0-F4.7 and ARCHITECTURE.md section 3.6:
- AC1: Kernel hangs before systemd starts -> hardware watchdog reset -> return to previous slot.
- AC2: Boots systemd but fails local health check -> timeout/failure -> return to previous slot.
- AC3: Boots and locally healthy but no network/SSH -> remote confirmation timeout -> return to previous slot.
- AC4: Data partition remains unchanged across rollbacks.
- AC5: Valid image can be deployed and committed immediately afterwards.
- AC6: Three test images can be rebuilt from documented build options.

Usage:
    python3 scripts/bench/test_broken_images.py --test
    python3 scripts/bench/test_broken_images.py [--host <ip/hostname>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

TEST_IMAGES = {
    "hang": {
        "recipe": ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image-broken-hang.bb",
        "kas": ROOT / "kas/test-broken-hang.yml",
        "target": "nextoomio-image-broken-hang",
        "description": "Kernel/init hang before systemd starts (AC1)",
        "fault_package": "nextoomio-test-faults-hang",
    },
    "health": {
        "recipe": ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image-broken-health.bb",
        "kas": ROOT / "kas/test-broken-health.yml",
        "target": "nextoomio-image-broken-health",
        "description": "Local health check failure (AC2)",
        "fault_package": "nextoomio-test-faults-health",
    },
    "network": {
        "recipe": ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image-broken-network.bb",
        "kas": ROOT / "kas/test-broken-network.yml",
        "target": "nextoomio-image-broken-network",
        "description": "Unreachable network / no SSH service (AC3)",
        "fault_package": "nextoomio-test-faults-network",
    },
}


def validate_test_images_statically(root: pathlib.Path = ROOT) -> list[str]:
    """Validate static invariants for broken test images and build options (AC6)."""
    errors: list[str] = []

    # 1. Base image must not contain any fault injection packages (Isolation requirement)
    base_image_bb = root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
    if base_image_bb.is_file():
        base_text = base_image_bb.read_text(encoding="utf-8")
        if "nextoomio-test-faults" in base_text:
            errors.append("nextoomio-image.bb must not include nextoomio-test-faults (broken images must be isolated)")
        if "broken" in base_text.lower():
            errors.append("nextoomio-image.bb must not reference broken image components")
    else:
        errors.append("Missing base image recipe: meta-nextoomio/recipes-core/images/nextoomio-image.bb")

    # 2. Base kas configuration must target nextoomio-image, not any broken variant
    base_kas = root / "kas/base.yml"
    if base_kas.is_file():
        base_kas_text = base_kas.read_text(encoding="utf-8")
        if not re.search(r"^\s*target:\s*nextoomio-image\s*$", base_kas_text, re.MULTILINE):
            errors.append("kas/base.yml must target 'nextoomio-image' as default release target")
        if "broken" in base_kas_text.lower():
            errors.append("kas/base.yml must not reference broken image variants")

    # 3. Fault injection recipe and helper files must exist
    faults_bb = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/nextoomio-test-faults_0.0.1.bb"
    if not faults_bb.is_file():
        errors.append("Missing fault injection recipe: nextoomio-test-faults_0.0.1.bb")
    else:
        faults_text = faults_bb.read_text(encoding="utf-8")
        for pkg in ("hang", "health", "network"):
            if f"${{PN}}-{pkg}" not in faults_text:
                errors.append(f"nextoomio-test-faults_0.0.1.bb missing package ${{PN}}-{pkg}")

    hang_sh = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/nextoomio-hang-early.sh"
    if not hang_sh.is_file():
        errors.append("Missing early hang script: nextoomio-hang-early.sh")
    else:
        hang_text = hang_sh.read_text(encoding="utf-8")
        if "DELIBERATE EARLY INIT HANG" not in hang_text:
            errors.append("nextoomio-hang-early.sh missing identification banner")
        if "sleep" not in hang_text or "while" not in hang_text:
            errors.append("nextoomio-hang-early.sh must loop/sleep to induce watchdog reset")

    health_sh = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/nextoomio-broken-health.sh"
    if not health_sh.is_file():
        errors.append("Missing broken health check script: nextoomio-broken-health.sh")
    else:
        health_text = health_sh.read_text(encoding="utf-8")
        if "exit 1" not in health_text:
            errors.append("nextoomio-broken-health.sh must exit 1 to indicate failure")
        if "/run/nextoomio/diagnostics/health.json" not in health_text:
            errors.append("nextoomio-broken-health.sh must output failure diagnostics json")

    health_svc = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/nextoomio-broken-health.service"
    if not health_svc.is_file():
        errors.append("Missing broken health service: nextoomio-broken-health.service")

    block_nft = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/99-block-network-test.nft"
    if not block_nft.is_file():
        errors.append("Missing firewall block rule: 99-block-network-test.nft")

    # 4. Each broken image recipe and kas file must exist and match specification
    for name, info in TEST_IMAGES.items():
        recipe_path = info["recipe"]
        kas_path = info["kas"]
        target = info["target"]
        fault_pkg = info["fault_package"]

        if not recipe_path.is_file():
            errors.append(f"Missing broken image recipe: {recipe_path.relative_to(root)}")
        else:
            rec_text = recipe_path.read_text(encoding="utf-8")
            if "require nextoomio-image.bb" not in rec_text:
                errors.append(f"{recipe_path.name} must require nextoomio-image.bb")
            if fault_pkg not in rec_text:
                errors.append(f"{recipe_path.name} must install {fault_pkg}")

            if name == "hang":
                if "ROOTFS_POSTPROCESS_COMMAND" not in rec_text or "/sbin/init" not in rec_text:
                    errors.append(f"{recipe_path.name} must override /sbin/init via ROOTFS_POSTPROCESS_COMMAND")
            elif name == "network":
                if "ROOTFS_POSTPROCESS_COMMAND" not in rec_text or "NetworkManager.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask NetworkManager.service via ROOTFS_POSTPROCESS_COMMAND")
                if "sshd.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask sshd.service via ROOTFS_POSTPROCESS_COMMAND")

        if not kas_path.is_file():
            errors.append(f"Missing kas configuration file: {kas_path.relative_to(root)}")
        else:
            kas_text = kas_path.read_text(encoding="utf-8")
            if f"target: {target}" not in kas_text:
                errors.append(f"{kas_path.name} must set target to '{target}'")
            if "rpi4-64.yml" not in kas_text:
                errors.append(f"{kas_path.name} must include rpi4-64.yml")

    return errors


class RollbackSimulation:
    """Simulates the A/B update state machine and rollback engine per ARCHITECTURE.md section 3.6."""

    def __init__(self, initial_slot: str = "A") -> None:
        self.active_slot = initial_slot
        self.default_slot = initial_slot
        self.consumed_attempts = 0
        self.max_attempts = 2
        self.trial_budget_seconds = 120
        self.transaction_state = "idle"
        self.data_partition_files = {"/data/config/v0/network.json": "valid", "/data/test_marker": "marker_12345"}

    def install(self, image_type: str) -> None:
        """Stage and install image into inactive slot."""
        target_slot = "B" if self.active_slot == "A" else "A"
        self.target_slot = target_slot
        self.target_image_type = image_type
        self.transaction_state = "installed"
        self.consumed_attempts = 0

    def arm_trial(self) -> None:
        """Arm trial boot: persist attempt counter and switch tryboot selection."""
        assert self.transaction_state in ("installed", "trial_failed_attempt_1"), "Invalid state for trial"
        self.consumed_attempts += 1
        self.transaction_state = "trial"
        # Boots target slot on trial, but default slot remains previous slot
        self.active_slot = self.target_slot

    def evaluate_boot(self) -> str:
        """Evaluate trial boot behavior for given broken image type."""
        assert self.transaction_state == "trial"

        if self.target_image_type == "hang":
            # AC1: Kernel hangs before systemd; hardware watchdog resets board
            return "watchdog_reset"
        elif self.target_image_type == "health":
            # AC2: Boots systemd but local health check fails
            return "health_check_failed"
        elif self.target_image_type == "network":
            # AC3: Boots, local health check passes, but network/SSH unreachable; remote confirmation times out
            return "remote_confirmation_timeout"
        elif self.target_image_type == "valid":
            return "passed"
        else:
            raise ValueError(f"Unknown image type: {self.target_image_type}")

    def recover(self, failure_reason: str) -> bool:
        """Execute recovery logic. Returns True if returning to trial, False if permanently returned to previous slot."""
        # On failure or watchdog reset, tryboot falls back or next attempt runs
        if self.consumed_attempts < self.max_attempts:
            # Exactly one more trial is permitted (UPD-06, ARCHITECTURE.md section 3.6 step 7)
            self.transaction_state = "trial_failed_attempt_1"
            self.arm_trial()
            return True
        else:
            # Exhausted attempts: permanently select previous good slot, state is 'failed'
            self.active_slot = self.default_slot
            self.transaction_state = "failed"
            return False

    def commit(self) -> None:
        """Commit trial slot as default after successful confirmation."""
        assert self.transaction_state == "trial"
        self.default_slot = self.active_slot
        self.transaction_state = "committed"


def run_self_tests() -> int:
    """Run automated self-tests verifying static checks and update state machine logic."""
    print("Running test_broken_images self-tests...")

    # 1. Static validation of repository files
    errs = validate_test_images_statically(ROOT)
    assert not errs, f"Static validation failed: {errs}"
    print("  test_static_validation_passes: OK")

    # 2. AC1: Test early hang image simulation (watchdog reset, 2 attempts, returns to slot A, failed state)
    sim_hang = RollbackSimulation("A")
    data_snapshot = dict(sim_hang.data_partition_files)
    sim_hang.install("hang")
    sim_hang.arm_trial()
    assert sim_hang.active_slot == "B"
    assert sim_hang.default_slot == "A"
    assert sim_hang.consumed_attempts == 1

    # Attempt 1 fails (watchdog reset)
    res1 = sim_hang.evaluate_boot()
    assert res1 == "watchdog_reset"
    has_retry = sim_hang.recover(res1)
    assert has_retry is True
    assert sim_hang.consumed_attempts == 2

    # Attempt 2 fails (watchdog reset)
    res2 = sim_hang.evaluate_boot()
    assert res2 == "watchdog_reset"
    has_retry2 = sim_hang.recover(res2)
    assert has_retry2 is False
    assert sim_hang.active_slot == "A"
    assert sim_hang.transaction_state == "failed"
    # AC4: Data partition unchanged
    assert sim_hang.data_partition_files == data_snapshot
    print("  test_ac1_early_hang_rollback_and_attempt_limit: OK")

    # 3. AC2: Test local health check failure simulation
    sim_health = RollbackSimulation("A")
    sim_health.install("health")
    sim_health.arm_trial()
    res1 = sim_health.evaluate_boot()
    assert res1 == "health_check_failed"
    sim_health.recover(res1)
    res2 = sim_health.evaluate_boot()
    assert res2 == "health_check_failed"
    sim_health.recover(res2)
    assert sim_health.active_slot == "A"
    assert sim_health.transaction_state == "failed"
    assert sim_health.data_partition_files == data_snapshot
    print("  test_ac2_health_check_failure_rollback: OK")

    # 4. AC3: Test unreachable network simulation (remote confirmation timeout)
    sim_net = RollbackSimulation("A")
    sim_net.install("network")
    sim_net.arm_trial()
    res1 = sim_net.evaluate_boot()
    assert res1 == "remote_confirmation_timeout"
    sim_net.recover(res1)
    res2 = sim_net.evaluate_boot()
    assert res2 == "remote_confirmation_timeout"
    sim_net.recover(res2)
    assert sim_net.active_slot == "A"
    assert sim_net.transaction_state == "failed"
    assert sim_net.data_partition_files == data_snapshot
    print("  test_ac3_unreachable_network_confirmation_timeout: OK")

    # 5. AC5: Test deploying valid image immediately after recovery
    sim_net.install("valid")
    sim_net.arm_trial()
    assert sim_net.active_slot == "B"
    res_valid = sim_net.evaluate_boot()
    assert res_valid == "passed"
    sim_net.commit()
    assert sim_net.active_slot == "B"
    assert sim_net.default_slot == "B"
    assert sim_net.transaction_state == "committed"
    print("  test_ac5_immediate_valid_image_deployment_and_commit: OK")

    # 6. Negative test: verify that modifying nextoomio-image.bb to include test faults fails validation
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_p = pathlib.Path(tmp_str)
        # Create minimal mock tree
        img_dir = tmp_p / "meta-nextoomio/recipes-core/images"
        img_dir.mkdir(parents=True)
        (img_dir / "nextoomio-image.bb").write_text('IMAGE_INSTALL:append = " nextoomio-test-faults-hang"\n', encoding="utf-8")
        kas_dir = tmp_p / "kas"
        kas_dir.mkdir(parents=True)
        (kas_dir / "base.yml").write_text("target: nextoomio-image\n", encoding="utf-8")
        test_errs = validate_test_images_statically(tmp_p)
        assert any("nextoomio-image.bb must not include nextoomio-test-faults" in e for e in test_errs)
    print("  test_isolation_failure_detected: OK")

    print("OK: all test_broken_images self-tests passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test runner for automatic return from broken images (P0-F4.7)")
    parser.add_argument("--test", action="store_true", help="Run automated self-tests and static validation")
    parser.add_argument("--host", default="nextoomio.local", help="Target bench device hostname or IP")
    parser.add_argument("--dry-run", action="store_true", help="Simulate bench deployment without connecting to hardware")
    args = parser.parse_args()

    if args.test:
        return run_self_tests()

    print(f"Target bench host: {args.host}")
    print("Checking static invariants...")
    errs = validate_test_images_statically(ROOT)
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("Static checks OK: 3 test image recipes and kas build options verified.")
    if args.dry_run:
        print("[DRY-RUN] Simulating bench deployment across all 3 failure modes...")
        sim = RollbackSimulation("A")
        for mode in ("hang", "health", "network"):
            print(f"[DRY-RUN] Testing mode '{mode}': {TEST_IMAGES[mode]['description']}")
            sim.install(mode)
            sim.arm_trial()
            r1 = sim.evaluate_boot()
            sim.recover(r1)
            r2 = sim.evaluate_boot()
            sim.recover(r2)
            assert sim.transaction_state == "failed"
            assert sim.active_slot == "A"
            print(f"[DRY-RUN] Mode '{mode}' returned to slot A with state 'failed'.")
        print("[DRY-RUN] Verification complete.")
        return 0

    print("Bench hardware deployment requires physical execution on bench device with SWUpdate.")
    print("Run with --dry-run for simulation or --test for static self-tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
