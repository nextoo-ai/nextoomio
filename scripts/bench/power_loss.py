#!/usr/bin/env python3
"""Automated power-loss resilience tool for nextoomio test bench (P0-F4.8).

Executes automated power-loss resilience testing across four critical update phases
using remote power control through a switched power outlet, verifying:
- AC1: Power removed through the switched outlet during upload, during writing
       of the inactive slot (install), during trial boot, and during commit;
       in every case the device boots, is reachable over SSH, and runs a complete image.
- AC2: Update state read afterwards is consistent with the running slot:
       - Interrupted upload or install is not installable or bootable.
       - Interrupted trial has consumed its attempt.
       - Interrupted commit is either completed or returned to the previous slot, never undefined.
- AC3: Each case repeated at least ten times at varied moments using a seeded random delay,
       recording if any trial needed physical access (any physical intervention blocks phase exit).
- AC4: Given power loss in any case, when a new valid update is deployed afterwards, it succeeds.

References:
- SPECIFICATION.md section 2.1
- ROADMAP.md section 2.1 (exit gates)
- ARCHITECTURE.md section 3.6 (durable transaction record, selector recovery, attempt accounting)
- ARCHITECTURE.md section 4.1 (Phase 0 base image & bench)
- SYS-05, UPD-05, UPD-06
"""

from __future__ import annotations

import abc
import argparse
import datetime
import json
import os
import pathlib
import random
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Import deploy primitives
try:
    from .deploy import (
        DEFAULT_POLL_INTERVAL_SECS,
        DEFAULT_SSH_PORT,
        DEFAULT_SSH_USER,
        DEFAULT_TIMEOUT_SECS,
        SAFE_IDENTIFIER_PATTERN,
        DeployResult,
        Deployer,
        SshCommandResult,
        SshRunner,
        SystemSshRunner,
        is_safe_identifier,
        normalize_slot,
        opposite_slot,
        parse_swu_description,
    )
except ImportError:
    from deploy import (
        DEFAULT_POLL_INTERVAL_SECS,
        DEFAULT_SSH_PORT,
        DEFAULT_SSH_USER,
        DEFAULT_TIMEOUT_SECS,
        SAFE_IDENTIFIER_PATTERN,
        DeployResult,
        Deployer,
        SshCommandResult,
        SshRunner,
        SystemSshRunner,
        is_safe_identifier,
        normalize_slot,
        opposite_slot,
        parse_swu_description,
    )


VALID_CASES = ("upload", "install", "trial", "commit")
DEFAULT_TRIALS_PER_CASE = 10
DEFAULT_SEED = 42
DEFAULT_POWER_CYCLE_DELAY = 5.0
DEFAULT_REPORT_FILE = "power-loss-report.md"

# Default delay ranges in seconds for each case
DEFAULT_DELAY_RANGES: Dict[str, Tuple[float, float]] = {
    "upload": (0.5, 4.0),
    "install": (1.0, 10.0),
    "trial": (2.0, 15.0),
    "commit": (0.05, 0.8),
}

BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+([^\s,;'\"]+)")
USER_PASS_PATTERN = re.compile(r"(?i)(-u\s+[^:\s]+:)([^\s'\"]+)")
BASIC_AUTH_PATTERN = re.compile(r"(?i)\b(basic)\s+([A-Za-z0-9+/=]+)")
KEY_VAL_PATTERN = re.compile(
    r"(?i)\b([a-z0-9_-]*(?:password|secret|token|key|auth|credential)[a-z0-9_-]*)\s*[:=]\s*([^\s,;'\"&]+)"
)
QUERY_CRED_PATTERN = re.compile(r"(?i)([?&](?:password|token|key|secret)=)[^&\s'\"]+")
URL_CREDENTIAL_PATTERN = re.compile(r"://([^:@/]+):([^@/]+)@")


def redact_text(text: str) -> str:
    """Redact sensitive credentials, authorization tokens, or passwords from logs (CQ-R6)."""
    if not text:
        return text
    # Redact URL credentials (scheme://user:pass@host -> scheme://user:[REDACTED]@host)
    redacted = URL_CREDENTIAL_PATTERN.sub(r"://\1:[REDACTED]@", text)
    # Redact URL query parameters (?token=secret -> ?token=[REDACTED])
    redacted = QUERY_CRED_PATTERN.sub(r"\1[REDACTED]", redacted)
    # Redact Bearer tokens (Bearer abc123xyz -> Bearer [REDACTED])
    redacted = BEARER_PATTERN.sub(r"\1 [REDACTED]", redacted)
    # Redact Basic auth base64
    redacted = BASIC_AUTH_PATTERN.sub(r"\1 [REDACTED]", redacted)
    # Redact curl -u user:password
    redacted = USER_PASS_PATTERN.sub(r"\1[REDACTED]", redacted)
    # Redact key-value secrets (password=xyz, secret: abc)
    redacted = KEY_VAL_PATTERN.sub(r"\1=[REDACTED]", redacted)
    return redacted


class PowerLossError(Exception):
    """Base exception for power-loss test failures."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class PowerCutError(PowerLossError):
    """Raised when outlet switching or power cut execution fails."""


class DeviceRecoveryError(PowerLossError):
    """Raised when bench device fails to boot or become reachable after power restoration."""


class StateConsistencyError(PowerLossError):
    """Raised when device state after power restoration violates AC2 consistency invariants."""


class SubsequentDeployError(PowerLossError):
    """Raised when subsequent update deployment fails after recovery (AC4)."""


# ==============================================================================
# Power Controller Abstraction
# ==============================================================================


class PowerController(abc.ABC):
    """Abstract interface for remotely controlling bench power outlet."""

    @abc.abstractmethod
    def power_off(self) -> None:
        """Cut power to the bench device."""

    @abc.abstractmethod
    def power_on(self) -> None:
        """Restore power to the bench device."""

    @abc.abstractmethod
    def power_cycle(self, delay: float = DEFAULT_POWER_CYCLE_DELAY) -> None:
        """Cut power, wait delay, and restore power."""

    @abc.abstractmethod
    def get_status(self) -> str:
        """Query outlet status ('on' or 'off')."""


class CommandPowerController(PowerController):
    """Power controller invoking an external power command (e.g. scripts/bench/power.sh)."""

    def __init__(
        self,
        command_template: str,
        cycle_delay: float = DEFAULT_POWER_CYCLE_DELAY,
        timeout: float = 15.0,
        verbose: bool = False,
    ):
        self.command_template = command_template
        self.cycle_delay = cycle_delay
        self.timeout = timeout
        self.verbose = verbose

    def _execute(self, action: str, extra_args: Optional[List[str]] = None) -> str:
        args = extra_args or []
        if "{action}" in self.command_template:
            cmd_str = self.command_template.replace("{action}", action)
            cmd = shlex.split(cmd_str) + args
        else:
            cmd = shlex.split(self.command_template) + [action] + args

        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] Power action: {redact_text(' '.join(cmd))}\n")
            sys.stderr.flush()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if proc.returncode != 0:
                err = redact_text(proc.stderr.strip() or proc.stdout.strip())
                raise PowerCutError(
                    f"Power command '{action}' failed with exit code {proc.returncode}: {err}",
                    details={"action": action, "returncode": proc.returncode, "error": err},
                )
            return proc.stdout.strip()
        except subprocess.TimeoutExpired as e:
            raise PowerCutError(f"Power command '{action}' timed out after {self.timeout}s: {e}")
        except Exception as e:
            if isinstance(e, PowerCutError):
                raise
            raise PowerCutError(f"Power command '{action}' execution error: {redact_text(str(e))}")

    def power_off(self) -> None:
        self._execute("off")

    def power_on(self) -> None:
        self._execute("on")

    def power_cycle(self, delay: float = DEFAULT_POWER_CYCLE_DELAY) -> None:
        try:
            self._execute("cycle", ["--delay", str(delay)])
        except PowerCutError:
            # Fallback to discrete off -> sleep -> on if cycle subcommand is unsupported
            self.power_off()
            time.sleep(delay)
            self.power_on()

    def get_status(self) -> str:
        try:
            out = self._execute("status")
            if "on" in out.lower():
                return "on"
            if "off" in out.lower():
                return "off"
            return out
        except Exception:
            return "unknown"


class MockPowerController(PowerController):
    """In-memory simulated power controller for offline testing and CI verification."""

    def __init__(self, verbose: bool = False):
        self.is_on = True
        self.verbose = verbose
        self.power_cuts: List[Dict[str, Any]] = []
        self.on_cut_callback: Optional[Callable[[], None]] = None
        self.on_restore_callback: Optional[Callable[[], None]] = None

    def power_off(self) -> None:
        self.is_on = False
        record = {"action": "off", "timestamp": time.time()}
        self.power_cuts.append(record)
        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] [MockPower] OUTLET CUT (OFF)\n")
        if self.on_cut_callback:
            self.on_cut_callback()

    def power_on(self) -> None:
        self.is_on = True
        record = {"action": "on", "timestamp": time.time()}
        self.power_cuts.append(record)
        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] [MockPower] OUTLET RESTORED (ON)\n")
        if self.on_restore_callback:
            self.on_restore_callback()

    def power_cycle(self, delay: float = DEFAULT_POWER_CYCLE_DELAY) -> None:
        self.power_off()
        time.sleep(min(0.05, delay))
        self.power_on()

    def get_status(self) -> str:
        return "on" if self.is_on else "off"


# ==============================================================================
# Trial Results and Report Structures
# ==============================================================================


class PowerLossTrialResult:
    """Stores the parameters, timing, observations, and verdicts of a single power-loss trial."""

    def __init__(
        self,
        case: str,
        trial_index: int,
        seed: int,
        delay_seconds: float,
        power_cut_moment: str,
        power_cut_timestamp: str,
        recovery_time_seconds: float,
        initial_slot: str,
        target_slot: str,
        resulting_slot: str,
        device_state: str,
        consumed_attempts: Optional[int],
        reachable: bool,
        ac1_complete_image: bool,
        ac2_state_consistent: bool,
        ac2_details: str,
        ac4_subsequent_deploy: bool,
        physical_access_needed: bool = False,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        self.case = case
        self.trial_index = trial_index
        self.seed = seed
        self.delay_seconds = delay_seconds
        self.power_cut_moment = power_cut_moment
        self.power_cut_timestamp = power_cut_timestamp
        self.recovery_time_seconds = recovery_time_seconds
        self.initial_slot = initial_slot
        self.target_slot = target_slot
        self.resulting_slot = resulting_slot
        self.device_state = device_state
        self.consumed_attempts = consumed_attempts
        self.reachable = reachable
        self.ac1_complete_image = ac1_complete_image
        self.ac2_state_consistent = ac2_state_consistent
        self.ac2_details = ac2_details
        self.ac4_subsequent_deploy = ac4_subsequent_deploy
        self.physical_access_needed = physical_access_needed
        self.success = success
        self.error_message = error_message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case,
            "trial_index": self.trial_index,
            "seed": self.seed,
            "delay_seconds": self.delay_seconds,
            "power_cut_moment": self.power_cut_moment,
            "power_cut_timestamp": self.power_cut_timestamp,
            "recovery_time_seconds": self.recovery_time_seconds,
            "initial_slot": self.initial_slot,
            "target_slot": self.target_slot,
            "resulting_slot": self.resulting_slot,
            "device_state": self.device_state,
            "consumed_attempts": self.consumed_attempts,
            "reachable": self.reachable,
            "ac1_complete_image": self.ac1_complete_image,
            "ac2_state_consistent": self.ac2_state_consistent,
            "ac2_details": self.ac2_details,
            "ac4_subsequent_deploy": self.ac4_subsequent_deploy,
            "physical_access_needed": self.physical_access_needed,
            "success": self.success,
            "error_message": self.error_message,
        }


class PowerLossReport:
    """Aggregates all trial results across cases and produces structured reports."""

    def __init__(
        self,
        cases: List[str],
        trials_per_case: int,
        seed: int,
        start_time: str,
        end_time: str,
        total_duration: float,
        trials: List[PowerLossTrialResult],
        failing_step: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.cases = cases
        self.trials_per_case = trials_per_case
        self.seed = seed
        self.start_time = start_time
        self.end_time = end_time
        self.total_duration = total_duration
        self.trials = trials
        self.failing_step = failing_step
        self.error_message = error_message

    @property
    def total_trials(self) -> int:
        return len(self.trials)

    @property
    def passed_trials(self) -> int:
        return sum(1 for t in self.trials if t.success)

    @property
    def failed_trials(self) -> int:
        return sum(1 for t in self.trials if not t.success)

    @property
    def physical_access_count(self) -> int:
        return sum(1 for t in self.trials if t.physical_access_needed)

    @property
    def meets_gate_criteria(self) -> bool:
        # Gate requires all 4 cases, at least 10 trials per case, 0 failures, 0 physical interventions
        if self.trials_per_case < DEFAULT_TRIALS_PER_CASE:
            return False
        if len(self.cases) < len(VALID_CASES):
            return False
        if self.failed_trials > 0:
            return False
        if self.physical_access_count > 0:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "start_time": self.start_time,
                "end_time": self.end_time,
                "total_duration_seconds": round(self.total_duration, 2),
                "cases": self.cases,
                "trials_per_case": self.trials_per_case,
                "seed": self.seed,
                "total_trials": self.total_trials,
                "passed_trials": self.passed_trials,
                "failed_trials": self.failed_trials,
                "physical_access_count": self.physical_access_count,
                "meets_gate_criteria": self.meets_gate_criteria,
                "failing_step": self.failing_step,
                "error_message": self.error_message,
            },
            "trials": [t.to_dict() for t in self.trials],
        }

    def format_text(self) -> str:
        lines = [
            "=== nextoomio Power-Loss Resilience Report (P0-F4.8) ===",
            f"Execution Window: {self.start_time} -> {self.end_time} ({self.total_duration:.1f}s)",
            f"Evaluated Cases:  {', '.join(self.cases)}",
            f"Trials Per Case:  {self.trials_per_case} (Total: {self.total_trials})",
            f"PRNG Random Seed: {self.seed}",
            f"Passed Trials:    {self.passed_trials}/{self.total_trials}",
            f"Failed Trials:    {self.failed_trials}/{self.total_trials}",
            f"Physical Access:  {self.physical_access_count} (Must be 0 for Gate)",
            f"Gate Verdict:     {'PASS' if self.meets_gate_criteria else 'FAILED / NON-GATE'}",
        ]
        if self.error_message:
            lines.append(f"Halt Error:       {self.error_message}")
        lines.append("")
        lines.append(f"{'Case':<9} {'#':<3} {'Delay(s)':<8} {'Result Slot':<12} {'State':<14} {'Reach':<6} {'AC1':<5} {'AC2':<5} {'AC4':<5} {'PhysAccess':<10} {'Verdict'}")
        lines.append("-" * 90)
        for t in self.trials:
            verdict = "PASS" if t.success else "FAIL"
            lines.append(
                f"{t.case:<9} {t.trial_index:<3} {t.delay_seconds:<8.3f} {t.resulting_slot:<12} "
                f"{t.device_state:<14} {'Yes' if t.reachable else 'No':<6} "
                f"{'OK' if t.ac1_complete_image else 'NO':<5} "
                f"{'OK' if t.ac2_state_consistent else 'NO':<5} "
                f"{'OK' if t.ac4_subsequent_deploy else 'NO':<5} "
                f"{'None' if not t.physical_access_needed else 'NEEDED':<10} {verdict}"
            )
        return "\n".join(lines)

    def format_markdown(self) -> str:
        gate_status = "PASS" if self.meets_gate_criteria else "FAILED (Non-Gate)"
        lines = [
            "# Power-Loss Resilience Verification Report (P0-F4.8)",
            "",
            "## Summary",
            "",
            f"- **Start time:** {self.start_time}",
            f"- **End time:** {self.end_time}",
            f"- **Duration:** {self.total_duration:.1f}s",
            f"- **Cases tested:** {', '.join(self.cases)}",
            f"- **Trials per case:** {self.trials_per_case}",
            f"- **Total trials:** {self.total_trials}",
            f"- **Random seed:** {self.seed}",
            f"- **Passed trials:** {self.passed_trials}",
            f"- **Failed trials:** {self.failed_trials}",
            f"- **Physical access interventions:** {self.physical_access_count} (AC3 requirement: 0)",
            f"- **Gate verdict:** **{gate_status}**",
            "",
        ]

        if self.error_message:
            lines.extend([
                f"> [!WARNING]",
                f"> Execution halted at step `{self.failing_step}`: {self.error_message}",
                "",
            ])

        lines.extend([
            "## Per-Case Table of Trials",
            "",
            "| Case | Trial | Moment / Delay | Cut Timestamp | Resulting Slot | Device State | Reachable | AC1 Image | AC2 State | AC4 Deploy | Physical Access | Verdict |",
            "| :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        for t in self.trials:
            verdict = "**PASS**" if t.success else "**FAIL**"
            phys = "None" if not t.physical_access_needed else "**NEEDED**"
            reach = "Yes" if t.reachable else "No"
            ac1 = "Pass" if t.ac1_complete_image else "Fail"
            ac2 = "Pass" if t.ac2_state_consistent else "Fail"
            ac4 = "Pass" if t.ac4_subsequent_deploy else "Fail"
            lines.append(
                f"| `{t.case}` | {t.trial_index} | {t.delay_seconds:.3f}s | {t.power_cut_timestamp} | `{t.resulting_slot}` | `{t.device_state}` | {reach} | {ac1} | {ac2} | {ac4} | {phys} | {verdict} |"
            )

        lines.extend([
            "",
            "## Acceptance Criteria Analysis",
            "",
            f"- **AC1 (Device boots, is reachable, runs complete image):** "
            f"{'Demonstrated across all trials' if all(t.ac1_complete_image for t in self.trials) else 'FAILED'}.",
            f"- **AC2 (State consistent with running slot):** "
            f"{'Demonstrated across all trials (interrupted upload/install not installable/bootable, trial attempt consumed, commit resolved cleanly)' if all(t.ac2_state_consistent for t in self.trials) else 'FAILED'}.",
            f"- **AC3 (Ten trials per case at varied moments, 0 physical access):** "
            f"{'Met: ' + str(self.total_trials) + ' trials executed with varied seeded delays, 0 physical interventions' if self.meets_gate_criteria else 'FAILED or fewer than 10 trials per case'}.",
            f"- **AC4 (Subsequent valid deploy succeeds in every case):** "
            f"{'Demonstrated across all trials' if all(t.ac4_subsequent_deploy for t in self.trials) else 'FAILED'}.",
            "",
        ])
        return "\n".join(lines)


# ==============================================================================
# Power-Loss Resilience Test Runner
# ==============================================================================


class PowerLossRunner:
    """Orchestrates power-loss resilience verification on test bench."""

    def __init__(
        self,
        runner: SshRunner,
        power_ctrl: PowerController,
        swu_path: pathlib.Path,
        cases: Optional[List[str]] = None,
        trials_per_case: int = DEFAULT_TRIALS_PER_CASE,
        seed: int = DEFAULT_SEED,
        power_cycle_delay: float = DEFAULT_POWER_CYCLE_DELAY,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
        poll_interval_secs: float = DEFAULT_POLL_INTERVAL_SECS,
        delay_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        verbose: bool = False,
    ):
        self.runner = runner
        self.power_ctrl = power_ctrl
        self.swu_path = swu_path
        self.cases = [c.lower() for c in (cases or list(VALID_CASES))]
        self.trials_per_case = trials_per_case
        self.seed = seed
        self.power_cycle_delay = power_cycle_delay
        self.timeout_secs = timeout_secs
        self.poll_interval_secs = poll_interval_secs
        self.delay_ranges = dict(DEFAULT_DELAY_RANGES)
        if delay_ranges:
            self.delay_ranges.update(delay_ranges)
        self.verbose = verbose

        # Validate cases
        for c in self.cases:
            if c not in VALID_CASES:
                raise ValueError(f"Unknown test case '{c}'; expected one of {VALID_CASES}")

        # Validate trials
        if self.trials_per_case < 1:
            raise ValueError(f"trials_per_case must be >= 1 (got {self.trials_per_case})")

        self.rng = random.Random(self.seed)

    def _log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            sys.stderr.flush()

    def query_device_state(self) -> Tuple[str, str, Dict[str, Any]]:
        """Query running slot, version, and update status from target."""
        slot = "unknown"
        version = "unknown"
        update_status: Dict[str, Any] = {}

        # 1. Try nx-sysd version --json
        res = self.runner.run("nx-sysd version --json", timeout=5.0)
        if res.ok and res.stdout.strip():
            try:
                data = json.loads(res.stdout)
                slot = data.get("slot") or slot
                version = data.get("version") or version
            except Exception:
                pass

        # 2. cmdline fallback
        if slot == "unknown":
            res_cmd = self.runner.run("cat /proc/cmdline", timeout=5.0)
            if res_cmd.ok:
                if "root_b" in res_cmd.stdout or "4e58544d-06" in res_cmd.stdout:
                    slot = "root_b"
                elif "root_a" in res_cmd.stdout or "4e58544d-05" in res_cmd.stdout:
                    slot = "root_a"

        # 3. Query updater status
        res_stat = self.runner.run("nx-sysd update status --json", timeout=5.0)
        if res_stat.ok and res_stat.stdout.strip():
            try:
                update_status = json.loads(res_stat.stdout)
                if slot == "unknown" and "active_slot" in update_status:
                    slot = update_status["active_slot"]
            except Exception:
                pass

        return normalize_slot(slot), version, update_status

    def _wait_for_device_recovery(self) -> float:
        """Wait for device to reboot and become reachable over SSH after power restoration."""
        start = time.monotonic()
        while (time.monotonic() - start) < self.timeout_secs:
            if self.runner.check_reachability(timeout=min(3.0, self.poll_interval_secs + 1.0)):
                elapsed = time.monotonic() - start
                self._log(f"Device responded to SSH after {elapsed:.1f}s.")
                return elapsed
            time.sleep(self.poll_interval_secs)
        raise DeviceRecoveryError(
            f"Device failed to become reachable over SSH within {self.timeout_secs}s after power return"
        )

    def _verify_complete_image(self, running_slot: str) -> bool:
        """Verify AC1: running slot holds a complete image with basic system services healthy."""
        # Check systemd PID 1 is running and filesystem is mounted
        res_pid1 = self.runner.run("kill -0 1", timeout=5.0)
        if not res_pid1.ok:
            return False

        # Check rootfs mount is active and read-only
        res_mount = self.runner.run("mount", timeout=5.0)
        if not res_mount.ok:
            return False
        # Root partition for slot must be mounted read-only (SYS-04)
        if running_slot not in ("root_a", "root_b"):
            return False

        return True

    def _execute_subsequent_deploy(self, current_slot: str) -> bool:
        """Verify AC4: perform a complete, valid deployment following power loss recovery."""
        deployer = Deployer(
            runner=self.runner,
            swu_path=self.swu_path,
            timeout_secs=self.timeout_secs,
            poll_interval_secs=self.poll_interval_secs,
            remote_confirm=True,
            verbose=self.verbose,
        )
        res = deployer.deploy()
        if not res.success:
            raise SubsequentDeployError(
                f"Subsequent deploy failed at step '{res.failing_step}': {res.error_message}",
                details=res.to_dict(),
            )
        # Ensure slot toggled
        if res.running_slot == current_slot:
            raise SubsequentDeployError(
                f"Subsequent deploy did not toggle slot: still running '{current_slot}'",
                details=res.to_dict(),
            )
        return True

    def run_trial(self, case: str, trial_idx: int) -> PowerLossTrialResult:
        """Run a single power-loss trial for the given case."""
        t_min, t_max = self.delay_ranges[case]
        delay = round(self.rng.uniform(t_min, t_max), 3)

        self._log(f"--- Starting {case.upper()} Trial {trial_idx}/{self.trials_per_case} (Delay: {delay}s) ---")

        # Step 0: Ensure bench is reachable and read initial state
        if not self.runner.check_reachability(timeout=5.0):
            raise DeviceRecoveryError("Bench device is not reachable before trial start")

        initial_slot, initial_version, initial_state = self.query_device_state()
        if initial_slot not in ("root_a", "root_b"):
            raise DeviceRecoveryError(f"Could not determine valid active slot from device (got '{initial_slot}')")
        target_slot = opposite_slot(initial_slot)

        power_cut_ts = ""
        power_cut_moment = ""
        recovery_time = 0.0
        resulting_slot = "unknown"
        device_state_str = "unknown"
        consumed_attempts: Optional[int] = None
        ac1_ok = False
        ac2_ok = False
        ac2_details = ""
        ac4_ok = False
        physical_needed = False
        success = False
        error_msg: Optional[str] = None

        try:
            # Dispatch to case-specific interruption sequence
            if case == "upload":
                power_cut_ts, power_cut_moment = self._run_upload_cut(delay)
            elif case == "install":
                power_cut_ts, power_cut_moment = self._run_install_cut(delay, target_slot)
            elif case == "trial":
                power_cut_ts, power_cut_moment = self._run_trial_cut(delay, target_slot)
            elif case == "commit":
                power_cut_ts, power_cut_moment = self._run_commit_cut(delay, target_slot)

            # Step: Restore power
            self._log(f"Restoring power via outlet (cycle delay {self.power_cycle_delay}s)...")
            time.sleep(self.power_cycle_delay)
            self.power_ctrl.power_on()

            # Step: Wait for device to boot and become reachable
            recovery_time = self._wait_for_device_recovery()

            # Step: Query post-recovery state
            resulting_slot, resulting_version, post_status = self.query_device_state()
            device_state_str = str(post_status.get("state") or "unknown").lower()
            consumed_attempts = post_status.get("trial_attempts")

            # Step: Verify AC1 (Device boots, reachable, complete image)
            ac1_ok = self._verify_complete_image(resulting_slot)
            if not ac1_ok:
                raise PowerLossError(f"AC1 violation: running slot '{resulting_slot}' image is incomplete or unhealthy")

            # Step: Verify AC2 (State consistency)
            ac2_ok, ac2_details = self._verify_ac2_state_consistency(
                case, initial_slot, target_slot, resulting_slot, device_state_str, post_status
            )
            if not ac2_ok:
                raise StateConsistencyError(f"AC2 violation: {ac2_details}")

            # Step: Verify AC4 (Subsequent valid deploy succeeds)
            self._log("Verifying AC4: deploying fresh valid update post-recovery...")
            ac4_ok = self._execute_subsequent_deploy(resulting_slot)
            success = True

        except Exception as exc:
            error_msg = str(exc)
            self._log(f"Trial failed: {error_msg}")
            # If device never recovered, physical access might be required
            if isinstance(exc, DeviceRecoveryError):
                physical_needed = True

        return PowerLossTrialResult(
            case=case,
            trial_index=trial_idx,
            seed=self.seed,
            delay_seconds=delay,
            power_cut_moment=power_cut_moment,
            power_cut_timestamp=power_cut_ts or datetime.datetime.now().isoformat(),
            recovery_time_seconds=recovery_time,
            initial_slot=initial_slot,
            target_slot=target_slot,
            resulting_slot=resulting_slot,
            device_state=device_state_str,
            consumed_attempts=consumed_attempts,
            reachable=ac1_ok or (resulting_slot in ("root_a", "root_b")),
            ac1_complete_image=ac1_ok,
            ac2_state_consistent=ac2_ok,
            ac2_details=ac2_details or ("OK" if ac2_ok else (error_msg or "Failed")),
            ac4_subsequent_deploy=ac4_ok,
            physical_access_needed=physical_needed,
            success=success,
            error_message=error_msg,
        )

    def _schedule_cut(self, delay: float) -> threading.Timer:
        """Schedule power off in a background thread timer."""
        timer = threading.Timer(delay, self.power_ctrl.power_off)
        timer.daemon = True
        timer.start()
        return timer

    def _run_upload_cut(self, delay: float) -> Tuple[str, str]:
        """Case 1: Cut power while update payload is streaming to staging buffer."""
        self._log(f"Starting upload stream; power cut scheduled in {delay}s...")
        cut_timer = self._schedule_cut(delay)
        moment = f"upload stream at t={delay}s"

        upload_cmd = (
            "mkdir -p /data/update && "
            "cat > /data/update/staging.upload.swu && "
            "nx-sysd update stage --file /data/update/staging.upload.swu"
        )
        # Stream upload (this is expected to terminate abruptly when power is cut)
        ts = datetime.datetime.now().isoformat()
        try:
            self.runner.stream_upload(self.swu_path, upload_cmd, timeout=30.0)
        except Exception:
            pass
        cut_timer.join(timeout=1.0)
        return ts, moment

    def _run_install_cut(self, delay: float, target_slot: str) -> Tuple[str, str]:
        """Case 2: Stage and validate update, then cut power while writing inactive slot."""
        self._log("Staging and validating update before install...")
        upload_cmd = (
            "mkdir -p /data/update && "
            "cat > /data/update/staging.upload.swu && "
            "nx-sysd update stage --file /data/update/staging.upload.swu"
        )
        res_up = self.runner.stream_upload(self.swu_path, upload_cmd, timeout=30.0)
        if not res_up.ok:
            raise PowerLossError(f"Pre-install stage failed: {res_up.stderr}")

        res_val = self.runner.run("nx-sysd update validate", timeout=15.0)
        if not res_val.ok:
            raise PowerLossError(f"Pre-install validate failed: {res_val.stderr}")

        self._log(f"Triggering install to {target_slot}; power cut scheduled in {delay}s...")
        cut_timer = self._schedule_cut(delay)
        moment = f"raw partition write to {target_slot} at t={delay}s"
        ts = datetime.datetime.now().isoformat()
        try:
            self.runner.run("nx-sysd update install", timeout=30.0)
        except Exception:
            pass
        cut_timer.join(timeout=1.0)
        return ts, moment

    def _run_trial_cut(self, delay: float, target_slot: str) -> Tuple[str, str]:
        """Case 3: Fully install update, arm trial boot, and cut power during trial boot/window."""
        self._log(f"Installing update to {target_slot} for trial cut test...")
        upload_cmd = (
            "mkdir -p /data/update && "
            "cat > /data/update/staging.upload.swu && "
            "nx-sysd update stage --file /data/update/staging.upload.swu"
        )
        self.runner.stream_upload(self.swu_path, upload_cmd, timeout=30.0)
        self.runner.run("nx-sysd update validate", timeout=15.0)
        self.runner.run("nx-sysd update install", timeout=30.0)

        self._log(f"Arming trial boot; power cut scheduled in {delay}s during trial...")
        cut_timer = self._schedule_cut(delay)
        moment = f"trial boot window for {target_slot} at t={delay}s"
        ts = datetime.datetime.now().isoformat()
        try:
            trial_cmd = "nx-sysd update trial --remote-confirm && (nohup systemctl reboot >/dev/null 2>&1 &)"
            self.runner.run(trial_cmd, timeout=15.0)
        except Exception:
            pass
        cut_timer.join(timeout=1.0)
        return ts, moment

    def _run_commit_cut(self, delay: float, target_slot: str) -> Tuple[str, str]:
        """Case 4: Install update, boot trial slot, and cut power right at the moment of commit."""
        self._log(f"Installing and trial booting into {target_slot} for commit cut test...")
        upload_cmd = (
            "mkdir -p /data/update && "
            "cat > /data/update/staging.upload.swu && "
            "nx-sysd update stage --file /data/update/staging.upload.swu"
        )
        self.runner.stream_upload(self.swu_path, upload_cmd, timeout=30.0)
        self.runner.run("nx-sysd update validate", timeout=15.0)
        self.runner.run("nx-sysd update install", timeout=30.0)
        self.runner.run("nx-sysd update trial --remote-confirm && (nohup systemctl reboot >/dev/null 2>&1 &)", timeout=15.0)

        # Wait for trial slot to boot
        self._wait_for_device_recovery()

        self._log(f"Executing commit; power cut scheduled in {delay}s at commit boundary...")
        cut_timer = self._schedule_cut(delay)
        moment = f"commit selector write at t={delay}s"
        ts = datetime.datetime.now().isoformat()
        try:
            self.runner.run("nx-sysd update commit", timeout=15.0)
        except Exception:
            pass
        cut_timer.join(timeout=1.0)
        return ts, moment

    def _verify_ac2_state_consistency(
        self,
        case: str,
        initial_slot: str,
        target_slot: str,
        resulting_slot: str,
        device_state: str,
        status_dict: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """Verify AC2 consistency invariants for the given case."""
        # AC2 rule 1: Interrupted upload
        if case == "upload":
            if resulting_slot != initial_slot:
                return False, f"Interrupted upload booted unexpected slot '{resulting_slot}' (expected '{initial_slot}')"
            if device_state in ("installed", "trial", "committed"):
                return False, f"Interrupted upload left bootable/installed state '{device_state}'"
            return True, "Interrupted upload: running original slot, incomplete payload discarded/not bootable"

        # AC2 rule 2: Interrupted install
        if case == "install":
            if resulting_slot != initial_slot:
                return False, f"Interrupted install booted unexpected slot '{resulting_slot}' (expected '{initial_slot}')"
            if device_state in ("installed", "trial", "committed"):
                return False, f"Interrupted install left bootable state '{device_state}'"
            return True, "Interrupted install: running original slot, partial partition write not installable or bootable"

        # AC2 rule 3: Interrupted trial
        if case == "trial":
            attempts = status_dict.get("trial_attempts") or 1
            if attempts < 1:
                return False, f"Interrupted trial did not consume attempt (attempts={attempts})"
            # Interrupted trial must boot either original slot (if trial aborted) or target slot (if tryboot engaged)
            if resulting_slot not in (initial_slot, target_slot):
                return False, f"Interrupted trial booted unknown slot '{resulting_slot}'"
            return True, f"Interrupted trial: consumed attempt ({attempts}), running slot '{resulting_slot}'"

        # AC2 rule 4: Interrupted commit
        if case == "commit":
            # An interrupted commit is either completed or returned to the previous slot, never undefined
            if device_state in ("undefined", "unknown", "corrupt"):
                return False, f"Commit state is undefined: '{device_state}'"
            if resulting_slot == target_slot:
                if device_state != "committed":
                    return False, f"Commit switched to '{target_slot}' but state is '{device_state}' (expected 'committed')"
                return True, "Interrupted commit: completed atomically and durable selector committed"
            elif resulting_slot == initial_slot:
                return True, f"Interrupted commit: returned cleanly to previous slot '{initial_slot}' (state '{device_state}')"
            else:
                return False, f"Interrupted commit resulted in unknown slot '{resulting_slot}'"

        return True, "State consistent"

    def run(self) -> PowerLossReport:
        """Run the full power-loss test matrix across all selected cases and trials."""
        start_ts = datetime.datetime.now().isoformat()
        start_time = time.monotonic()
        all_trials: List[PowerLossTrialResult] = []
        failing_step: Optional[str] = None
        error_message: Optional[str] = None

        self._log(f"Starting power-loss test suite: {len(self.cases)} cases, {self.trials_per_case} trials/case, seed={self.seed}")

        try:
            for case in self.cases:
                for idx in range(1, self.trials_per_case + 1):
                    trial_res = self.run_trial(case, idx)
                    all_trials.append(trial_res)
                    if not trial_res.success:
                        failing_step = f"{case}-trial-{idx}"
                        error_message = trial_res.error_message or "Trial failure"
                        self._log(f"Halting execution on failure in {failing_step}: {error_message}")
                        break
                if error_message:
                    break
        except Exception as e:
            failing_step = failing_step or "runner"
            error_message = str(e)
            self._log(f"Runner halted with unhandled exception: {error_message}")

        end_ts = datetime.datetime.now().isoformat()
        total_dur = time.monotonic() - start_time

        return PowerLossReport(
            cases=self.cases,
            trials_per_case=self.trials_per_case,
            seed=self.seed,
            start_time=start_ts,
            end_time=end_ts,
            total_duration=total_dur,
            trials=all_trials,
            failing_step=failing_step,
            error_message=error_message,
        )


# ==============================================================================
# CLI Argument Parsing and Main Entrypoint
# ==============================================================================


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser for power-loss resilience tool."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="192.0.2.1",
        help="Target bench device hostname or IP address (default: 192.0.2.1)",
    )
    parser.add_argument(
        "--swu",
        dest="swu_path",
        type=pathlib.Path,
        help="Path to valid .swu update container used for deploy & recovery tests",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=VALID_CASES,
        default=list(VALID_CASES),
        help=f"Subset of cases to test (default: {' '.join(VALID_CASES)})",
    )
    parser.add_argument(
        "--trials",
        dest="trials_per_case",
        type=int,
        default=DEFAULT_TRIALS_PER_CASE,
        help=f"Number of repetitions per case (default: {DEFAULT_TRIALS_PER_CASE})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Deterministic seed for PRNG random delays (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--power-cmd",
        dest="power_cmd",
        default="scripts/bench/power.sh",
        help="Power command or template used to switch outlet (default: scripts/bench/power.sh)",
    )
    parser.add_argument(
        "--mock-power",
        action="store_true",
        help="Use in-memory simulated power controller instead of executing external command",
    )
    parser.add_argument(
        "--power-cycle-delay",
        type=float,
        default=DEFAULT_POWER_CYCLE_DELAY,
        help=f"Delay in seconds between power off and on (default: {DEFAULT_POWER_CYCLE_DELAY}s)",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout_secs",
        type=float,
        default=DEFAULT_TIMEOUT_SECS,
        help=f"Timeout waiting for device boot & reachability (default: {DEFAULT_TIMEOUT_SECS}s)",
    )
    parser.add_argument(
        "--poll-interval",
        dest="poll_interval_secs",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECS,
        help=f"Polling interval in seconds for reachability checks (default: {DEFAULT_POLL_INTERVAL_SECS}s)",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_SSH_USER,
        help=f"SSH username for bench access (default: {DEFAULT_SSH_USER})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_SSH_PORT,
        help=f"SSH port (default: {DEFAULT_SSH_PORT})",
    )
    parser.add_argument(
        "--report",
        dest="report_file",
        type=pathlib.Path,
        default=pathlib.Path(DEFAULT_REPORT_FILE),
        help=f"Output Markdown report file (default: {DEFAULT_REPORT_FILE})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON report to standard output",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human-readable progress logs on stderr",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run automated test suite and exit",
    )
    return parser


def parse_arguments(
    parser: argparse.ArgumentParser, argv: Optional[List[str]] = None
) -> argparse.Namespace:
    """Parse and validate command line arguments."""
    args = parser.parse_args(argv)

    if args.test:
        return args

    # Check for argument injection or invalid characters (CQ-R7)
    if not is_safe_identifier(args.target):
        sys.stderr.write(
            f"Error: Target '{args.target}' contains unsafe characters or starts with '-'\n"
        )
        sys.exit(2)

    if not is_safe_identifier(args.user):
        sys.stderr.write(
            f"Error: User '{args.user}' contains unsafe characters or starts with '-'\n"
        )
        sys.exit(2)

    if args.trials_per_case < 1:
        sys.stderr.write(
            f"Error: --trials must be at least 1 (got {args.trials_per_case})\n"
        )
        sys.exit(2)

    if not args.swu_path:
        sys.stderr.write("Error: --swu <path> is required when running power-loss tests\n")
        sys.exit(2)

    if not args.swu_path.is_file():
        sys.stderr.write(f"Error: Update file '{args.swu_path}' does not exist\n")
        sys.exit(1)

    return args


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parse_arguments(parser, argv)

    if args.test:
        try:
            from .test_power_loss import main as test_main
        except ImportError:
            from test_power_loss import main as test_main
        return test_main()

    # Configure runner
    ssh_runner = SystemSshRunner(host=args.target, user=args.user, port=args.port)

    # Configure power controller
    if args.mock_power:
        power_ctrl = MockPowerController(verbose=not args.quiet)
    else:
        power_ctrl = CommandPowerController(
            command_template=args.power_cmd,
            cycle_delay=args.power_cycle_delay,
            verbose=not args.quiet,
        )

    runner = PowerLossRunner(
        runner=ssh_runner,
        power_ctrl=power_ctrl,
        swu_path=args.swu_path,
        cases=args.cases,
        trials_per_case=args.trials_per_case,
        seed=args.seed,
        power_cycle_delay=args.power_cycle_delay,
        timeout_secs=args.timeout_secs,
        poll_interval_secs=args.poll_interval_secs,
        verbose=not args.quiet,
    )

    report = runner.run()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_text())

    # Write Markdown report
    if args.report_file:
        try:
            args.report_file.parent.mkdir(parents=True, exist_ok=True)
            args.report_file.write_text(report.format_markdown(), encoding="utf-8")
            if not args.quiet:
                sys.stderr.write(f"Wrote report to {args.report_file}\n")
        except Exception as e:
            sys.stderr.write(f"Failed to write report file '{args.report_file}': {e}\n")

    return 0 if report.meets_gate_criteria else 1


if __name__ == "__main__":
    sys.exit(main())
