#!/usr/bin/env python3
"""Automated unit and integration self-tests for bench deploy tooling (P0-F5.1).

Verifies all six acceptance criteria (AC1–AC6) using deterministic mock runners:
- AC1: Full deploy sequence (upload, validate, install, trial, wait, verify, confirm).
- AC2: Exit status zero, versions, slot, and elapsed step times reported.
- AC3: Validation failure, device timeout, and version mismatch fail with step name and no confirmation.
- AC4: Rollback detected, reported with update state read from device, no confirmation.
- AC5: Consecutive deploys alternate slots (A -> B, then B -> A).
- AC6: No credential stored and no key material printed.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

# Ensure deploy module can be imported
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from deploy import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USER,
    DeployResult,
    Deployer,
    SshCommandResult,
    SshRunner,
    SystemSshRunner,
    build_parser,
    format_text_report,
    normalize_slot,
    opposite_slot,
    parse_arguments,
    parse_swu_description,
)


def create_mock_cpio_swu(
    dest_path: pathlib.Path,
    version: str = "0.0.1-dev-test",
    hardware: str = "raspberrypi4-64",
) -> None:
    """Create a minimal valid CPIO (070701 new ASCII) containing sw-description."""
    sw_desc = (
        f'software =\n'
        f'{{\n'
        f'    version = "{version}";\n'
        f'    hardware-compatibility: [ "{hardware}" ];\n'
        f'}};\n'
    ).encode("utf-8")

    filename = b"sw-description\x00"
    namesize = len(filename)
    filesize = len(sw_desc)

    # 110-byte CPIO header
    header = (
        b"070701"
        b"00000001"  # ino
        b"000081a4"  # mode (regular file 0644)
        b"00000000"  # uid
        b"00000000"  # gid
        b"00000001"  # nlink
        b"67370000"  # mtime
        + f"{filesize:08X}".encode("ascii")  # filesize
        + b"00000000"  # devmajor
        b"00000000"  # devminor
        b"00000000"  # rdevmajor
        b"00000000"  # rdevminor
        + f"{namesize:08X}".encode("ascii")  # namesize
        + b"00000000"  # chksum
    )

    pad_name = (4 - ((110 + namesize) % 4)) % 4
    pad_file = (4 - (filesize % 4)) % 4

    trailer = b"TRAILER!!!\x00"
    t_namesize = len(trailer)
    trailer_header = (
        b"070701"
        b"00000000"
        b"00000000"
        b"00000000"
        b"00000000"
        b"00000001"
        b"00000000"
        b"00000000"
        b"00000000"
        b"00000000"
        b"00000000"
        b"00000000"
        + f"{t_namesize:08X}".encode("ascii")
        + b"00000000"
    )
    t_pad = (4 - ((110 + t_namesize) % 4)) % 4

    with open(dest_path, "wb") as f:
        f.write(header)
        f.write(filename)
        f.write(b"\x00" * pad_name)
        f.write(sw_desc)
        f.write(b"\x00" * pad_file)
        f.write(trailer_header)
        f.write(trailer)
        f.write(b"\x00" * t_pad)


class MockBenchDevice(SshRunner):
    """Simulates bench device behavior over SSH for automated testing."""

    def __init__(
        self,
        initial_slot: str = "root_a",
        initial_version: str = "0.0.1-dev-base",
        target_version: str = "0.0.1-dev-test",
        reachable: bool = True,
        fail_validation: bool = False,
        fail_install: bool = False,
        fail_timeout: bool = False,
        simulate_rollback: bool = False,
        version_mismatch: bool = False,
    ):
        self.active_slot = initial_slot
        self.default_slot = initial_slot
        self.current_version = initial_version
        self.target_version = target_version
        self.reachable = reachable
        self.fail_validation = fail_validation
        self.fail_install = fail_install
        self.fail_timeout = fail_timeout
        self.simulate_rollback = simulate_rollback
        self.version_mismatch = version_mismatch

        # State tracking
        self.update_state = "Idle"
        self.confirmation_received = False
        self.commands_executed: List[str] = []
        self.uploaded_bytes = 0
        self.trial_armed = False

    def check_reachability(self, timeout: float = 3.0) -> bool:
        return self.reachable

    def stream_upload(
        self,
        local_file: pathlib.Path,
        remote_cmd: str,
        timeout: Optional[float] = None,
    ) -> SshCommandResult:
        self.commands_executed.append(f"UPLOAD: {remote_cmd}")
        if not self.reachable:
            return SshCommandResult(255, "", "SSH connection refused")
        self.uploaded_bytes = local_file.stat().st_size
        self.update_state = "Staged"
        return SshCommandResult(0, "Upload staged successfully", "")

    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        self.commands_executed.append(cmd)
        if not self.reachable:
            return SshCommandResult(255, "", "SSH connection refused")

        if "nx-sysd version --json" in cmd:
            data = {
                "version": self.current_version,
                "commit": "mock123",
                "build_id": "20261116-01",
                "slot": self.active_slot,
            }
            return SshCommandResult(0, json.dumps(data), "")

        if "nx-sysd update status --json" in cmd:
            data = {
                "state": self.update_state,
                "active_slot": self.active_slot,
                "default_slot": self.default_slot,
                "payload_length": self.uploaded_bytes,
                "reserved_capacity": 1073741824,
            }
            if self.update_state == "Failed":
                data["error_code"] = "health_check_timeout"
                data["message"] = "Local health check timed out during trial boot"
            return SshCommandResult(0, json.dumps(data), "")

        if "nx-sysd update validate" in cmd:
            if self.fail_validation:
                self.update_state = "Failed"
                return SshCommandResult(1, "", "Validation error: update_incompatible_hardware")
            self.update_state = "Validated"
            return SshCommandResult(0, '{"state": "Validated"}', "")

        if "nx-sysd update install" in cmd:
            if self.fail_install:
                self.update_state = "Failed"
                return SshCommandResult(1, "", "Installation write failed to partition")
            self.update_state = "Installed"
            return SshCommandResult(0, '{"state": "Installed"}', "")

        if "nx-sysd update trial" in cmd:
            self.trial_armed = True
            self.update_state = "Trial"
            # Simulate reboot behavior
            if self.fail_timeout:
                self.reachable = False  # Device hangs / never returns
            elif self.simulate_rollback:
                # Device boots previous slot and marks failed
                self.update_state = "Failed"
            else:
                # Normal reboot into inactive slot
                self.active_slot = opposite_slot(self.active_slot)
                if self.version_mismatch:
                    self.current_version = "0.0.0-unrelated-version"
                else:
                    self.current_version = self.target_version
            return SshCommandResult(0, "Trial boot armed, rebooting", "")

        if "nx-sysd update confirm" in cmd:
            self.confirmation_received = True
            self.default_slot = self.active_slot
            self.update_state = "Committed"
            return SshCommandResult(0, '{"state": "Committed"}', "")

        return SshCommandResult(0, "", "")


class TestDeployTool(unittest.TestCase):
    """Test suite covering AC1 through AC6."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = pathlib.Path(self.temp_dir.name)
        self.swu_file = self.temp_path / "test-image.swu"
        create_mock_cpio_swu(self.swu_file, version="0.0.1-dev-target")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cpio_metadata_parsing(self) -> None:
        """Verify sw-description parsing from CPIO archive."""
        meta = parse_swu_description(self.swu_file)
        self.assertEqual(meta.get("version"), "0.0.1-dev-target")
        self.assertEqual(meta.get("hardware"), ["raspberrypi4-64"])

    def test_ac1_and_ac2_successful_deploy(self) -> None:
        """AC1 & AC2: Full deploy sequence, exit zero, versions, slot, and step times."""
        device = MockBenchDevice(
            initial_slot="root_a",
            initial_version="0.0.1-dev-base",
            target_version="0.0.1-dev-target",
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
            remote_confirm=True,
        )

        res = deployer.deploy()

        self.assertTrue(res.success)
        self.assertEqual(res.previous_version, "0.0.1-dev-base")
        self.assertEqual(res.new_version, "0.0.1-dev-target")
        self.assertEqual(res.running_slot, "root_b")
        self.assertEqual(res.final_state, "Committed")
        self.assertTrue(res.confirmation_sent)
        self.assertTrue(device.confirmation_received)
        self.assertFalse(res.rollback_detected)

        # AC2: Check elapsed times for all steps
        steps = res.step_times
        for req_step in (
            "precheck",
            "upload",
            "validate",
            "install",
            "start_trial",
            "wait_for_device",
            "check_running",
            "confirm",
            "total",
        ):
            self.assertIn(req_step, steps)
            self.assertGreaterEqual(steps[req_step], 0.0)

        # Check human-readable report formatting
        report = format_text_report(res)
        self.assertIn("Deploy Succeeded", report)
        self.assertIn("Previous Version:  0.0.1-dev-base", report)
        self.assertIn("New Version:       0.0.1-dev-target", report)
        self.assertIn("Running Slot:      root_b", report)
        self.assertIn("Step Elapsed Times:", report)

    def test_ac3_validation_failure(self) -> None:
        """AC3: Validation fails -> non-zero exit, failing step named, no confirmation."""
        device = MockBenchDevice(
            initial_slot="root_a",
            fail_validation=True,
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )

        res = deployer.deploy()

        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "validate")
        self.assertIn("Validation error", str(res.error_message))
        self.assertFalse(res.confirmation_sent)
        self.assertFalse(device.confirmation_received)

        report = format_text_report(res)
        self.assertIn("Deploy FAILED", report)
        self.assertIn("Failing Step:      validate", report)
        self.assertIn("Confirmation Sent: No", report)

    def test_ac3_device_timeout(self) -> None:
        """AC3: Device does not return -> non-zero exit, wait_for_device named, no confirmation."""
        device = MockBenchDevice(
            initial_slot="root_a",
            fail_timeout=True,
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=0.2,
            poll_interval_secs=0.05,
        )

        res = deployer.deploy()

        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "wait_for_device")
        self.assertIn("timeout", str(res.error_message).lower())
        self.assertFalse(res.confirmation_sent)
        self.assertFalse(device.confirmation_received)

    def test_ac3_version_mismatch(self) -> None:
        """AC3: Version mismatch -> non-zero exit, version_check named, no confirmation."""
        device = MockBenchDevice(
            initial_slot="root_a",
            version_mismatch=True,
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            expected_version="0.0.1-dev-target",
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )

        res = deployer.deploy()

        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "version_check")
        self.assertIn("mismatch", str(res.error_message).lower())
        self.assertFalse(res.confirmation_sent)
        self.assertFalse(device.confirmation_received)

    def test_ac4_rollback_reporting(self) -> None:
        """AC4: Device returned on previous slot -> reports rollback and state, no confirmation."""
        device = MockBenchDevice(
            initial_slot="root_a",
            simulate_rollback=True,
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )

        res = deployer.deploy()

        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "slot_check")
        self.assertTrue(res.rollback_detected)
        self.assertFalse(res.confirmation_sent)
        self.assertFalse(device.confirmation_received)
        self.assertIn("health_check_timeout", res.device_update_state.get("error_code", ""))

        report = format_text_report(res)
        self.assertIn("Rollback Detected", report)
        self.assertIn("root_a", report)
        self.assertIn("health_check_timeout", report)

    def test_ac5_consecutive_deploys_alternate_slots(self) -> None:
        """AC5: Same image deployed twice completes normally into the other slot."""
        device = MockBenchDevice(
            initial_slot="root_a",
            initial_version="0.0.1-dev-base",
            target_version="0.0.1-dev-target",
        )

        # Run 1: root_a -> root_b
        deployer1 = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )
        res1 = deployer1.deploy()
        self.assertTrue(res1.success)
        self.assertEqual(res1.previous_version, "0.0.1-dev-base")
        self.assertEqual(res1.new_version, "0.0.1-dev-target")
        self.assertEqual(res1.running_slot, "root_b")
        self.assertEqual(device.active_slot, "root_b")

        # Run 2: root_b -> root_a with same image
        deployer2 = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )
        res2 = deployer2.deploy()
        self.assertTrue(res2.success)
        self.assertEqual(res2.previous_version, "0.0.1-dev-target")
        self.assertEqual(res2.new_version, "0.0.1-dev-target")
        self.assertEqual(res2.running_slot, "root_a")
        self.assertEqual(device.active_slot, "root_a")

    def test_ac6_no_credential_stored_or_printed(self) -> None:
        """AC6: Verifies system SSH runner stores no credentials and prints no key material."""
        runner = SystemSshRunner(host="192.0.2.1", user="benchuser", port=2222)
        base_args = runner._base_ssh_args()

        # Check command invocation parameters
        self.assertIn("ssh", base_args)
        self.assertIn("-p", base_args)
        self.assertIn("2222", base_args)
        self.assertIn("benchuser@192.0.2.1", base_args)
        self.assertIn("BatchMode=yes", " ".join(base_args))

        # Ensure no identity files (-i) or raw keys are hardcoded in the command
        self.assertNotIn("-i", base_args)
        for arg in base_args:
            self.assertNotIn("PRIVATE KEY", arg)
            self.assertNotIn("ssh-rsa", arg)
            self.assertNotIn("ssh-ed25519", arg)

    def test_cli_argument_parsing(self) -> None:
        """Verify CLI argument handling for positional and option flags."""
        # Positional order 1: <device> <file>
        args, dev, f = parse_arguments(["bench-pi4", str(self.swu_file)])
        self.assertEqual(dev, "bench-pi4")
        self.assertEqual(f, self.swu_file)

        # Positional order 2: <file> <device>
        args, dev, f = parse_arguments([str(self.swu_file), "bench-pi4"])
        self.assertEqual(dev, "bench-pi4")
        self.assertEqual(f, self.swu_file)

        # Explicit flags
        args, dev, f = parse_arguments(
            ["-d", "bench-pi4", "-f", str(self.swu_file), "--timeout", "90", "--json"]
        )
        self.assertEqual(dev, "bench-pi4")
        self.assertEqual(f, self.swu_file)
        self.assertEqual(args.timeout, 90.0)
        self.assertTrue(args.json)


def run_self_tests() -> int:
    """Run all unit tests and report result."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDeployTool)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_self_tests())
