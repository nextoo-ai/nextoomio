#!/usr/bin/env python3
"""Automated unit and integration self-tests for power-loss resilience tool (P0-F4.8).

Verifies all four acceptance criteria (AC1–AC4), security rules (CQ-R6, CQ-R7),
and reporting using deterministic in-memory mock devices:
- AC1: In every case (upload, install, trial, commit), device boots, is reachable over SSH,
       and runs a slot holding a complete image.
- AC2: Update state read afterwards is consistent with running slot:
       - Interrupted upload or install is not installable or bootable.
       - Interrupted trial has consumed its attempt.
       - Interrupted commit is either completed or returned to previous slot, never undefined.
- AC3: Ten repetitions per case with varied seeded delays, verifying zero physical access
       interventions and flagging any physical access as gate blocker.
- AC4: Given power loss in any case, subsequent valid deploy succeeds to completion.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

# Ensure bench modules can be imported
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from deploy import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USER,
    SshCommandResult,
    SshRunner,
    is_safe_identifier,
    normalize_slot,
    opposite_slot,
)
from power_loss import (
    DEFAULT_TRIALS_PER_CASE,
    VALID_CASES,
    CommandPowerController,
    DeviceRecoveryError,
    MockPowerController,
    PowerLossError,
    PowerLossReport,
    PowerLossRunner,
    PowerLossTrialResult,
    StateConsistencyError,
    build_parser,
    main,
    parse_arguments,
    redact_text,
)
from test_deploy import create_mock_cpio_swu


class PowerLossMockDevice(SshRunner):
    """Simulates a bench device undergoing power-loss interruptions during update phases."""

    def __init__(
        self,
        initial_slot: str = "root_a",
        initial_version: str = "0.0.1-powerloss-base",
        target_version: str = "0.0.1-powerloss-target",
        fail_recovery: bool = False,
        power_ctrl: Optional[MockPowerController] = None,
    ):
        self.host = "192.0.2.1"
        self.active_slot = initial_slot
        self.default_slot = initial_slot
        self.initial_version = initial_version
        self.current_version = initial_version
        self.target_version = target_version
        self.fail_recovery = fail_recovery
        self.power_ctrl = power_ctrl

        # Power state
        self.is_powered = True
        self.boot_count = 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"

        # Update state machine
        self.update_state = "Idle"
        self.trial_attempts: Optional[int] = 0
        self.uploaded_bytes = 0
        self.current_phase = "idle"
        self.last_interrupted_phase: Optional[str] = None
        self.commands_executed: List[str] = []
        self.mount_options = "ro,relatime"
        self.mount_device_override: Optional[str] = None

    def handle_power_cut(self) -> None:
        """Called when power outlet is switched OFF."""
        self.is_powered = False
        self.last_interrupted_phase = self.current_phase

    def handle_power_restore(self) -> None:
        """Called when power outlet is switched ON."""
        if self.fail_recovery:
            # Device remains dead / failed to boot
            self.is_powered = False
            return

        self.is_powered = True
        self.boot_count += 1
        self.boot_id = f"mock-boot-id-{self.boot_count}"

        # Resolve state and slot according to ARCHITECTURE.md section 3.6
        interrupted = self.last_interrupted_phase
        self.current_phase = "idle"

        if interrupted == "upload":
            # Incomplete upload discarded, original slot preserved, state idle
            self.active_slot = self.default_slot
            self.update_state = "Idle"
            self.uploaded_bytes = 0

        elif interrupted == "install":
            # Partial inactive slot write discarded/invalidated, boots default slot
            self.active_slot = self.default_slot
            self.update_state = "Failed"

        elif interrupted == "trial":
            # Trial attempt consumed. Cold power cycle boots default slot (tryboot one-shot)
            self.active_slot = self.default_slot
            self.trial_attempts = max(1, (self.trial_attempts or 0) + 1)
            self.update_state = "Failed" if self.trial_attempts >= 2 else "TrialInterrupted"

        elif interrupted == "commit":
            # Commit either completed atomically or reverted to previous slot, never undefined
            # We simulate atomic commit success
            self.default_slot = opposite_slot(self.default_slot)
            self.active_slot = self.default_slot
            self.current_version = self.target_version
            self.update_state = "Committed"

    def check_reachability(self, timeout: float = 3.0) -> bool:
        return self.is_powered

    def stream_upload(
        self,
        local_file: pathlib.Path,
        remote_cmd: str,
        timeout: Optional[float] = None,
    ) -> SshCommandResult:
        self.commands_executed.append(f"UPLOAD: {remote_cmd}")
        if not self.is_powered:
            return SshCommandResult(255, "", "SSH connection lost: host down")

        self.current_phase = "upload"
        if self.power_ctrl and self.power_ctrl.pending_cut_phase == "upload":
            start = time.monotonic()
            while self.is_powered and (time.monotonic() - start < 0.2):
                time.sleep(0.002)
            if not self.is_powered:
                return SshCommandResult(255, "", "SSH connection lost: host down")

        self.uploaded_bytes = local_file.stat().st_size
        self.update_state = "Staged"
        return SshCommandResult(0, "Staged successfully", "")

    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        self.commands_executed.append(cmd)
        if not self.is_powered:
            return SshCommandResult(255, "", "SSH connection refused")

        if "kill -0 1" in cmd:
            return SshCommandResult(0, "", "")

        if "mount" in cmd:
            if self.mount_device_override:
                dev = self.mount_device_override
            else:
                part = "mmcblk0p5" if self.active_slot == "root_a" else "mmcblk0p6"
                dev = f"/dev/{part}"
            return SshCommandResult(0, f"{dev} on / type ext4 ({self.mount_options})\n", "")

        if "cat /proc/sys/kernel/random/boot_id" in cmd:
            return SshCommandResult(0, self.boot_id, "")

        if "nx-sysd version --json" in cmd:
            data = {
                "version": self.current_version,
                "commit": "mock123",
                "slot": self.active_slot,
            }
            return SshCommandResult(0, json.dumps(data), "")

        if "nx-sysd update status --json" in cmd:
            data = {
                "state": self.update_state,
                "active_slot": self.active_slot,
                "default_slot": self.default_slot,
            }
            if self.trial_attempts is not None:
                data["trial_attempts"] = self.trial_attempts
            return SshCommandResult(0, json.dumps(data), "")

        if "nx-sysd update validate" in cmd:
            self.update_state = "Validated"
            return SshCommandResult(0, '{"state": "Validated"}', "")

        if "nx-sysd update install" in cmd:
            self.current_phase = "install"
            if self.power_ctrl and self.power_ctrl.pending_cut_phase == "install":
                start = time.monotonic()
                while self.is_powered and (time.monotonic() - start < 0.2):
                    time.sleep(0.002)
                if not self.is_powered:
                    return SshCommandResult(255, "", "SSH connection lost: host down")
            self.update_state = "Installed"
            return SshCommandResult(0, '{"state": "Installed"}', "")

        if "nx-sysd update trial" in cmd:
            self.current_phase = "trial"
            self.update_state = "Trial"
            self.trial_attempts = (self.trial_attempts or 0) + 1
            # Trial armed: reboot toggles active slot and activates target version
            self.active_slot = opposite_slot(self.active_slot)
            self.current_version = self.target_version
            self.boot_count += 1
            self.boot_id = f"mock-boot-id-{self.boot_count}"
            return SshCommandResult(0, "Trial armed, rebooting", "")

        if "nx-sysd update confirm" in cmd or "nx-sysd update commit" in cmd:
            self.current_phase = "commit"
            if self.power_ctrl and self.power_ctrl.pending_cut_phase == "commit":
                start = time.monotonic()
                while self.is_powered and (time.monotonic() - start < 0.2):
                    time.sleep(0.002)
                if not self.is_powered:
                    return SshCommandResult(255, "", "SSH connection lost: host down")
            self.default_slot = self.active_slot
            self.current_version = self.target_version
            self.update_state = "Committed"
            return SshCommandResult(0, '{"state": "Committed"}', "")

        return SshCommandResult(0, "", "")


class TestPowerLossTool(unittest.TestCase):
    """Test suite for power-loss resilience tool."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.swu_path = pathlib.Path(self.tmpdir.name) / "update.swu"
        create_mock_cpio_swu(self.swu_path, version="0.0.1-powerloss-target")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _create_runner_and_mock(
        self,
        cases: List[str],
        trials_per_case: int = 1,
        fail_recovery: bool = False,
    ) -> Tuple[PowerLossRunner, PowerLossMockDevice, MockPowerController]:
        mock_power = MockPowerController(verbose=False)
        mock_dev = PowerLossMockDevice(fail_recovery=fail_recovery, power_ctrl=mock_power)
        mock_power.on_cut_callback = mock_dev.handle_power_cut
        mock_power.on_restore_callback = mock_dev.handle_power_restore

        # Use very fast delay ranges in unit tests for speed
        test_delay_ranges = {
            "upload": (0.01, 0.03),
            "install": (0.01, 0.03),
            "trial": (0.01, 0.03),
            "commit": (0.01, 0.03),
        }

        runner = PowerLossRunner(
            runner=mock_dev,
            power_ctrl=mock_power,
            swu_path=self.swu_path,
            cases=cases,
            trials_per_case=trials_per_case,
            seed=42,
            power_cycle_delay=0.01,
            timeout_secs=5.0,
            poll_interval_secs=0.02,
            delay_ranges=test_delay_ranges,
            verbose=False,
        )
        return runner, mock_dev, mock_power

    def test_ac1_ac2_ac4_upload_interruption(self) -> None:
        """AC1, AC2, AC4: Interrupted upload recovers to original slot, state consistent, deploy succeeds."""
        runner, dev, _ = self._create_runner_and_mock(cases=["upload"], trials_per_case=1)
        report = runner.run()

        self.assertEqual(len(report.trials), 1)
        trial = report.trials[0]
        self.assertTrue(trial.success, f"Trial failed: {trial.error_message}")
        self.assertTrue(trial.ac1_complete_image)
        self.assertTrue(trial.ac2_state_consistent)
        self.assertTrue(trial.ac4_subsequent_deploy)
        self.assertFalse(trial.physical_access_needed)
        self.assertEqual(trial.resulting_slot, "root_a")
        self.assertEqual(trial.device_state, "idle")

    def test_ac1_ac2_ac4_install_interruption(self) -> None:
        """AC1, AC2, AC4: Interrupted install boots original slot, state failed/not bootable, deploy succeeds."""
        runner, dev, _ = self._create_runner_and_mock(cases=["install"], trials_per_case=1)
        report = runner.run()

        self.assertEqual(len(report.trials), 1)
        trial = report.trials[0]
        self.assertTrue(trial.success, f"Trial failed: {trial.error_message}")
        self.assertTrue(trial.ac1_complete_image)
        self.assertTrue(trial.ac2_state_consistent)
        self.assertTrue(trial.ac4_subsequent_deploy)
        self.assertEqual(trial.resulting_slot, "root_a")
        self.assertEqual(trial.device_state, "failed")

    def test_ac1_ac2_ac4_trial_interruption(self) -> None:
        """AC1, AC2, AC4: Interrupted trial consumes attempt, device boots, deploy succeeds."""
        runner, dev, _ = self._create_runner_and_mock(cases=["trial"], trials_per_case=1)
        report = runner.run()

        self.assertEqual(len(report.trials), 1)
        trial = report.trials[0]
        self.assertTrue(trial.success, f"Trial failed: {trial.error_message}")
        self.assertTrue(trial.ac1_complete_image)
        self.assertTrue(trial.ac2_state_consistent)
        self.assertTrue(trial.ac4_subsequent_deploy)
        self.assertGreaterEqual(trial.consumed_attempts or 0, 1)

    def test_ac1_ac2_ac4_commit_interruption(self) -> None:
        """AC1, AC2, AC4: Interrupted commit resolves cleanly, state committed or rolled back, never undefined."""
        runner, dev, _ = self._create_runner_and_mock(cases=["commit"], trials_per_case=1)
        report = runner.run()

        self.assertEqual(len(report.trials), 1)
        trial = report.trials[0]
        self.assertTrue(trial.success, f"Trial failed: {trial.error_message}")
        self.assertTrue(trial.ac1_complete_image)
        self.assertTrue(trial.ac2_state_consistent)
        self.assertTrue(trial.ac4_subsequent_deploy)
        self.assertIn(trial.device_state, ("committed", "failed", "rolled_back"))
        self.assertNotIn(trial.device_state, ("undefined", "unknown", "corrupt"))

    def test_ac1_fails_when_rootfs_mounted_read_write(self) -> None:
        """AC1: _verify_complete_image rejects rootfs mounted read-write or with mismatched partition."""
        runner, dev, _ = self._create_runner_and_mock(cases=["upload"], trials_per_case=1)

        # Baseline: root_a mounted ro passes
        self.assertTrue(runner._verify_complete_image("root_a"))

        # Failed case 1: mounted rw instead of ro
        dev.mount_options = "rw,relatime"
        self.assertFalse(runner._verify_complete_image("root_a"))

        # Failed case 2: mounted ro but wrong partition device
        dev.mount_options = "ro,relatime"
        dev.mount_device_override = "/dev/mmcblk0p9"
        self.assertFalse(runner._verify_complete_image("root_a"))

    def test_ac2_trial_missing_or_zero_attempts_fails(self) -> None:
        """AC2: Interrupted trial with missing or zero trial_attempts fails consistency check."""
        runner, dev, _ = self._create_runner_and_mock(cases=["trial"], trials_per_case=1)

        # Zero attempts must fail
        ok, details = runner._verify_ac2_state_consistency(
            "trial", "root_a", "root_b", "root_a", "trialinterrupted", {"trial_attempts": 0}
        )
        self.assertFalse(ok)
        self.assertIn("did not consume attempt", details)

        # Missing attempts must fail
        ok, details = runner._verify_ac2_state_consistency(
            "trial", "root_a", "root_b", "root_a", "trialinterrupted", {}
        )
        self.assertFalse(ok)
        self.assertIn("missing required 'trial_attempts'", details)

    def test_unreachable_device_before_trial_records_physical_access_and_blocks_gate(self) -> None:
        """AC3: Unreachable bench device before trial start is recorded as failed trial requiring physical access."""
        runner, dev, _ = self._create_runner_and_mock(cases=["upload"], trials_per_case=1)
        dev.is_powered = False  # Unreachable before trial start

        report = runner.run()
        self.assertEqual(len(report.trials), 1)
        trial = report.trials[0]
        self.assertFalse(trial.success)
        self.assertTrue(trial.physical_access_needed)
        self.assertEqual(report.physical_access_count, 1)
        self.assertEqual(report.failed_trials, 1)
        self.assertFalse(report.meets_gate_criteria)

    def test_upload_finished_before_cut_raises_error(self) -> None:
        """CQ-R1: Upload completing before scheduled power cut raises error and is not marked as upload interruption."""
        runner, dev, _ = self._create_runner_and_mock(cases=["upload"], trials_per_case=1)
        # Prevent mock device from waiting for cut during upload
        dev.power_ctrl = None  # Will return immediately before power cut fires

        trial = runner.run_trial("upload", 1)
        self.assertFalse(trial.success)
        self.assertIn("finished before scheduled power cut", trial.error_message or "")

    def test_ac3_ten_trials_varied_moments_all_cases(self) -> None:
        """AC3: 10 trials per case across all 4 cases (40 total) with varied seeded delays."""
        runner, dev, _ = self._create_runner_and_mock(
            cases=list(VALID_CASES), trials_per_case=DEFAULT_TRIALS_PER_CASE
        )
        report = runner.run()

        self.assertEqual(report.total_trials, 40)
        self.assertEqual(report.passed_trials, 40)
        self.assertEqual(report.failed_trials, 0)
        self.assertEqual(report.physical_access_count, 0)
        self.assertTrue(report.is_mock_power)
        # Mock power controller cannot satisfy hardware gate (CQ-R14, CQ-R2)
        self.assertFalse(report.meets_gate_criteria)

        # Real execution with same 40 passed trials passes hardware gate
        real_report = PowerLossReport(
            cases=list(VALID_CASES),
            trials_per_case=DEFAULT_TRIALS_PER_CASE,
            seed=42,
            start_time="",
            end_time="",
            total_duration=10.0,
            trials=report.trials,
            is_mock_power=False,
        )
        self.assertTrue(real_report.meets_gate_criteria)

        # Verify delay values are varied
        delays = [t.delay_seconds for t in report.trials]
        self.assertGreater(len(set(delays)), 10, "Delays should be varied across trials")

        # Verify markdown table formatting includes all 40 trials and non-gate status for mock
        md = report.format_markdown()
        self.assertIn("| `upload` | 10 |", md)
        self.assertIn("| `install` | 10 |", md)
        self.assertIn("| `trial` | 10 |", md)
        self.assertIn("| `commit` | 10 |", md)
        self.assertIn("NON-GATE (Simulated Power Controller)", md)

    def test_ac3_device_failure_flags_physical_access_and_blocks_gate(self) -> None:
        """AC3: Failure of device to recover flags physical access requirement and blocks gate."""
        runner, dev, _ = self._create_runner_and_mock(
            cases=["upload"], trials_per_case=1, fail_recovery=True
        )
        report = runner.run()

        self.assertFalse(report.meets_gate_criteria)
        self.assertEqual(report.failed_trials, 1)
        self.assertEqual(report.physical_access_count, 1)
        self.assertTrue(report.trials[0].physical_access_needed)

    def test_fewer_than_ten_trials_is_non_passing(self) -> None:
        """Fewer than 10 completed trials per case is marked as NON-GATE / does not meet gate."""
        runner, dev, _ = self._create_runner_and_mock(cases=list(VALID_CASES), trials_per_case=5)
        report = runner.run()

        self.assertEqual(report.total_trials, 20)
        self.assertEqual(report.passed_trials, 20)
        self.assertFalse(report.meets_gate_criteria, "Fewer than 10 trials per case must not meet gate")

    def test_credential_redaction_cq_r6(self) -> None:
        """CQ-R6: Sensitive tokens, passwords, and URL credentials are redacted."""
        secret_url = "http://admin:super_secret_pw@192.0.2.100/relay/0"
        secret_hdr = "Authorization: Bearer my-secret-auth-token-12345"

        redacted_url = redact_text(secret_url)
        redacted_hdr = redact_text(secret_hdr)

        self.assertNotIn("super_secret_pw", redacted_url)
        self.assertIn("[REDACTED]", redacted_url)
        self.assertNotIn("my-secret-auth-token-12345", redacted_hdr)
        self.assertIn("[REDACTED]", redacted_hdr)

    def test_command_power_controller_redacts_credentials(self) -> None:
        """CommandPowerController redacts credentials in verbose logging."""
        cmd = "curl -u admin:secret123 http://outlet.local/{action}"
        ctrl = CommandPowerController(cmd, verbose=True)

        old_stderr = sys.stderr
        capture_io = io.StringIO()
        try:
            sys.stderr = capture_io
            try:
                ctrl.power_off()
            except Exception:
                pass
        finally:
            sys.stderr = old_stderr

        logged = capture_io.getvalue()
        self.assertNotIn("secret123", logged)

    def test_cq_r7_unsafe_identifiers_rejected(self) -> None:
        """CQ-R7: Targets with option injection or shell metacharacters are rejected."""
        parser = build_parser()

        # Missing both --power-cmd and --mock-power rejected
        with self.assertRaises(SystemExit) as cm:
            parse_arguments(parser, ["192.0.2.1", "--swu", str(self.swu_path)])
        self.assertEqual(cm.exception.code, 2)

        # Target starting with '-' passed via '--' option delimiter
        with self.assertRaises(SystemExit) as cm:
            parse_arguments(parser, ["--mock-power", "--", "-badtarget", "--swu", str(self.swu_path)])
        self.assertEqual(cm.exception.code, 2)

        # User starting with '-'
        with self.assertRaises(SystemExit) as cm:
            parse_arguments(parser, ["192.0.2.1", "--mock-power", "--swu", str(self.swu_path), "--user", "-baduser"])
        self.assertEqual(cm.exception.code, 2)

        # Host with semicolon / command injection
        with self.assertRaises(SystemExit) as cm:
            parse_arguments(parser, ["host;rm -rf /", "--mock-power", "--swu", str(self.swu_path)])
        self.assertEqual(cm.exception.code, 2)

        # Negative trials
        with self.assertRaises(SystemExit) as cm:
            parse_arguments(parser, ["192.0.2.1", "--mock-power", "--swu", str(self.swu_path), "--trials", "0"])
        self.assertEqual(cm.exception.code, 2)

    def test_json_and_markdown_report_structure(self) -> None:
        """Verify report serialization formats for JSON and Markdown."""
        runner, _, _ = self._create_runner_and_mock(cases=["upload"], trials_per_case=1)
        report = runner.run()

        report_dict = report.to_dict()
        self.assertIn("summary", report_dict)
        self.assertIn("trials", report_dict)
        self.assertEqual(report_dict["summary"]["total_trials"], 1)

        # Check JSON serialization
        json_str = json.dumps(report_dict)
        self.assertIn("upload", json_str)

        # Check Markdown format
        md_text = report.format_markdown()
        self.assertIn("# Power-Loss Resilience Verification Report", md_text)
        self.assertIn("| Case | Trial | Moment / Delay |", md_text)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPowerLossTool)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
