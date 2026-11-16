#!/usr/bin/env python3
"""Automated unit and integration self-tests for ten-cycle update soak tool (P0-F5.3).

Verifies all four acceptance criteria (AC1–AC4), security rules (CQ-R7, AD-23),
and CLI reporting using deterministic in-memory mock runners:
- AC1: Ten alternating deploys all committed, slot toggles every cycle, version matches.
- AC2: Dated report with per-cycle step times and final state; unattended execution.
- AC3: Marker file written to /data before cycle 1 verified unchanged across all cycles.
- AC4: Immediate halt on any cycle failure with cycle & step diagnostics, reported as FAILED.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

# Ensure bench modules can be imported
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from deploy import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USER,
    SAFE_IDENTIFIER_PATTERN,
    SshCommandResult,
    SshRunner,
    SystemSshRunner,
    is_safe_identifier,
    normalize_slot,
    opposite_slot,
)
from soak import (
    DEFAULT_MARKER_PATH,
    DEFAULT_SOAK_CYCLES,
    CycleResult,
    MarkerManager,
    SoakError,
    SoakReport,
    SoakRunner,
    build_parser,
    is_safe_data_path,
    main,
    parse_arguments,
)
from test_deploy import create_mock_cpio_swu


class SoakMockDevice(SshRunner):
    """Simulates a bench device executing multi-cycle soak deployments."""

    def __init__(
        self,
        initial_slot: str = "root_a",
        initial_version: str = "0.0.1-soak-a",
        version_a: str = "0.0.1-soak-a",
        version_b: str = "0.0.1-soak-b",
        reachable: bool = True,
        fail_cycle: Optional[int] = None,
        fail_step: Optional[str] = None,
    ):
        self.host = "192.0.2.1"
        self.active_slot = initial_slot
        self.default_slot = initial_slot
        self.current_version = initial_version
        self.version_a = version_a
        self.version_b = version_b
        self.target_version = version_b if initial_version == version_a else version_a
        self.reachable = reachable

        # Failure injection
        self.fail_cycle = fail_cycle
        self.fail_step = fail_step
        self.current_cycle = 0

        # Boot & state tracking
        self.boot_count = 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"
        self.update_state = "Idle"
        self.trial_armed = False
        self.data_files: Dict[str, str] = {}
        self.commands_executed: List[str] = []
        self.uploaded_bytes = 0

    def check_reachability(self, timeout: float = 3.0) -> bool:
        return self.reachable

    def stream_upload(
        self,
        local_file: pathlib.Path,
        remote_cmd: str,
        timeout: Optional[float] = None,
    ) -> SshCommandResult:
        self.commands_executed.append(f"UPLOAD: {remote_cmd}")
        self.current_cycle += 1
        if not self.reachable:
            return SshCommandResult(255, "", "SSH connection refused")
        if self.fail_cycle == self.current_cycle and self.fail_step == "upload":
            return SshCommandResult(1, "", "Mock upload failed: network reset")
        self.uploaded_bytes = local_file.stat().st_size
        self.update_state = "Staged"
        return SshCommandResult(0, "Upload staged successfully", "")

    def _trigger_reboot(self) -> None:
        self.boot_count += 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"
        if self.fail_cycle == self.current_cycle and self.fail_step == "slot_alternation":
            # Retain current slot to simulate rollback / failed slot switch
            pass
        else:
            self.active_slot = opposite_slot(self.active_slot)

        if self.fail_cycle == self.current_cycle and self.fail_step in ("version_match", "version_check"):
            self.current_version = "0.0.0-unrelated-version"
        else:
            # Toggle version between version_a and version_b
            if self.current_version == self.version_a:
                self.current_version = self.version_b
            else:
                self.current_version = self.version_a

    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        self.commands_executed.append(cmd)
        if not self.reachable:
            return SshCommandResult(255, "", "SSH connection refused")

        # Marker write: cat > /data/... << 'EOF'
        if "cat >" in cmd and "EOF" in cmd:
            match = re.search(r"cat > '([^']+)' << 'EOF'\n(.*?)\nEOF", cmd, re.DOTALL)
            if match:
                fpath = match.group(1)
                content = match.group(2) + "\n"
                self.data_files[fpath] = content
                h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                return SshCommandResult(0, f"{h}  {fpath}\n", "")

        # Marker check: sha256sum ... && cat ...
        if "sha256sum" in cmd:
            match = re.search(r"sha256sum '([^']+)'", cmd)
            if match:
                fpath = match.group(1)
                # Inject failure if requested
                if self.fail_cycle == self.current_cycle and self.fail_step == "marker_check":
                    # Corrupt file
                    self.data_files[fpath] = "CORRUPTED_DATA\n"

                if fpath not in self.data_files:
                    return SshCommandResult(1, "", f"sha256sum: {fpath}: No such file or directory")
                content = self.data_files[fpath]
                h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                return SshCommandResult(0, f"{h}  {fpath}\n{content}", "")

        # Kernel boot ID
        if "cat /proc/sys/kernel/random/boot_id" in cmd:
            return SshCommandResult(0, self.boot_id, "")

        # Version query
        if "nx-sysd version --json" in cmd:
            data = {
                "version": self.current_version,
                "commit": "mock123",
                "build_id": "20261116-01",
                "slot": self.active_slot,
            }
            return SshCommandResult(0, json.dumps(data), "")

        # Status query
        if "nx-sysd update status --json" in cmd:
            data = {
                "state": self.update_state,
                "active_slot": self.active_slot,
                "default_slot": self.default_slot,
                "payload_length": self.uploaded_bytes,
                "reserved_capacity": 1073741824,
            }
            return SshCommandResult(0, json.dumps(data), "")

        # Validation
        if "nx-sysd update validate" in cmd:
            if self.fail_cycle == self.current_cycle and self.fail_step == "validate":
                return SshCommandResult(1, "", "Validation error: invalid signature")
            self.update_state = "Validated"
            return SshCommandResult(0, "Validated", "")

        # Installation
        if "nx-sysd update install" in cmd:
            if self.fail_cycle == self.current_cycle and self.fail_step == "install":
                return SshCommandResult(1, "", "Installation error: write failed")
            self.update_state = "Installed"
            return SshCommandResult(0, "Installed", "")

        # Trial boot trigger
        if "nx-sysd update trial" in cmd:
            if self.fail_cycle == self.current_cycle and self.fail_step == "trial":
                return SshCommandResult(1, "", "Trial boot trigger failed")
            self.update_state = "TrialArmed"
            self.trial_armed = True
            # In mock device, reboot takes effect
            if not (self.fail_cycle == self.current_cycle and self.fail_step == "wait_for_device"):
                self._trigger_reboot()
                self.update_state = "Trial"
            return SshCommandResult(0, "Reboot scheduled", "")

        # Confirmation
        if "nx-sysd update confirm" in cmd:
            if self.fail_cycle == self.current_cycle and self.fail_step == "confirm":
                return SshCommandResult(1, "", "Confirmation failed")
            self.update_state = "Committed"
            return SshCommandResult(0, "Update committed", "")

        return SshCommandResult(0, "", "")


class TestSoakTool(unittest.TestCase):
    """Test suite for ten-cycle update soak runner and acceptance criteria."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = pathlib.Path(self.temp_dir.name)

        self.img_a_path = self.dir_path / "image-a.swu"
        self.img_b_path = self.dir_path / "image-b.swu"

        create_mock_cpio_swu(self.img_a_path, version="0.0.1-soak-a")
        create_mock_cpio_swu(self.img_b_path, version="0.0.1-soak-b")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ac1_and_ac2_ten_cycle_soak_success(self) -> None:
        """AC1 & AC2: Full 10-cycle update soak success, slot alternation, version match, and report."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            version_a="0.0.1-soak-a",
            version_b="0.0.1-soak-b",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
            verbose=False,
        )

        report = runner.run()

        # AC1 Assertions
        self.assertTrue(report.success, f"Soak run should succeed: {report.failure_message}")
        self.assertEqual(len(report.cycles), 10)
        self.assertEqual(report.total_cycles_requested, 10)

        # Invariant: slot alternates every cycle
        expected_slots = ["root_b", "root_a"] * 5
        observed_slots = [c.running_slot for c in report.cycles]
        self.assertEqual(observed_slots, expected_slots)

        # Invariant: version matches deployed image version
        expected_versions = ["0.0.1-soak-b", "0.0.1-soak-a"] * 5
        observed_versions = [c.new_version for c in report.cycles]
        self.assertEqual(observed_versions, expected_versions)

        # Invariant: all cycles succeeded
        self.assertTrue(all(c.success for c in report.cycles))

        # AC2 Assertions: report contains step times and final state
        self.assertTrue(report.start_time)
        self.assertTrue(report.end_time)
        self.assertGreater(report.total_duration_secs, 0.0)
        self.assertEqual(report.initial_slot, "root_a")
        self.assertEqual(report.final_slot, "root_a")  # After 10 alternations from root_a, returns to root_a
        self.assertEqual(report.final_version, "0.0.1-soak-a")

        # Every cycle has recorded step times
        for c in report.cycles:
            self.assertIn("upload", c.step_times)
            self.assertIn("validate", c.step_times)
            self.assertIn("install", c.step_times)
            self.assertIn("wait_for_device", c.step_times)
            self.assertIn("confirm", c.step_times)
            self.assertIn("marker_check", c.step_times)

        # Markdown report rendering check
        md = report.format_markdown()
        self.assertIn("# Soak Test Report: Ten-Cycle Update Soak (P0-F5.3)", md)
        self.assertIn("| Cycle | Image | From Slot | To Slot | Deployed Version |", md)
        self.assertIn("PASSED", md)

    def test_ac3_marker_written_and_verified_unchanged(self) -> None:
        """AC3: Marker file written to /data before run and verified unchanged across all cycles."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
            marker_path="/data/custom-soak-marker.txt",
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )

        report = runner.run()
        self.assertTrue(report.success)
        self.assertTrue(report.marker_verified_unchanged)
        self.assertTrue(report.marker_hash)

        # Verify marker was written to the specified path in device mock
        self.assertIn("/data/custom-soak-marker.txt", device.data_files)
        content = device.data_files["/data/custom-soak-marker.txt"]
        self.assertIn("NEXTOOMIO_SOAK_MARKER_P0_F5_3", content)
        self.assertEqual(
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            report.marker_hash,
        )

    def test_ac3_marker_corruption_causes_failure(self) -> None:
        """AC3: Modification or corruption of marker file on /data triggers immediate halt."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            fail_cycle=3,
            fail_step="marker_check",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )

        report = runner.run()

        # AC3 & AC4: Halted on cycle 3 at step marker_check
        self.assertFalse(report.success)
        self.assertEqual(report.failing_cycle, 3)
        self.assertEqual(report.failing_step, "marker_check")
        self.assertIn("Persistent data marker check failed", report.failure_message or "")
        self.assertEqual(len(report.cycles), 3)
        self.assertFalse(report.marker_verified_unchanged)

    def test_ac4_failure_during_install_step(self) -> None:
        """AC4: Failure at install step halts run, names cycle and step, and marks FAILED."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            fail_cycle=4,
            fail_step="install",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )

        report = runner.run()

        self.assertFalse(report.success)
        self.assertEqual(report.failing_cycle, 4)
        self.assertEqual(report.failing_step, "install")
        self.assertIn("Installation error", report.failure_message or "")
        self.assertEqual(len(report.cycles), 4)

        # Cycles 5..10 must not have been executed
        self.assertLess(len(report.cycles), 10)

        # Check markdown report contains failure diagnosis
        md = report.format_markdown()
        self.assertIn("## Failure Diagnosis (AC4)", md)
        self.assertIn("Soak run halted at Cycle 4", md)
        self.assertIn("FAILED", md)

    def test_ac4_failure_during_device_timeout(self) -> None:
        """AC4: Device timeout during trial boot halts run and names wait_for_device."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            fail_cycle=2,
            fail_step="wait_for_device",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=5,
            timeout_secs=0.1,  # Short timeout for test
            poll_interval_secs=0.02,
        )

        report = runner.run()

        self.assertFalse(report.success)
        self.assertEqual(report.failing_cycle, 2)
        self.assertEqual(report.failing_step, "wait_for_device")
        self.assertEqual(len(report.cycles), 2)

    def test_ac4_slot_alternation_failure(self) -> None:
        """AC4: Failure to alternate slots triggers immediate halt and failure report."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            fail_cycle=3,
            fail_step="slot_alternation",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )

        report = runner.run()

        self.assertFalse(report.success)
        self.assertEqual(report.failing_cycle, 3)
        self.assertIn(report.failing_step, ("slot_check", "slot_alternation", "wait_for_device"))
        msg = (report.failure_message or "").lower()
        self.assertTrue("slot" in msg or "rollback" in msg)

    def test_ac4_version_mismatch_failure(self) -> None:
        """AC4: Version mismatch after reboot triggers immediate halt and failure report."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            fail_cycle=2,
            fail_step="version_match",
        )

        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=5,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )

        report = runner.run()

        self.assertFalse(report.success)
        self.assertEqual(report.failing_cycle, 2)
        self.assertIn(report.failing_step, ("version_match", "version_check"))
        self.assertIn("version mismatch", (report.failure_message or "").lower())

    def test_precheck_fails_on_identical_images(self) -> None:
        """Images A and B must have different versions; identical images are rejected in precheck."""
        device = SoakMockDevice()
        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_a_path,  # Same image passed twice
            cycles=2,
        )

        report = runner.run()
        self.assertFalse(report.success)
        self.assertEqual(report.failing_step, "precheck")
        self.assertIn("must be distinguishable", report.failure_message or "")

    def test_cli_parsing_positional_and_options(self) -> None:
        """Verify CLI argument parsing handles positional and flag forms correctly."""
        # Positional form
        args, dev, a, b = parse_arguments(["192.0.2.1", "a.swu", "b.swu", "--cycles", "5"])
        self.assertEqual(dev, "192.0.2.1")
        self.assertEqual(str(a), "a.swu")
        self.assertEqual(str(b), "b.swu")
        self.assertEqual(args.cycles, 5)

        # Flag form
        args2, dev2, a2, b2 = parse_arguments([
            "-d", "bench.local", "--image-a", "img1.swu", "--image-b", "img2.swu"
        ])
        self.assertEqual(dev2, "bench.local")
        self.assertEqual(str(a2), "img1.swu")
        self.assertEqual(str(b2), "img2.swu")

    def test_cq_r7_unsafe_identifiers_rejected(self) -> None:
        """CQ-R7: Option injection in host or user argument is rejected with exit code 2."""
        self.assertFalse(is_safe_identifier("-oProxyCommand=touch/tmp/pwn"))
        self.assertFalse(is_safe_identifier("host;rm -rf /"))
        self.assertTrue(is_safe_identifier("bench-rpi4.local"))
        self.assertTrue(is_safe_identifier("192.168.1.100"))

        exit_code = main(["-oProxyCommand=evil", str(self.img_a_path), str(self.img_b_path)])
        self.assertEqual(exit_code, 2)

    def test_json_and_text_report_outputs(self) -> None:
        """Verify JSON and plain-text serialization formats."""
        report = SoakReport(target_device="test-device", total_cycles_requested=1, marker_path="/data/test")
        report.start_time = "2026-11-16T00:00:00Z"
        report.end_time = "2026-11-16T00:01:00Z"
        report.total_duration_secs = 60.0
        report.success = True
        report.initial_slot = "root_a"
        report.final_slot = "root_b"

        c = CycleResult(
            cycle=1,
            image_name="Image B",
            swu_path=self.img_b_path,
            expected_version="0.0.1-soak-b",
            previous_slot="root_a",
            running_slot="root_b",
            previous_version="0.0.1-soak-a",
            new_version="0.0.1-soak-b",
            step_times={"upload": 1.0, "validate": 0.5, "install": 2.0, "wait_for_device": 5.0, "confirm": 0.5, "marker_check": 0.1},
            cycle_duration=9.1,
            success=True,
        )
        report.cycles.append(c)

        d = report.to_dict()
        self.assertEqual(d["target_device"], "test-device")
        self.assertEqual(len(d["cycles"]), 1)
        self.assertEqual(d["cycles"][0]["running_slot"], "root_b")

        md = report.format_markdown()
        self.assertIn("root_b", md)
        self.assertIn("0.0.1-soak-b", md)

    def test_fewer_than_ten_cycles_is_non_passing(self) -> None:
        """Fewer than 10 completed cycles marks the run as NON-GATE / FAILED and states actual count."""
        device = SoakMockDevice(
            initial_slot="root_a",
            initial_version="0.0.1-soak-a",
            version_a="0.0.1-soak-a",
            version_b="0.0.1-soak-b",
        )
        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=3,
            timeout_secs=2.0,
            poll_interval_secs=0.01,
        )
        report = runner.run()

        # AC1 & AC4: Run must not be counted as passed when cycles < 10
        self.assertFalse(report.success)
        self.assertEqual(report.failing_step, "gate_criteria")
        self.assertEqual(len(report.cycles), 3)
        self.assertEqual(report.total_cycles_requested, 3)
        self.assertTrue(report.marker_verified_unchanged)

        md = report.format_markdown()
        self.assertIn("NON-GATE (3 / 10 CYCLES)", md)
        self.assertIn("3 / 3", md)
        self.assertIn("All 3 executed cycle(s) committed", md)
        self.assertIn("AC1 gate requires 10 cycles", md)
        self.assertNotIn("All 10 cycles committed", md)

    def test_cli_rejects_cycles_less_than_one(self) -> None:
        """CLI argument parser rejects --cycles < 1 with exit code 2."""
        code_zero = main(["192.0.2.1", str(self.img_a_path), str(self.img_b_path), "--cycles", "0"])
        self.assertEqual(code_zero, 2)

        code_neg = main(["192.0.2.1", str(self.img_a_path), str(self.img_b_path), "--cycles", "-5"])
        self.assertEqual(code_neg, 2)

        # In-memory runner directly rejects cycles < 1 as precheck failure
        runner = SoakRunner(
            runner=SoakMockDevice(),
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=0,
        )
        report = runner.run()
        self.assertFalse(report.success)
        self.assertEqual(report.failing_step, "precheck")
        self.assertIn("Cycles must be at least 1", report.failure_message or "")

    def test_marker_status_on_early_precheck_failure(self) -> None:
        """Precheck failure outputs 'not checked' for persistent marker, not 'FAILED / MODIFIED'."""
        device = SoakMockDevice(reachable=False)
        runner = SoakRunner(
            runner=device,
            image_a=self.img_a_path,
            image_b=self.img_b_path,
            cycles=10,
        )
        report = runner.run()

        self.assertFalse(report.success)
        self.assertEqual(report.failing_step, "precheck")
        self.assertFalse(report.marker_verified_unchanged)

        md = report.format_markdown()
        self.assertIn("Persistent Data Marker (`/data/nextoomio-soak-marker.txt`):** not checked", md)
        self.assertNotIn("FAILED / MODIFIED", md)
        self.assertIn("NOT CHECKED - Run halted before marker verification", md)
        self.assertIn("FAILED - Halted during precheck before recording per-cycle step times", md)
        self.assertNotIn("unattended execution", md)

    def test_marker_path_validation(self) -> None:
        """CQ-R7: Marker path must be an absolute path under /data with safe characters."""
        self.assertTrue(is_safe_data_path("/data/marker.txt"))
        self.assertTrue(is_safe_data_path("/data/nested/marker.txt"))
        self.assertFalse(is_safe_data_path("/etc/passwd"))
        self.assertFalse(is_safe_data_path("relative/path"))
        self.assertFalse(is_safe_data_path("/data/../etc/passwd"))
        self.assertFalse(is_safe_data_path("/data/evil;rm"))
        self.assertFalse(is_safe_data_path("/data//double_slash"))

        code = main([
            "192.0.2.1",
            str(self.img_a_path),
            str(self.img_b_path),
            "--marker-path",
            "/etc/evil",
        ])
        self.assertEqual(code, 2)



if __name__ == "__main__":
    unittest.main()
