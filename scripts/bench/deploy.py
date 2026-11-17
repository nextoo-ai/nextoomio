#!/usr/bin/env python3
"""One-command deploy tool for nextoomio test bench (P0-F5.1).

Takes a built system update file (.swu) on the developer's workstation, uploads
it to a bench device over SSH, validates, installs to the inactive slot, starts
a trial boot with remote-confirmed commit requested (AD-22), waits for the
device to return, checks slot and version, and confirms the update.

Acceptance criteria covered:
- AC1: Uploads, validates, installs, starts trial, waits for device, verifies
       running slot and version, sends remote confirmation, and prints final state.
- AC2: Exit status is zero on success; prints previous and new versions, running
       slot, and elapsed time of each step.
- AC3: On validation failure, device timeout, or version mismatch, exits non-zero,
       names failing step, and sends no confirmation.
- AC4: On rollback to previous slot, reports rollback and update state from device,
       exits non-zero, and sends no confirmation.
- AC5: Deploying twice alternates slots normally (A -> B, then B -> A).
- AC6: Stores no credentials and prints no key material (uses system ssh and ssh-agent).

References:
- ROADMAP.md section 2.1
- ARCHITECTURE.md sections 3.6, 4.1 (AD-22, AD-23, UPD-05, UPD-06)
"""

from __future__ import annotations

import abc
import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SSH_USER = "root"
DEFAULT_SSH_PORT = 22
DEFAULT_TIMEOUT_SECS = 120.0
DEFAULT_POLL_INTERVAL_SECS = 2.0


class DeployError(Exception):
    """Exception raised when a deployment step fails."""

    def __init__(self, step: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"Deploy failed at step '{step}': {message}")
        self.step = step
        self.message = message
        self.details = details or {}


class StepTimer:
    """Records wall-clock elapsed time for deployment steps."""

    def __init__(self) -> None:
        self._start_times: Dict[str, float] = {}
        self._elapsed: Dict[str, float] = {}
        self._overall_start = time.monotonic()

    def start(self, step: str) -> None:
        self._start_times[step] = time.monotonic()

    def stop(self, step: str) -> float:
        start = self._start_times.get(step, time.monotonic())
        elapsed = time.monotonic() - start
        self._elapsed[step] = elapsed
        return elapsed

    def get_elapsed(self, step: str) -> float:
        return self._elapsed.get(step, 0.0)

    def total_elapsed(self) -> float:
        return time.monotonic() - self._overall_start

    def all_steps(self) -> Dict[str, float]:
        res = dict(self._elapsed)
        res["total"] = self.total_elapsed()
        return res


class SshCommandResult:
    """Encapsulates the exit code and outputs from a remote command execution."""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SshRunner(abc.ABC):
    """Abstract interface for executing commands and streaming data over SSH."""

    @abc.abstractmethod
    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        """Run a command string on the target device."""

    @abc.abstractmethod
    def stream_upload(
        self,
        local_file: pathlib.Path,
        remote_cmd: str,
        timeout: Optional[float] = None,
    ) -> SshCommandResult:
        """Stream a local file into standard input of a remote command."""

    @abc.abstractmethod
    def check_reachability(self, timeout: float = 3.0) -> bool:
        """Check if target SSH service is reachable and authenticates."""


SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_identifier(val: str) -> bool:
    """Validate that host or user string contains only safe alphanumeric/dot/hyphen/underscore chars.

    Rejects strings starting with '-' or containing shell/ssh option injection metacharacters (CQ-R7).
    """
    return bool(SAFE_IDENTIFIER_PATTERN.match(val))


