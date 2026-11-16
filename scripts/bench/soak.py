#!/usr/bin/env python3
"""Automated ten-cycle update soak tool for nextoomio test bench (P0-F5.3).

Executes an automated sequence of ten alternating system deployments between two
distinguishable valid update images (.swu) against a bench device, verifying:
- AC1: All 10 deploys are committed, active slot alternates each cycle (root_a <-> root_b),
       and reported version matches deployed image version.
- AC2: Writes a dated soak report with per-cycle step times and final state; runs
       unattended without touching the device.
- AC3: Verifies persistent /data partition preservation using a marker file written
       before the run, confirming it remains completely unchanged after the run.
- AC4: If any cycle fails, halts immediately, names failing cycle and step, and
       reports overall status as FAILED with non-zero exit code.

References:
- ROADMAP.md section 2.1 (exit gates)
- ARCHITECTURE.md sections 3.6, 4.1 (AD-22, AD-23, UPD-05, UPD-06)
- SPECIFICATION.md section 2.1
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Import deploy tooling primitives
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


DEFAULT_SOAK_CYCLES = 10
DEFAULT_MARKER_PATH = "/data/nextoomio-soak-marker.txt"
DEFAULT_REPORT_FILE = "soak-report.md"


def is_safe_data_path(path: str) -> bool:
    """Validate that a marker path is an absolute path within /data with safe characters."""
    if not path.startswith("/data/"):
        return False
    subpath = path[len("/data/") :]
    if not subpath or ".." in subpath or "//" in subpath:
        return False
    return bool(re.match(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$", subpath))


class SoakError(Exception):
    """Exception raised when a soak execution step or invariant check fails."""

    def __init__(
        self,
        cycle: int,
        step: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(f"Cycle {cycle} failed at step '{step}': {message}")
        self.cycle = cycle
        self.step = step
        self.message = message
        self.details = details or {}


class CycleResult:
    """Records the timing and outcome of one individual deployment cycle."""

    def __init__(
        self,
        cycle: int,
        image_name: str,
        swu_path: pathlib.Path,
        expected_version: str,
        previous_slot: str,
        running_slot: str,
        previous_version: str,
        new_version: str,
        step_times: Dict[str, float],
        cycle_duration: float,
        success: bool,
        failing_step: Optional[str] = None,
        error_message: Optional[str] = None,
        marker_valid: bool = True,
    ):
        self.cycle = cycle
        self.image_name = image_name
        self.swu_path = swu_path
        self.expected_version = expected_version
        self.previous_slot = previous_slot
        self.running_slot = running_slot
        self.previous_version = previous_version
        self.new_version = new_version
        self.step_times = step_times
        self.cycle_duration = cycle_duration
        self.success = success
        self.failing_step = failing_step
        self.error_message = error_message
        self.marker_valid = marker_valid

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle": self.cycle,
            "image_name": self.image_name,
            "swu_path": str(self.swu_path),
            "expected_version": self.expected_version,
            "previous_slot": self.previous_slot,
            "running_slot": self.running_slot,
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "step_times": self.step_times,
            "cycle_duration": round(self.cycle_duration, 3),
            "success": self.success,
            "failing_step": self.failing_step,
            "error_message": self.error_message,
            "marker_valid": self.marker_valid,
        }


class SoakReport:
    """Aggregates per-cycle results and generates the final soak report."""

    def __init__(
        self,
        target_device: str,
        total_cycles_requested: int,
        marker_path: str,
    ):
        self.target_device = target_device
        self.total_cycles_requested = total_cycles_requested
        self.marker_path = marker_path
        self.start_time: str = ""
        self.end_time: str = ""
        self.total_duration_secs: float = 0.0
        self.initial_slot: str = "unknown"
        self.initial_version: str = "unknown"
        self.final_slot: str = "unknown"
        self.final_version: str = "unknown"
        self.marker_hash: str = ""
        self.marker_verified_unchanged: bool = False
        self.success: bool = False
        self.failing_cycle: Optional[int] = None
        self.failing_step: Optional[str] = None
        self.failure_message: Optional[str] = None
        self.cycles: List[CycleResult] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_device": self.target_device,
            "total_cycles_requested": self.total_cycles_requested,
            "completed_cycles": len(self.cycles),
            "success": self.success,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_duration_secs": round(self.total_duration_secs, 3),
            "initial_slot": self.initial_slot,
            "final_slot": self.final_slot,
            "initial_version": self.initial_version,
            "final_version": self.final_version,
            "marker_path": self.marker_path,
            "marker_hash": self.marker_hash,
            "marker_verified_unchanged": self.marker_verified_unchanged,
            "failing_cycle": self.failing_cycle,
            "failing_step": self.failing_step,
            "failure_message": self.failure_message,
            "cycles": [c.to_dict() for c in self.cycles],
        }

    def format_markdown(self) -> str:
        """Render soak report as a GitHub-flavored Markdown document (AC2)."""
        completed_count = len(self.cycles)
        if self.success:
            status_badge = "PASSED"
        elif self.failing_step == "gate_criteria":
            status_badge = f"NON-GATE ({completed_count} / 10 CYCLES)"
        else:
            status_badge = "FAILED"

        if self.marker_verified_unchanged:
            marker_status = "UNCHANGED (Verified)"
        elif self.failing_step in ("marker_check", "final_marker_verification"):
            marker_status = "FAILED / MODIFIED"
        elif self.failing_step == "marker_setup":
            marker_status = "FAILED (Setup error)"
        else:
            marker_status = "not checked"

        # AC1 Summary
        if self.success:
            ac1_summary = (
                f"PASSED - All {completed_count} cycles committed with slot alternating and version matching"
            )
        elif self.failing_step == "gate_criteria":
            ac1_summary = (
                f"FAILED (NON-GATE) - All {completed_count} executed cycle(s) committed, "
                f"but AC1 gate requires 10 cycles"
            )
        elif self.failing_cycle is not None:
            ac1_summary = f"FAILED - Halted on cycle {self.failing_cycle} at step `{self.failing_step}`"
        else:
            ac1_summary = f"FAILED - Halted during precheck at step `{self.failing_step}`"

        # AC2 Summary: tied to whether report recorded per-cycle step times and final state
        if completed_count > 0:
            ac2_summary = "PASSED - Per-cycle step times and final state recorded below"
        else:
            ac2_summary = "FAILED - Halted during precheck before recording per-cycle step times"

        # AC3 Summary
        if self.marker_verified_unchanged:
            ac3_summary = f"PASSED - Marker file on /data unchanged across all {completed_count} cycle(s)"
        elif self.failing_step in ("marker_check", "final_marker_verification"):
            ac3_summary = "FAILED - Marker file on /data modified or corrupted"
        elif self.failing_step == "marker_setup":
            ac3_summary = "FAILED - Failed to initialize marker file on /data"
        else:
            ac3_summary = "NOT CHECKED - Run halted before marker verification"

        # AC4 Summary
        if self.success:
            ac4_summary = f"N/A (All {completed_count} cycles succeeded)"
        elif self.failing_step == "gate_criteria":
            ac4_summary = f"FAILED - Run not counted as passed ({completed_count} < 10 cycles completed)"
        elif self.failing_cycle is not None:
            ac4_summary = f"HALTED on Cycle {self.failing_cycle} at step `{self.failing_step}`"
        else:
            ac4_summary = f"HALTED during precheck at step `{self.failing_step}`"

        lines = [
            f"# Soak Test Report: Ten-Cycle Update Soak (P0-F5.3)",
            "",
            f"- **Date:** {self.start_time[:10] if self.start_time else datetime.date.today().isoformat()}",
            f"- **Target Device:** `{self.target_device}`",
            f"- **Overall Status:** **{status_badge}**",
            f"- **Cycles Completed:** {completed_count} / {self.total_cycles_requested}",
            f"- **Total Elapsed Duration:** {self.total_duration_secs:.2f} s",
            f"- **Start Time:** `{self.start_time}`",
            f"- **End Time:** `{self.end_time}`",
            f"- **Initial State:** slot `{self.initial_slot}`, version `{self.initial_version}`",
            f"- **Final State:** slot `{self.final_slot}`, version `{self.final_version}`",
            f"- **Persistent Data Marker (`{self.marker_path}`):** {marker_status}",
            "",
            "## Summary of Acceptance Criteria",
            "",
            f"- **AC1 (Alternating Slot & Version Match):** {ac1_summary}",
            f"- **AC2 (Report with Step Times & Final State):** {ac2_summary}",
            f"- **AC3 (Persistent Data Marker Unchanged):** {ac3_summary}",
            f"- **AC4 (Failure Diagnostics & Stop on Error):** {ac4_summary}",
            "",
        ]

        if not self.success:
            lines.extend([
                "## Failure Diagnosis (AC4)",
                "",
                f"> [!CAUTION]",
            ])
            if self.failing_step == "gate_criteria":
                lines.extend([
                    f"> **Non-gate execution:** {self.failure_message}.",
                    "> The soak test must complete at least 10 alternating cycles to satisfy P0-F5.3 exit gate.",
                    "",
                ])
            else:
                cycle_label = f"Cycle {self.failing_cycle}" if self.failing_cycle is not None else "precheck"
                lines.extend([
                    f"> **Soak run halted at {cycle_label}** during step `{self.failing_step}`.",
                    f"> Error: {self.failure_message}",
                    "",
                ])

        lines.extend([
            "## Per-Cycle Step Times (AC2)",
            "",
            "| Cycle | Image | From Slot | To Slot | Deployed Version | Upload (s) | Validate (s) | Install (s) | Trial / Wait (s) | Confirm (s) | Marker (s) | Total (s) | Result |",
            "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        for c in self.cycles:
            st = c.step_times
            up_s = f"{st.get('upload', 0.0):.2f}"
            val_s = f"{st.get('validate', 0.0):.2f}"
            ins_s = f"{st.get('install', 0.0):.2f}"
            wait_s = f"{st.get('wait_for_device', 0.0):.2f}"
            conf_s = f"{st.get('confirm', 0.0):.2f}"
            mark_s = f"{st.get('marker_check', 0.0):.2f}"
            tot_s = f"{c.cycle_duration:.2f}"
            res_str = "OK" if c.success else f"FAIL ({c.failing_step})"

            lines.append(
                f"| {c.cycle} | {c.image_name} | `{c.previous_slot}` | `{c.running_slot}` | "
                f"`{c.new_version}` | {up_s} | {val_s} | {ins_s} | {wait_s} | {conf_s} | "
                f"{mark_s} | {tot_s} | **{res_str}** |"
            )

        lines.extend([
            "",
            "## Invariant Verifications",
            "",
        ])
        if completed_count > 0:
            lines.extend([
                f"1. **Slot Alternation (AC1):** Each consecutive cycle ({completed_count} completed) toggled between `root_a` and `root_b` without repeating.",
                f"2. **Commit State (AC1):** All {completed_count} deployed image(s) transitioned to `committed` update state via remote confirmation.",
                f"3. **Version Accuracy (AC1):** Active image version read from `/etc/nextoomio-release` matched the deployed SWU release version across all {completed_count} cycle(s).",
            ])
        else:
            lines.extend([
                "1. **Slot Alternation (AC1):** None (0 cycles executed).",
                "2. **Commit State (AC1):** None (0 cycles executed).",
                "3. **Version Accuracy (AC1):** None (0 cycles executed).",
            ])

        if self.marker_verified_unchanged:
            lines.append(
                f"4. **Data Partition Integrity (AC3):** Marker file written before cycle 1 retained exact SHA-256 (`{self.marker_hash}`) through all {completed_count} cycle reboots."
            )
        elif self.marker_hash:
            lines.append(
                f"4. **Data Partition Integrity (AC3):** Marker file check failed or incomplete (expected SHA-256: `{self.marker_hash}`)."
            )
        else:
            lines.append(
                "4. **Data Partition Integrity (AC3):** Marker file was not initialized."
            )

        if completed_count > 0:
            lines.append(
                "5. **Automated SSH Execution (AC2):** Non-interactive execution over SSH automation; per-cycle step times recorded in report."
            )
        else:
            lines.append(
                "5. **Automated SSH Execution (AC2):** Halted during precheck before executing deployment cycles."
            )
        lines.append("")

        return "\n".join(lines)


class MarkerManager:
    """Manages the lifecycle and verification of the persistent data marker file (AC3)."""

    def __init__(self, marker_path: str = DEFAULT_MARKER_PATH):
        if not is_safe_data_path(marker_path):
            raise ValueError(
                f"Marker path '{marker_path}' must be an absolute path under /data with safe characters"
            )
        self.marker_path = marker_path
        self.expected_content: str = ""
        self.expected_hash: str = ""

    def setup_marker(self, runner: SshRunner) -> Tuple[str, str]:
        """Write a unique verifiable marker file to the /data partition before the soak run."""
        token = uuid.uuid4().hex
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.expected_content = f"NEXTOOMIO_SOAK_MARKER_P0_F5_3\ntoken={token}\ntimestamp={timestamp}\n"
        self.expected_hash = hashlib.sha256(self.expected_content.encode("utf-8")).hexdigest()

        # Write to persistent partition
        quoted_path = "'" + self.marker_path.replace("'", "'\\''") + "'"
        write_cmd = (
            f"mkdir -p /data && "
            f"cat > {quoted_path} << 'EOF'\n{self.expected_content}EOF\n"
            f"sha256sum {quoted_path}"
        )
        res = runner.run(write_cmd)
        if not res.ok:
            raise RuntimeError(f"Failed to write marker file to {self.marker_path}: {res.stderr}")

        # Check hash returned by sha256sum
        match = re.search(r"([0-9a-fA-F]{64})", res.stdout)
        if not match or match.group(1).lower() != self.expected_hash.lower():
            raise RuntimeError(
                f"Marker checksum verification mismatch on write. Expected {self.expected_hash}, got {res.stdout}"
            )

        return self.expected_content, self.expected_hash

    def verify_marker(self, runner: SshRunner) -> Tuple[bool, str]:
        """Verify that the marker file exists on /data and its content/checksum is unchanged."""
        if not self.expected_hash:
            return False, "Marker was never initialized"

        quoted_path = "'" + self.marker_path.replace("'", "'\\''") + "'"
        check_cmd = f"sha256sum {quoted_path} && cat {quoted_path}"
        res = runner.run(check_cmd)
        if not res.ok:
            return False, f"Marker file unreadable or missing at {self.marker_path}: {res.stderr}"

        match = re.search(r"([0-9a-fA-F]{64})", res.stdout)
        if not match:
            return False, f"Could not parse sha256 checksum from output: {res.stdout}"

        observed_hash = match.group(1).lower()
        if observed_hash != self.expected_hash.lower():
            return False, f"Marker checksum mismatch! Expected {self.expected_hash}, observed {observed_hash}"

        return True, "Marker intact and verified"


class SoakRunner:
    """Orchestrates the ten-cycle update soak execution against a bench device."""

    def __init__(
        self,
        runner: SshRunner,
        image_a: pathlib.Path,
        image_b: pathlib.Path,
        cycles: int = DEFAULT_SOAK_CYCLES,
        marker_path: str = DEFAULT_MARKER_PATH,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
        poll_interval_secs: float = DEFAULT_POLL_INTERVAL_SECS,
        verbose: bool = False,
    ):
        self.runner = runner
        self.image_a = image_a
        self.image_b = image_b
        self.cycles = cycles
        self.marker_path = marker_path
        self.timeout_secs = timeout_secs
        self.poll_interval_secs = poll_interval_secs
        self.verbose = verbose
        try:
            self.marker_manager = MarkerManager(marker_path)
        except ValueError:
            self.marker_manager = None

    def _log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] [SOAK] {msg}\n")
            sys.stderr.flush()

    def run(self) -> SoakReport:
        """Execute the full multi-cycle soak test."""
        target_name = getattr(self.runner, "host", "bench-device")
        report = SoakReport(
            target_device=target_name,
            total_cycles_requested=self.cycles,
            marker_path=self.marker_path,
        )

        start_dt = datetime.datetime.now(datetime.timezone.utc)
        report.start_time = start_dt.isoformat()
        start_mono = time.monotonic()

        self._log(f"Starting {self.cycles}-cycle update soak on {target_name}...")

        # Step -1: Pre-flight parameter validations
        if self.cycles < 1:
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = f"Cycles must be at least 1, got {self.cycles}"
            self._finalize_report(report, start_mono)
            return report

        if self.marker_manager is None or not is_safe_data_path(self.marker_path):
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = (
                f"Marker path '{self.marker_path}' must be an absolute path under /data with safe characters"
            )
            self._finalize_report(report, start_mono)
            return report

        # Step 0: Pre-flight image validations
        if not self.image_a.is_file() or self.image_a.stat().st_size == 0:
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = f"Image A '{self.image_a}' does not exist or is empty"
            self._finalize_report(report, start_mono)
            return report

        if not self.image_b.is_file() or self.image_b.stat().st_size == 0:
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = f"Image B '{self.image_b}' does not exist or is empty"
            self._finalize_report(report, start_mono)
            return report

        meta_a = parse_swu_description(self.image_a)
        meta_b = parse_swu_description(self.image_b)
        ver_a = meta_a.get("version")
        ver_b = meta_b.get("version")

        if not ver_a or not ver_b:
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = (
                f"Could not extract release versions from images: "
                f"Image A version='{ver_a}', Image B version='{ver_b}'"
            )
            self._finalize_report(report, start_mono)
            return report

        if ver_a == ver_b:
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = (
                f"Images A and B must be distinguishable, but both report version '{ver_a}'"
            )
            self._finalize_report(report, start_mono)
            return report

        self._log(f"Image A: {self.image_a.name} (version {ver_a})")
        self._log(f"Image B: {self.image_b.name} (version {ver_b})")

        # Step 1: Check initial device reachability and query state
        if not self.runner.check_reachability(timeout=5.0):
            report.success = False
            report.failing_step = "precheck"
            report.failure_message = f"Target device '{target_name}' is not reachable over SSH"
            self._finalize_report(report, start_mono)
            return report

        # Query initial state via Deployer helper
        probe_deployer = Deployer(
            runner=self.runner,
            swu_path=self.image_a,
            expected_version=ver_a,
            timeout_secs=self.timeout_secs,
            poll_interval_secs=self.poll_interval_secs,
            verbose=False,
        )
        init_slot, init_ver, _ = probe_deployer.query_device_state()
        report.initial_slot = init_slot
        report.initial_version = init_ver
        self._log(f"Initial device state: slot={init_slot}, version={init_ver}")

        # Step 2: Write persistent marker to /data partition (AC3)
        self._log(f"Writing persistent data marker to {self.marker_path}...")
        try:
            _, marker_hash = self.marker_manager.setup_marker(self.runner)
            report.marker_hash = marker_hash
            self._log(f"Persistent marker established with SHA256: {marker_hash[:16]}...")
        except Exception as e:
            report.success = False
            report.failing_step = "marker_setup"
            report.failure_message = f"Failed to initialize persistent marker: {e}"
            self._finalize_report(report, start_mono)
            return report

        # Step 3: Run the alternating deployment cycles (AC1, AC2, AC3, AC4)
        current_slot = init_slot
        current_version = init_ver

        for cycle_idx in range(1, self.cycles + 1):
            cycle_start_mono = time.monotonic()
            self._log(f"=== Starting Cycle {cycle_idx} / {self.cycles} ===")

            # Determine alternating image:
            # If current version is ver_a, deploy Image B; otherwise deploy Image A
            if current_version == ver_a:
                cur_image = self.image_b
                cur_img_name = "Image B"
                cur_expected_version = ver_b
            elif current_version == ver_b:
                cur_image = self.image_a
                cur_img_name = "Image A"
                cur_expected_version = ver_a
            else:
                # Initial version matches neither; select based on cycle parity
                if cycle_idx % 2 == 1:
                    cur_image = self.image_a
                    cur_img_name = "Image A"
                    cur_expected_version = ver_a
                else:
                    cur_image = self.image_b
                    cur_img_name = "Image B"
                    cur_expected_version = ver_b

            expected_target_slot = opposite_slot(current_slot)
            self._log(
                f"Cycle {cycle_idx}: Deploying {cur_img_name} ({cur_expected_version}) "
                f"from {current_slot} -> expected {expected_target_slot}"
            )

            # Execute deploy
            cycle_deployer = Deployer(
                runner=self.runner,
                swu_path=cur_image,
                expected_version=cur_expected_version,
                timeout_secs=self.timeout_secs,
                poll_interval_secs=self.poll_interval_secs,
                remote_confirm=True,
                verbose=self.verbose,
            )

            deploy_res: DeployResult = cycle_deployer.deploy()
            step_times = dict(deploy_res.step_times)

            # AC4: If deploy fails, halt immediately
            if not deploy_res.success:
                cycle_duration = time.monotonic() - cycle_start_mono
                failing_step = deploy_res.failing_step or "deploy"
                error_msg = deploy_res.error_message or "Deployment failed"

                cycle_rec = CycleResult(
                    cycle=cycle_idx,
                    image_name=cur_img_name,
                    swu_path=cur_image,
                    expected_version=cur_expected_version,
                    previous_slot=current_slot,
                    running_slot=deploy_res.running_slot or current_slot,
                    previous_version=current_version,
                    new_version=deploy_res.new_version or "unknown",
                    step_times=step_times,
                    cycle_duration=cycle_duration,
                    success=False,
                    failing_step=failing_step,
                    error_message=error_msg,
                    marker_valid=False,
                )
                report.cycles.append(cycle_rec)
                report.success = False
                report.failing_cycle = cycle_idx
                report.failing_step = failing_step
                report.failure_message = error_msg
                self._log(f"Cycle {cycle_idx} FAILED at step '{failing_step}': {error_msg}")
                break

            # AC1 Invariant: Verify slot alternated
            if deploy_res.running_slot == current_slot or deploy_res.running_slot != expected_target_slot:
                cycle_duration = time.monotonic() - cycle_start_mono
                err = (
                    f"Slot failed to alternate! Expected '{expected_target_slot}', "
                    f"observed '{deploy_res.running_slot}' (previous was '{current_slot}')"
                )
                cycle_rec = CycleResult(
                    cycle=cycle_idx,
                    image_name=cur_img_name,
                    swu_path=cur_image,
                    expected_version=cur_expected_version,
                    previous_slot=current_slot,
                    running_slot=deploy_res.running_slot,
                    previous_version=current_version,
                    new_version=deploy_res.new_version,
                    step_times=step_times,
                    cycle_duration=cycle_duration,
                    success=False,
                    failing_step="slot_alternation",
                    error_message=err,
                )
                report.cycles.append(cycle_rec)
                report.success = False
                report.failing_cycle = cycle_idx
                report.failing_step = "slot_alternation"
                report.failure_message = err
                self._log(f"Cycle {cycle_idx} FAILED: {err}")
                break

            # AC1 Invariant: Verify version matched
            if deploy_res.new_version != cur_expected_version:
                cycle_duration = time.monotonic() - cycle_start_mono
                err = (
                    f"Version mismatch after reboot! Expected '{cur_expected_version}', "
                    f"observed '{deploy_res.new_version}'"
                )
                cycle_rec = CycleResult(
                    cycle=cycle_idx,
                    image_name=cur_img_name,
                    swu_path=cur_image,
                    expected_version=cur_expected_version,
                    previous_slot=current_slot,
                    running_slot=deploy_res.running_slot,
                    previous_version=current_version,
                    new_version=deploy_res.new_version,
                    step_times=step_times,
                    cycle_duration=cycle_duration,
                    success=False,
                    failing_step="version_match",
                    error_message=err,
                )
                report.cycles.append(cycle_rec)
                report.success = False
                report.failing_cycle = cycle_idx
                report.failing_step = "version_match"
                report.failure_message = err
                self._log(f"Cycle {cycle_idx} FAILED: {err}")
                break

            # AC3 Invariant: Verify persistent data partition marker intact
            m_start = time.monotonic()
            marker_ok, marker_msg = self.marker_manager.verify_marker(self.runner)
            step_times["marker_check"] = time.monotonic() - m_start

            if not marker_ok:
                cycle_duration = time.monotonic() - cycle_start_mono
                err = f"Persistent data marker check failed: {marker_msg}"
                cycle_rec = CycleResult(
                    cycle=cycle_idx,
                    image_name=cur_img_name,
                    swu_path=cur_image,
                    expected_version=cur_expected_version,
                    previous_slot=current_slot,
                    running_slot=deploy_res.running_slot,
                    previous_version=current_version,
                    new_version=deploy_res.new_version,
                    step_times=step_times,
                    cycle_duration=cycle_duration,
                    success=False,
                    failing_step="marker_check",
                    error_message=err,
                    marker_valid=False,
                )
                report.cycles.append(cycle_rec)
                report.success = False
                report.failing_cycle = cycle_idx
                report.failing_step = "marker_check"
                report.failure_message = err
                self._log(f"Cycle {cycle_idx} FAILED: {err}")
                break

            # Cycle completed successfully
            cycle_duration = time.monotonic() - cycle_start_mono
            cycle_rec = CycleResult(
                cycle=cycle_idx,
                image_name=cur_img_name,
                swu_path=cur_image,
                expected_version=cur_expected_version,
                previous_slot=current_slot,
                running_slot=deploy_res.running_slot,
                previous_version=current_version,
                new_version=deploy_res.new_version,
                step_times=step_times,
                cycle_duration=cycle_duration,
                success=True,
                marker_valid=True,
            )
            report.cycles.append(cycle_rec)
            self._log(
                f"Cycle {cycle_idx} OK: slot={deploy_res.running_slot}, "
                f"version={deploy_res.new_version}, elapsed={cycle_duration:.2f}s"
            )

            # Advance state for next iteration
            current_slot = deploy_res.running_slot
            current_version = deploy_res.new_version

        # Post-loop checks
        if len(report.cycles) == self.cycles and all(c.success for c in report.cycles):
            # Final check of marker
            m_final_ok, m_msg = self.marker_manager.verify_marker(self.runner)
            if not m_final_ok:
                report.success = False
                report.marker_verified_unchanged = False
                report.failing_step = "final_marker_verification"
                report.failure_message = f"Final marker verification failed after completing all cycles: {m_msg}"
            elif self.cycles < 10:
                report.success = False
                report.marker_verified_unchanged = True
                report.failing_step = "gate_criteria"
                report.failure_message = (
                    f"Fewer than 10 cycles completed ({self.cycles} / 10). "
                    f"P0-F5.3 exit gate (AC1) requires 10 alternating committed cycles"
                )
            else:
                report.success = True
                report.marker_verified_unchanged = True

        self._finalize_report(report, start_mono)
        return report

    def _finalize_report(self, report: SoakReport, start_mono: float) -> None:
        report.total_duration_secs = time.monotonic() - start_mono
        end_dt = datetime.datetime.now(datetime.timezone.utc)
        report.end_time = end_dt.isoformat()
        if report.cycles:
            report.final_slot = report.cycles[-1].running_slot
            report.final_version = report.cycles[-1].new_version
        else:
            report.final_slot = report.initial_slot
            report.final_version = report.initial_version


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser for the soak test runner."""
    parser = argparse.ArgumentParser(
        description="Ten-cycle automated update soak tool for nextoomio test bench (P0-F5.3)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "device",
        nargs="?",
        default=None,
        help="Target bench device hostname or IP address",
    )
    parser.add_argument(
        "image_a",
        nargs="?",
        default=None,
        type=pathlib.Path,
        help="Path to first update image (.swu)",
    )
    parser.add_argument(
        "image_b",
        nargs="?",
        default=None,
        type=pathlib.Path,
        help="Path to second update image (.swu)",
    )

    parser.add_argument(
        "-d", "--device-name",
        dest="opt_device",
        help="Target bench device hostname or IP address (alternative to positional)",
    )
    parser.add_argument(
        "--image-a",
        dest="opt_image_a",
        type=pathlib.Path,
        help="Path to first update image (alternative to positional)",
    )
    parser.add_argument(
        "--image-b",
        dest="opt_image_b",
        type=pathlib.Path,
        help="Path to second update image (alternative to positional)",
    )

    parser.add_argument(
        "-c", "--cycles",
        type=int,
        default=DEFAULT_SOAK_CYCLES,
        help=f"Number of update cycles to run (default: {DEFAULT_SOAK_CYCLES})",
    )
    parser.add_argument(
        "-u", "--user",
        default=DEFAULT_SSH_USER,
        help=f"SSH username for bench target (default: {DEFAULT_SSH_USER})",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_SSH_PORT,
        help=f"SSH port (default: {DEFAULT_SSH_PORT})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECS,
        help=f"Trial boot wait timeout in seconds (default: {DEFAULT_TIMEOUT_SECS})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECS,
        help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL_SECS})",
    )
    parser.add_argument(
        "--marker-path",
        default=DEFAULT_MARKER_PATH,
        help=f"Marker file path on persistent /data partition (default: {DEFAULT_MARKER_PATH})",
    )
    parser.add_argument(
        "-o", "--report-file",
        type=pathlib.Path,
        default=pathlib.Path(DEFAULT_REPORT_FILE),
        help=f"Path to write report output (default: {DEFAULT_REPORT_FILE})",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "text"],
        default="markdown",
        help="Report format to output (default: markdown)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Execute automated self-tests and exit",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print verbose step progress to stderr",
    )

    return parser


