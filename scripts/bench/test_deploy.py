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
import subprocess
import time
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

# Ensure deploy module can be imported
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from deploy import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USER,
    SAFE_IDENTIFIER_PATTERN,
    DeployResult,
    Deployer,
    SshCommandResult,
    SshRunner,
    SystemSshRunner,
    build_parser,
    format_text_report,
    is_safe_identifier,
    main,
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
        target_version: str = "0.0.1-dev-target",
        reachable: bool = True,
        fail_validation: bool = False,
        fail_install: bool = False,
        fail_timeout: bool = False,
        simulate_rollback: bool = False,
        version_mismatch: bool = False,
        reboot_delay_polls: int = 0,
        fail_confirm_state: bool = False,
    ):
        self.active_slot = initial_slot
        self.default_slot = initial_slot
        self.initial_version = initial_version
        self.current_version = initial_version
        self.target_version = target_version
        self.reachable = reachable
        self.fail_validation = fail_validation
        self.fail_install = fail_install
        self.fail_timeout = fail_timeout
        self.simulate_rollback = simulate_rollback
        self.version_mismatch = version_mismatch
        self.reboot_delay_polls = reboot_delay_polls
        self.remaining_reboot_delay = 0
        self.fail_confirm_state = fail_confirm_state

        # State tracking
        self.boot_count = 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"
        self.pre_reboot_boot_id = self.boot_id
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

    def _trigger_reboot(self) -> None:
        self.boot_count += 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"
        if self.simulate_rollback:
            self.update_state = "Failed"
        else:
            self.active_slot = opposite_slot(self.active_slot)
            if self.version_mismatch:
                self.current_version = "0.0.0-unrelated-version"
            else:
                self.current_version = self.target_version

    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        self.commands_executed.append(cmd)
        if not self.reachable:
            return SshCommandResult(255, "", "SSH connection refused")

        if "cat /proc/sys/kernel/random/boot_id" in cmd:
            if self.trial_armed and self.remaining_reboot_delay > 0:
                self.remaining_reboot_delay -= 1
                old_id = self.pre_reboot_boot_id
                if self.remaining_reboot_delay == 0:
                    self._trigger_reboot()
                return SshCommandResult(0, old_id, "")
            return SshCommandResult(0, self.boot_id, "")

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
            self.pre_reboot_boot_id = self.boot_id
            if self.fail_timeout:
                self.reachable = False  # Device hangs / never returns
            elif self.reboot_delay_polls > 0:
                self.remaining_reboot_delay = self.reboot_delay_polls
            else:
                self._trigger_reboot()
            return SshCommandResult(0, "Trial boot armed, rebooting", "")

        if "nx-sysd update confirm" in cmd:
            self.trial_armed = False
            if self.fail_confirm_state:
                self.update_state = "Unconfirmed"
                return SshCommandResult(0, '{"state": "Unconfirmed"}', "")
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

    def test_final_states_in_both_confirmation_modes(self) -> None:
        for remote in (False, True):
            for sequence, success, message in [
                (["Committed"], True, ""), (["Trial", "Committed"], True, ""),
                (["Trial"], False, "still in trial"), (["Failed"], False, "Failed"),
                ([None], False, "unknown"),
            ]:
                with self.subTest(remote=remote, sequence=sequence):
                    class StateDevice(MockBenchDevice):
                        reads = 0
                        def run(self, cmd, timeout=None):
                            if cmd == "nx-sysd update status --json" and self.trial_armed:
                                # First post-boot query is the slot/version check.
                                self.reads += 1
                                if self.reads > 1:
                                    state = sequence[min(self.reads - 2, len(sequence) - 1)]
                                    return SshCommandResult(0, json.dumps({"state": state}), "")
                            if cmd == "nx-sysd update confirm":
                                self.commands_executed.append(cmd)
                                return SshCommandResult(0, "", "")
                            return super().run(cmd, timeout)
                    device = StateDevice()
                    result = Deployer(device, self.swu_file, timeout_secs=0.08,
                                      poll_interval_secs=0.005, remote_confirm=remote).deploy()
                    self.assertEqual(result.success, success, result.error_message)
                    if not success:
                        self.assertIn(message, result.error_message)
                    trial = [c for c in device.commands_executed if c.startswith("nx-sysd update trial")]
                    self.assertEqual(trial, ["nx-sysd update trial"])
                    self.assertEqual("nx-sysd update confirm" in device.commands_executed, remote)

    def test_hanging_queries_obey_budget_and_name_step(self) -> None:
        for stage in ("precheck", "confirm"):
            with self.subTest(stage=stage):
                class HangingDevice(MockBenchDevice):
                    def run(self, cmd, timeout=None):
                        if cmd == "nx-sysd version --json" and (stage == "precheck" or self.confirmation_received):
                            self_timeout = timeout
                            try:
                                subprocess.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=self_timeout)
                            except subprocess.TimeoutExpired:
                                return SshCommandResult(124, "", "timed out")
                        return super().run(cmd, timeout)
                start = time.monotonic()
                result = Deployer(HangingDevice(), self.swu_file, timeout_secs=0.15, poll_interval_secs=0.005).deploy()
                self.assertLess(time.monotonic() - start, 0.6)
                self.assertFalse(result.success)
                self.assertEqual(result.failing_step, stage)
                self.assertIn("timed out", result.error_message)

    def test_failed_stage_removes_upload(self) -> None:
        class StageDevice(MockBenchDevice):
            def stream_upload(inner, local_file, remote_cmd, timeout=None):
                path = str(self.temp_path / "data" / "update")
                command = remote_cmd.replace("/data/update", path)
                command = "nx-sysd() { return 1; }; " + command
                proc = subprocess.run(["sh", "-c", command], input=b"payload", capture_output=True, timeout=timeout)
                return SshCommandResult(proc.returncode, "", proc.stderr.decode())
        result = Deployer(StageDevice(), self.swu_file, timeout_secs=2).deploy()
        self.assertFalse(result.success)
        self.assertEqual(result.failing_step, "upload")
        self.assertFalse((self.temp_path / "data/update/staging.upload.swu").exists())

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

    def test_ac1_precheck_fails_when_no_expected_version(self) -> None:
        """AC1: Precheck fails with non-zero exit when no version in SWU and none provided."""
        no_ver_swu = self.temp_path / "no-version.swu"
        no_ver_swu.write_bytes(b"not a valid cpio archive")
        device = MockBenchDevice()
        deployer = Deployer(
            runner=device,
            swu_path=no_ver_swu,
            expected_version=None,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
        )
        res = deployer.deploy()
        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "precheck")
        self.assertIn("No expected version", str(res.error_message))
        self.assertFalse(res.confirmation_sent)

    def test_ac1_confirm_fails_when_device_state_not_committed(self) -> None:
        """AC1: Confirm step fails with non-zero exit if device state is not 'committed'."""
        device = MockBenchDevice(
            initial_slot="root_a",
            fail_confirm_state=True,
        )
        deployer = Deployer(
            runner=device,
            swu_path=self.swu_file,
            timeout_secs=5.0,
            poll_interval_secs=0.01,
            remote_confirm=True,
        )
        res = deployer.deploy()
        self.assertFalse(res.success)
        self.assertEqual(res.failing_step, "confirm")
        self.assertIn("Unconfirmed", str(res.error_message))
        self.assertNotEqual(res.final_state.lower(), "committed")

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

    def test_ac3_device_stays_reachable_during_shutdown_before_reboot(self) -> None:
        """AC3/AC4: Device remains reachable for a few polls after reboot without false rollback."""
        device = MockBenchDevice(
            initial_slot="root_a",
            initial_version="0.0.1-dev-base",
            target_version="0.0.1-dev-target",
            reboot_delay_polls=2,
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
        self.assertEqual(res.running_slot, "root_b")
        self.assertEqual(res.new_version, "0.0.1-dev-target")
        self.assertEqual(res.final_state, "Committed")
        self.assertTrue(res.confirmation_sent)
        self.assertFalse(res.rollback_detected)

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
        self.assertIn("--", base_args)
        dash_idx = base_args.index("--")
        self.assertEqual(base_args[dash_idx + 1], "benchuser@192.0.2.1")
        self.assertIn("BatchMode=yes", " ".join(base_args))

        # Ensure no identity files (-i) or raw keys are hardcoded in the command
        self.assertNotIn("-i", base_args)
        for arg in base_args:
            self.assertNotIn("PRIVATE KEY", arg)
            self.assertNotIn("ssh-rsa", arg)
            self.assertNotIn("ssh-ed25519", arg)

        # Check working directory and stdout/stderr during a mock deploy run
        with tempfile.TemporaryDirectory() as clean_dir:
            clean_path = pathlib.Path(clean_dir)
            swu_copy = clean_path / "test-clean.swu"
            swu_copy.write_bytes(self.swu_file.read_bytes())
            initial_files = set(clean_path.iterdir())

            device = MockBenchDevice(
                initial_slot="root_a",
                initial_version="0.0.1-dev-base",
                target_version="0.0.1-dev-target",
            )
            deployer = Deployer(
                runner=device,
                swu_path=swu_copy,
                timeout_secs=5.0,
                poll_interval_secs=0.01,
                verbose=True,
            )

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            old_stdout, old_stderr = sys.stdout, sys.stderr
            try:
                sys.stdout, sys.stderr = stdout_buf, stderr_buf
                res = deployer.deploy()
                report = format_text_report(res)
                print(report)
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

            combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()

            # Verify no secret or key patterns in output
            for pattern in (
                "PRIVATE KEY",
                "ssh-rsa",
                "ssh-ed25519",
                "BEGIN RSA",
                "BEGIN OPENSSH",
            ):
                self.assertNotIn(pattern, combined_output)

            # Verify no credentials or unexpected files were created in working directory
            final_files = set(clean_path.iterdir())
            self.assertEqual(initial_files, final_files)

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

    def test_cq_r7_host_and_user_validation_rejects_option_injection(self) -> None:
        """CQ-R7: Host and user values starting with '-' or containing unsafe characters are rejected."""
        # 1. SystemSshRunner raises ValueError on invalid host or user
        with self.assertRaises(ValueError):
            SystemSshRunner(host="-oProxyCommand=calc")
        with self.assertRaises(ValueError):
            SystemSshRunner(host="bench-pi4", user="-oProxyCommand=calc")
        with self.assertRaises(ValueError):
            SystemSshRunner(host="-invalid-host")
        with self.assertRaises(ValueError):
            SystemSshRunner(host="bench;reboot")
        with self.assertRaises(ValueError):
            SystemSshRunner(host="bench-pi4", user="user name")
        with self.assertRaises(ValueError):
            SystemSshRunner(host="bench-pi4", user="user@domain")

        # 2. main() exits status 2 when device starts with '-' or is invalid
        buf = io.StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = buf
            rc = main(["--device=-oProxyCommand=calc", "-f", str(self.swu_file)])
            self.assertEqual(rc, 2)
            self.assertIn("invalid device", buf.getvalue())

            buf.seek(0)
            buf.truncate(0)
            rc = main(["-d", "bench;reboot", "-f", str(self.swu_file)])
            self.assertEqual(rc, 2)
            self.assertIn("invalid device", buf.getvalue())

            # 3. main() exits status 2 when user starts with '-' or is invalid
            buf.seek(0)
            buf.truncate(0)
            rc = main(["-u=-oProxyCommand=calc", "-d", "bench-pi4", "-f", str(self.swu_file)])
            self.assertEqual(rc, 2)
            self.assertIn("invalid user", buf.getvalue())

            buf.seek(0)
            buf.truncate(0)
            rc = main(["-u", "user@evil", "-d", "bench-pi4", "-f", str(self.swu_file)])
            self.assertEqual(rc, 2)
            self.assertIn("invalid user", buf.getvalue())

            # 4. main() exits status 2 when positional device starts with '-'
            buf.seek(0)
            buf.truncate(0)
            rc = main(["--", "-invalid-device", str(self.swu_file)])
            self.assertEqual(rc, 2)
            self.assertIn("invalid device", buf.getvalue())

            # 5. Option flag followed by -option also exits status 2
            buf.seek(0)
            buf.truncate(0)
            rc = main(["-d", "-oProxyCommand=calc", "-f", str(self.swu_file)])
            self.assertEqual(rc, 2)
        finally:
            sys.stderr = old_stderr

        # 5. Verify '--' is passed before user@host in SSH arguments
        runner = SystemSshRunner(host="bench-pi4", user="root")
        args = runner._base_ssh_args()
        self.assertIn("--", args)
        idx = args.index("--")
        self.assertEqual(args[idx + 1], "root@bench-pi4")


def run_self_tests() -> int:
    """Run all unit tests and report result."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDeployTool)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_self_tests())