class SystemSshRunner(SshRunner):
    """Production SSH runner utilizing the system ssh binary and SSH agent.

    Never stores credentials and prints no key material (AC6).
    """

    def __init__(self, host: str, user: str = DEFAULT_SSH_USER, port: int = DEFAULT_SSH_PORT):
        if not is_safe_identifier(host):
            raise ValueError(
                f"Invalid host '{host}': must match pattern ^[A-Za-z0-9][A-Za-z0-9._-]*$"
            )
        if not is_safe_identifier(user):
            raise ValueError(
                f"Invalid user '{user}': must match pattern ^[A-Za-z0-9][A-Za-z0-9._-]*$"
            )
        self.host = host
        self.user = user
        self.port = port

    def _base_ssh_args(self, connect_timeout: float = 5.0) -> List[str]:
        # BatchMode=yes prevents hanging on interactive password prompts.
        # StrictHostKeyChecking=accept-new accepts bench host keys without prompt.
        return [
            "ssh",
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={int(connect_timeout)}",
            "-o",
            "ServerAliveInterval=15",
            "--",
            f"{self.user}@{self.host}",
        ]

    def run(self, cmd: str, timeout: Optional[float] = None) -> SshCommandResult:
        ssh_cmd = self._base_ssh_args() + [cmd]
        try:
            proc = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return SshCommandResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return SshCommandResult(124, "", f"SSH command timed out after {timeout}s: {e}")
        except Exception as e:
            return SshCommandResult(1, "", f"SSH execution error: {e}")

    def stream_upload(
        self,
        local_file: pathlib.Path,
        remote_cmd: str,
        timeout: Optional[float] = None,
    ) -> SshCommandResult:
        ssh_cmd = self._base_ssh_args(connect_timeout=10.0) + [remote_cmd]
        try:
            with open(local_file, "rb") as f:
                proc = subprocess.run(
                    ssh_cmd,
                    stdin=f,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            return SshCommandResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return SshCommandResult(124, "", f"Upload stream timed out after {timeout}s: {e}")
        except Exception as e:
            return SshCommandResult(1, "", f"Upload stream error: {e}")

    def check_reachability(self, timeout: float = 3.0) -> bool:
        cmd = self._base_ssh_args(connect_timeout=timeout) + ["true"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
            return proc.returncode == 0
        except Exception:
            return False


def normalize_slot(raw: str) -> str:
    """Normalize slot identifier to standard form ('root_a' or 'root_b')."""
    s = raw.strip().lower()
    if "root_b" in s or s in ("b", "slot_b", "2", "6", "mmcblk0p6"):
        return "root_b"
    if "root_a" in s or s in ("a", "slot_a", "1", "5", "mmcblk0p5"):
        return "root_a"
    return s


def opposite_slot(slot: str) -> str:
    """Return the inactive slot opposite to the given active slot."""
    norm = normalize_slot(slot)
    return "root_a" if norm == "root_b" else "root_b"


def parse_swu_description(swu_path: pathlib.Path) -> Dict[str, Any]:
    """Parse sw-description metadata from a .swu CPIO container if possible."""
    metadata: Dict[str, Any] = {}
    if not swu_path.is_file():
        return metadata

    try:
        with open(swu_path, "rb") as f:
            while True:
                header = f.read(110)
                if len(header) < 110:
                    break

                magic = header[:6]
                if magic not in (b"070701", b"070702"):  # SVR4 new ASCII CPIO
                    break

                namesize = int(header[94:102], 16)
                filesize = int(header[54:62], 16)
                filename_bytes = f.read(namesize)
                filename = filename_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")

                pad_name = (4 - ((110 + namesize) % 4)) % 4
                if pad_name > 0:
                    f.read(pad_name)

                if filename == "TRAILER!!!":
                    break

                if filename == "sw-description":
                    content = f.read(filesize).decode("utf-8", errors="replace")
                    m_ver = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
                    if m_ver:
                        metadata["version"] = m_ver.group(1)
                    m_hw = re.search(r'hardware-compatibility\s*:\s*\[([^\]]+)\]', content)
                    if m_hw:
                        metadata["hardware"] = [
                            h.strip().strip('"').strip("'")
                            for h in m_hw.group(1).split(",")
                            if h.strip()
                        ]
                    break
                else:
                    # Skip file content
                    f.seek(filesize, os.SEEK_CUR)

                pad_file = (4 - (filesize % 4)) % 4
                if pad_file > 0:
                    f.seek(pad_file, os.SEEK_CUR)
    except Exception:
        pass

    return metadata


class DeployResult:
    """Summary of deployment execution."""

    def __init__(
        self,
        success: bool,
        previous_version: str,
        new_version: str,
        running_slot: str,
        final_state: str,
        step_times: Dict[str, float],
        failing_step: Optional[str] = None,
        error_message: Optional[str] = None,
        confirmation_sent: bool = False,
        rollback_detected: bool = False,
        device_update_state: Optional[Dict[str, Any]] = None,
    ):
        self.success = success
        self.previous_version = previous_version
        self.new_version = new_version
        self.running_slot = running_slot
        self.final_state = final_state
        self.step_times = step_times
        self.failing_step = failing_step
        self.error_message = error_message
        self.confirmation_sent = confirmation_sent
        self.rollback_detected = rollback_detected
        self.device_update_state = device_update_state or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "running_slot": self.running_slot,
            "final_state": self.final_state,
            "step_times": self.step_times,
            "failing_step": self.failing_step,
            "error_message": self.error_message,
            "confirmation_sent": self.confirmation_sent,
            "rollback_detected": self.rollback_detected,
            "device_update_state": self.device_update_state,
        }


class Deployer:
    """Manages the full deployment sequence against a bench device."""

    def __init__(
        self,
        runner: SshRunner,
        swu_path: pathlib.Path,
        expected_version: Optional[str] = None,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
        poll_interval_secs: float = DEFAULT_POLL_INTERVAL_SECS,
        remote_confirm: bool = True,
        verbose: bool = False,
    ):
        self.runner = runner
        self.swu_path = swu_path
        self.expected_version = expected_version
        self.timeout_secs = timeout_secs
        self.poll_interval_secs = poll_interval_secs
        self.remote_confirm = remote_confirm
        self.verbose = verbose
        self.timer = StepTimer()
        self.initial_boot_id: Optional[str] = None

    def _log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            sys.stderr.flush()

    def _remaining(self, step: str, cap: Optional[float] = None) -> float:
        remaining = self.timeout_secs - self.timer.total_elapsed()
        if remaining <= 0:
            raise DeployError(step, "Timeout: deployment time budget exhausted")
        return min(remaining, cap) if cap is not None else remaining

    def _run(self, cmd: str, step: str, cap: Optional[float] = None) -> SshCommandResult:
        res = self.runner.run(cmd, timeout=self._remaining(step, cap))
        if res.returncode == 124:
            raise DeployError(step, f"Remote command timed out: {cmd}")
        return res

    def query_device_state(self, step: str = "precheck") -> Tuple[str, str, Dict[str, Any]]:
        """Query the running slot, image version, and update status from target."""
        slot = "unknown"
        version = "unknown"
        update_status: Dict[str, Any] = {}

        # 1. Try nx-sysd version --json
        res = self._run("nx-sysd version --json", step)
        if res.ok and res.stdout.strip():
            try:
                data = json.loads(res.stdout)
                slot = data.get("slot") or slot
                version = data.get("version") or version
            except Exception:
                pass

        # 2. Fallbacks for slot and version if not resolved
        if slot == "unknown":
            res_cmd = self._run("cat /proc/cmdline", step)
            if res_cmd.ok:
                if "root_b" in res_cmd.stdout or "4e58544d-06" in res_cmd.stdout:
                    slot = "root_b"
                elif "root_a" in res_cmd.stdout or "4e58544d-05" in res_cmd.stdout:
                    slot = "root_a"

        if version == "unknown":
            res_ver = self._run(
                "cat /etc/nextoomio-release 2>/dev/null || cat /etc/os-release", step
            )
            if res_ver.ok:
                m = re.search(r'IMAGE_VERSION=["\']?([^"\']+)["\']?', res_ver.stdout)
                if not m:
                    m = re.search(r'VERSION_ID=["\']?([^"\']+)["\']?', res_ver.stdout)
                if m:
                    version = m.group(1).strip()

        # 3. Query updater status
        res_stat = self._run("nx-sysd update status --json", step)
        if res_stat.ok and res_stat.stdout.strip():
            try:
                update_status = json.loads(res_stat.stdout)
                if slot == "unknown" and "active_slot" in update_status:
                    slot = update_status["active_slot"]
            except Exception:
                pass

        return normalize_slot(slot), version, update_status

    def deploy(self) -> DeployResult:
        """Execute the end-to-end one-command deploy process."""
        confirmation_sent = False
        failing_step: Optional[str] = None
        error_msg: Optional[str] = None
        rollback_detected = False
        device_update_state: Dict[str, Any] = {}

        previous_slot = "unknown"
        previous_version = "unknown"
        target_slot = "unknown"
        new_version = "unknown"
        running_slot = "unknown"

        try:
            # Step 1: Pre-check & Target Slot calculation
            self.timer.start("precheck")
            self._log("Step 1: Checking update file and initial device state...")
            if not self.swu_path.is_file():
                raise DeployError("precheck", f"Update file '{self.swu_path}' not found")
            if self.swu_path.stat().st_size == 0:
                raise DeployError("precheck", f"Update file '{self.swu_path}' is empty")

            # Parse expected version from SWU if not supplied explicitly
            swu_meta = parse_swu_description(self.swu_path)
            if not self.expected_version and "version" in swu_meta:
                self.expected_version = swu_meta["version"]
                self._log(f"Extracted expected version from SWU: {self.expected_version}")

            if not self.expected_version:
                raise DeployError(
                    "precheck",
                    f"No expected version could be determined from '{self.swu_path}' (sw-description missing or unreadable) and --expected-version was not specified",
                )

            if not self.runner.check_reachability(timeout=self._remaining("precheck", 5.0)):
                raise DeployError("precheck", "Bench device is not reachable over SSH")

            # Record initial boot ID for reboot detection (Finding 3)
            res_boot = self._run("cat /proc/sys/kernel/random/boot_id", "precheck", 5.0)
            if res_boot.ok and res_boot.stdout.strip():
                self.initial_boot_id = res_boot.stdout.strip()
                self._log(f"Initial device boot ID: {self.initial_boot_id}")

            previous_slot, previous_version, initial_status = self.query_device_state()
            if previous_slot not in ("root_a", "root_b"):
                raise DeployError(
                    "precheck",
                    f"Could not determine valid active slot from device (got '{previous_slot}')",
                )

            target_slot = opposite_slot(previous_slot)
            self._log(
                f"Initial state: version={previous_version}, active_slot={previous_slot} "
                f"-> target_slot={target_slot}"
            )
            self.timer.stop("precheck")

            # Step 2: Upload (Stage)
            self.timer.start("upload")
            self._log("Step 2: Uploading update payload to device staging...")
            # Upload via stream to staging buffer and trigger nx-sysd update stage
            upload_cmd = (
                "trap 'rm -f /data/update/staging.upload.swu || exit 1' EXIT; "
                "trap 'exit 1' HUP INT TERM; "
                "mkdir -p /data/update && "
                "cat > /data/update/staging.upload.swu && "
                "nx-sysd update stage --file /data/update/staging.upload.swu"
            )
            res_upload = self.runner.stream_upload(
                self.swu_path, upload_cmd, timeout=self._remaining("upload")
            )
            if not res_upload.ok:
                raise DeployError(
                    "upload",
                    f"Upload/stage failed (code {res_upload.returncode}): {res_upload.stderr.strip() or res_upload.stdout.strip()}",
                )
            self._log("Upload and stage completed successfully.")
            self.timer.stop("upload")

            # Step 3: Validate
            self.timer.start("validate")
            self._log("Step 3: Validating staged update on device...")
            res_val = self._run("nx-sysd update validate", "validate", 30.0)
            if not res_val.ok:
                raise DeployError(
                    "validate",
                    f"Validation rejected update (code {res_val.returncode}): {res_val.stderr.strip() or res_val.stdout.strip()}",
                )
            self._log("Validation succeeded.")
            self.timer.stop("validate")

            # Step 4: Install
            self.timer.start("install")
            self._log(f"Step 4: Installing image artifacts to inactive slot ({target_slot})...")
            res_inst = self._run("nx-sysd update install", "install", 60.0)
            if not res_inst.ok:
                raise DeployError(
                    "install",
                    f"Installation into inactive slot failed (code {res_inst.returncode}): {res_inst.stderr.strip() or res_inst.stdout.strip()}",
                )
            self._log(f"Installation into {target_slot} completed.")
            self.timer.stop("install")

            # Step 5: Start Trial (Trigger trial boot and reboot)
            self.timer.start("start_trial")
            self._log(
                f"Step 5: Arming trial boot for {target_slot} (remote_confirm={self.remote_confirm})..."
            )
            res_trial = self._run("nx-sysd update trial", "start_trial", 15.0)
            if not res_trial.ok:
                raise DeployError(
                    "start_trial",
                    f"Starting trial boot failed (code {res_trial.returncode}): {res_trial.stderr.strip() or res_trial.stdout.strip()}",
                )
            self._log("Trial armed, device is rebooting.")
            self.timer.stop("start_trial")

            # Step 6: Wait for Device
            self.timer.start("wait_for_device")
            self._log(
                f"Step 6: Waiting for device to reboot and return (timeout={self.timeout_secs}s)..."
            )

            start_wait = time.monotonic()
            device_returned = False
            device_went_down = False

            while self.timer.total_elapsed() < self.timeout_secs:
                # 1. If initial_boot_id is known, check if boot ID has changed
                if self.initial_boot_id:
                    res_boot = self.runner.run(
                        "cat /proc/sys/kernel/random/boot_id",
                        timeout=self._remaining("wait_for_device", min(3.0, self.poll_interval_secs + 1.0)),
                    )
                    if res_boot.ok and res_boot.stdout.strip():
                        current_boot_id = res_boot.stdout.strip()
                        if current_boot_id != self.initial_boot_id:
                            self._log(
                                f"Device boot ID changed ({self.initial_boot_id} -> {current_boot_id}); reboot confirmed."
                            )
                            device_returned = True
                            break
                        else:
                            self._log(
                                "Device still reachable with pre-reboot boot ID (shutdown in progress)..."
                            )
                            time.sleep(min(self.poll_interval_secs, max(0, self.timeout_secs - self.timer.total_elapsed())))
                            continue

                # 2. Check reachability
                is_reachable = self.runner.check_reachability(
                    timeout=min(3.0, self.poll_interval_secs + 1.0)
                )
                if not is_reachable:
                    device_went_down = True
                    self._log("Device observed unreachable (reboot in progress).")
                elif device_went_down:
                    self._log("Device returned after reboot.")
                    device_returned = True
                    break
                elif not self.initial_boot_id:
                    # Fallback when boot_id is unavailable on the target
                    if (time.monotonic() - start_wait) > max(3.0, self.poll_interval_secs * 2):
                        self._log("Device reachable (boot ID unavailable).")
                        device_returned = True
                        break

                time.sleep(min(self.poll_interval_secs, max(0, self.timeout_secs - self.timer.total_elapsed())))

            if not device_returned:
                raise DeployError(
                    "wait_for_device",
                    f"Bench device did not return within {self.timeout_secs}s timeout",
                )
            self._log(f"Device returned after {self.timer.get_elapsed('wait_for_device'):.1f}s.")
            self.timer.stop("wait_for_device")

            # Step 7: Check running slot and image version
            self.timer.start("check_running")
            self._log("Step 7: Verifying running slot and image version...")
            running_slot, new_version, device_update_state = self.query_device_state("check_running")

            # Check for rollback (AC4)
            if running_slot == previous_slot:
                rollback_detected = True
                raise DeployError(
                    "slot_check",
                    f"Rollback detected: device booted previous slot '{previous_slot}' instead of target slot '{target_slot}'",
                    details=device_update_state,
                )

            if running_slot != target_slot:
                raise DeployError(
                    "slot_check",
                    f"Unexpected running slot '{running_slot}' (expected '{target_slot}')",
                    details=device_update_state,
                )

            # Check version match (AC1, AC3)
            if new_version != self.expected_version:
                raise DeployError(
                    "version_check",
                    f"Image version mismatch: running '{new_version}', expected '{self.expected_version}'",
                )

            self._log(
                f"Verification passed: running_slot={running_slot}, new_version={new_version}"
            )
            self.timer.stop("check_running")

            # Step 8: Confirm (AD-22)
            self.timer.start("confirm")
            if self.remote_confirm:
                res_conf = self._run("nx-sysd update confirm", "confirm", 15.0)
                if not res_conf.ok:
                    raise DeployError("confirm", f"Remote confirmation rejected: {res_conf.stderr or res_conf.stdout}")
                confirmation_sent = True

            # Both modes require an observed committed state. Trial may commit asynchronously.
            final_status: Dict[str, Any] = {}
            while True:
                _, _, final_status = self.query_device_state("confirm")
                raw_state = final_status.get("state")
                final_state = str(raw_state) if raw_state is not None else "unknown"
                state = final_state.lower()
                if state == "committed":
                    break
                if state != "trial":
                    raise DeployError("confirm", f"Update state is {final_state}", details=final_status)
                remaining = self.timeout_secs - self.timer.total_elapsed()
                if remaining <= 0:
                    raise DeployError("confirm", "Update still in trial at timeout", details=final_status)
                time.sleep(min(self.poll_interval_secs, remaining))
                if self.timer.total_elapsed() >= self.timeout_secs:
                    raise DeployError("confirm", "Update still in trial at timeout", details=final_status)
            self.timer.stop("confirm")

            return DeployResult(
                success=True,
                previous_version=previous_version,
                new_version=new_version,
                running_slot=running_slot,
                final_state=final_state,
                step_times=self.timer.all_steps(),
                confirmation_sent=confirmation_sent,
                rollback_detected=False,
                device_update_state=final_status,
            )

        except DeployError as e:
            failing_step = e.step
            error_msg = e.message
            if e.details:
                device_update_state = e.details
            self._log(f"Error at step '{failing_step}': {error_msg}")
        except Exception as e:
            failing_step = "unknown"
            error_msg = str(e)
            self._log(f"Unexpected error: {error_msg}")

        return DeployResult(
            success=False,
            previous_version=previous_version,
            new_version=new_version,
            running_slot=running_slot,
            final_state="failed",
            step_times=self.timer.all_steps(),
            failing_step=failing_step,
            error_message=error_msg,
            confirmation_sent=confirmation_sent,
            rollback_detected=rollback_detected,
            device_update_state=device_update_state,
        )


def format_text_report(res: DeployResult) -> str:
    """Format human-readable deploy report."""
    lines: List[str] = ["=" * 60]
    if res.success:
        lines.append("Deploy Succeeded")
        lines.append("=" * 60)
        lines.append(f"Previous Version:  {res.previous_version}")
        lines.append(f"New Version:       {res.new_version}")
        lines.append(f"Running Slot:      {res.running_slot}")
        lines.append(f"Final State:       {res.final_state}")
        lines.append(f"Confirmation Sent: {'Yes' if res.confirmation_sent else 'No'}")
    else:
        lines.append("Deploy FAILED")
        lines.append("=" * 60)
        lines.append(f"Failing Step:      {res.failing_step}")
        lines.append(f"Error:             {res.error_message}")
        lines.append(f"Confirmation Sent: {'Yes' if res.confirmation_sent else 'No'}")
        if res.rollback_detected:
            lines.append(f"Rollback Detected: Device returned on previous slot '{res.running_slot}'")
        if res.device_update_state:
            state_str = json.dumps(res.device_update_state, sort_keys=True)
            lines.append(f"Update State:      {state_str}")

    lines.append("-" * 60)
    lines.append("Step Elapsed Times:")
    for step, duration in res.step_times.items():
        if step != "total":
            lines.append(f"  {step:<16} {duration:6.2f}s")
    lines.append(f"  {'total':<16} {res.step_times.get('total', 0.0):6.2f}s")
    lines.append("=" * 60)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser for deploy tool."""
    parser = argparse.ArgumentParser(
        description="One-command deploy tool for nextoomio test bench (P0-F5.1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Positional arguments: <device-host> and <update-file.swu> in either order.",
    )
    parser.add_argument(
        "-d",
        "--device",
        help="Bench device hostname or IP address.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=pathlib.Path,
        help="Path to the built .swu update file.",
    )
    parser.add_argument(
        "-u",
        "--user",
        default=DEFAULT_SSH_USER,
        help=f"SSH user for bench access (default: '{DEFAULT_SSH_USER}').",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_SSH_PORT,
        help=f"SSH port on target (default: {DEFAULT_SSH_PORT}).",
    )
    parser.add_argument(
        "--expected-version",
        help="Expected image version after update (auto-detected from SWU if omitted).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECS,
        help=f"Total deployment time budget in seconds (default: {DEFAULT_TIMEOUT_SECS}s).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECS,
        help=f"Polling interval while waiting for device (default: {DEFAULT_POLL_INTERVAL_SECS}s).",
    )
    parser.add_argument(
        "--no-remote-confirm",
        action="store_true",
        help="Do not request remote confirmation (commit on local health check alone).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output deploy summary as JSON.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose step logging to stderr.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run embedded automated self-tests and exit.",
    )
    return parser


def parse_arguments(argv: List[str]) -> Tuple[argparse.Namespace, Optional[str], Optional[pathlib.Path]]:
    """Parse and resolve device target and update file arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.test:
        return args, None, None

    device = args.device
    update_file = args.file

    # Resolve positional arguments if supplied
    for t in args.targets:
        p = pathlib.Path(t)
        if p.is_file() or t.endswith(".swu"):
            if not update_file:
                update_file = p
            elif not device:
                device = t
        else:
            if not device:
                device = t
            elif not update_file:
                update_file = p

    return args, device, update_file


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    if argv is None:
        argv = sys.argv[1:]

    try:
        args, device, update_file = parse_arguments(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if args.test:
        from test_deploy import run_self_tests
        return run_self_tests()

    if not device:
        sys.stderr.write("Error: device target must be specified via argument or -d/--device.\n")
        return 2

    if not is_safe_identifier(device):
        sys.stderr.write(
            f"Error: invalid device '{device}': host must match ^[A-Za-z0-9][A-Za-z0-9._-]*$\n"
        )
        return 2

    if not is_safe_identifier(args.user):
        sys.stderr.write(
            f"Error: invalid user '{args.user}': user must match ^[A-Za-z0-9][A-Za-z0-9._-]*$\n"
        )
        return 2

    if not update_file:
        sys.stderr.write("Error: update file must be specified via argument or -f/--file.\n")
        return 2

    if not update_file.exists():
        sys.stderr.write(f"Error: update file '{update_file}' does not exist.\n")
        return 2

    if not (0 < args.timeout < float("inf") and 0 < args.poll_interval < float("inf")):
        sys.stderr.write("Error: timeout and poll interval must be finite positive numbers.\n")
        return 2

    runner = SystemSshRunner(host=device, user=args.user, port=args.port)
    deployer = Deployer(
        runner=runner,
        swu_path=update_file,
        expected_version=args.expected_version,
        timeout_secs=args.timeout,
        poll_interval_secs=args.poll_interval,
        remote_confirm=not args.no_remote_confirm,
        verbose=args.verbose,
    )

    result = deployer.deploy()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_text_report(result))

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