def parse_arguments(
    argv: List[str],
) -> Tuple[argparse.Namespace, Optional[str], Optional[pathlib.Path], Optional[pathlib.Path]]:
    """Parse and reconcile positional vs optional CLI arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.test:
        return args, None, None, None

    device = args.device or args.opt_device
    img_a = args.image_a or args.opt_image_a
    img_b = args.image_b or args.opt_image_b

    return args, device, img_a, img_b


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for soak runner."""
    if argv is None:
        argv = sys.argv[1:]

    args, device, img_a, img_b = parse_arguments(argv)

    if args.test:
        import unittest
        loader = unittest.TestLoader()
        suite = loader.discover(
            str(pathlib.Path(__file__).resolve().parent), pattern="test_soak.py"
        )
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        return 0 if res.wasSuccessful() else 1

    if not device:
        sys.stderr.write("Error: Target bench device hostname/IP must be specified\n")
        return 2

    if not is_safe_identifier(device):
        sys.stderr.write(f"Error: Target device '{device}' contains unsafe characters or starts with '-'\n")
        return 2

    if not is_safe_identifier(args.user):
        sys.stderr.write(f"Error: SSH user '{args.user}' contains unsafe characters or starts with '-'\n")
        return 2

    if not img_a or not img_b:
        sys.stderr.write("Error: Both update images (--image-a and --image-b) must be specified\n")
        return 2

    if args.cycles < 1:
        sys.stderr.write(f"Error: --cycles must be at least 1 (got {args.cycles})\n")
        return 2

    if not is_safe_data_path(args.marker_path):
        sys.stderr.write(
            f"Error: Marker path '{args.marker_path}' must be an absolute path under /data with safe characters\n"
        )
        return 2

    runner = SystemSshRunner(host=device, user=args.user, port=args.port)

    soak_runner = SoakRunner(
        runner=runner,
        image_a=img_a,
        image_b=img_b,
        cycles=args.cycles,
        marker_path=args.marker_path,
        timeout_secs=args.timeout,
        poll_interval_secs=args.poll_interval,
        verbose=args.verbose,
    )

    report = soak_runner.run()

    # Format report
    if args.format == "json":
        report_text = json.dumps(report.to_dict(), indent=2) + "\n"
    elif args.format == "text":
        if report.success:
            status_str = "PASSED"
        elif report.failing_step == "gate_criteria":
            status_str = f"NON-GATE ({len(report.cycles)} / 10 CYCLES)"
        else:
            status_str = "FAILED"
        lines = [
            f"Soak Run: {status_str}",
            f"Device: {report.target_device}",
            f"Cycles: {len(report.cycles)} / {report.total_cycles_requested}",
            f"Duration: {report.total_duration_secs:.2f}s",
            f"Final state: {report.final_slot} ({report.final_version})",
        ]
        if not report.success:
            cycle_str = f"cycle {report.failing_cycle}" if report.failing_cycle is not None else report.failing_step
            lines.append(f"Halted at {cycle_str} ({report.failing_step}): {report.failure_message}")
        report_text = "\n".join(lines) + "\n"
    else:
        report_text = report.format_markdown() + "\n"

    # Write report file
    try:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(report_text, encoding="utf-8")
        sys.stderr.write(f"Soak report written to: {args.report_file}\n")
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to write report file '{args.report_file}': {e}\n")

    # Also output summary to stdout
    sys.stdout.write(report_text)
    sys.stdout.flush()

    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
