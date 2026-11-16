#!/usr/bin/env python3
"""Automated self-tests and test fixtures for workstation bench power control (P0-F7.1).

Validates:
- AC2: Power command execution for 'on', 'off', 'cycle', and 'status'.
- AC3: Configuration loading, untracked credentials, and switching between
       different outlet types (generic HTTP, Shelly, Tasmota, Mock) via config only.
- CQ-R6: Redaction of passwords, tokens, and credentials in all logs, outputs, and errors.
- Exit codes: 0 (success), 1 (config error), 2 (network error), 3 (HTTP error), 4 (action failed).
- Shell wrapper: scripts/bench/power.sh functionality.

Runs completely offline without opening external network sockets.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
from typing import Any, Dict, Optional
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
POWER_PY = ROOT / "scripts" / "bench" / "power.py"
POWER_SH = ROOT / "scripts" / "bench" / "power.sh"

sys.path.insert(0, str(ROOT / "scripts" / "bench"))
import power  # type: ignore


class DummyHttpResponse:
    """Mock HTTP response object for urllib.request.urlopen."""

    def __init__(self, status: int = 200, body: bytes = b"OK", headers: Optional[Dict[str, str]] = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "DummyHttpResponse":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


def run_power_cmd(args: list[str], env: Optional[dict] = None) -> subprocess.CompletedProcess[str]:
    """Execute scripts/bench/power.py as a subprocess."""
    cmd = [sys.executable, str(POWER_PY)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env if env is not None else os.environ.copy(),
    )


def test_missing_config_fails_cleanly() -> None:
    """AC3: Fails cleanly with exit code 1 when no configuration exists."""
    print("Testing missing configuration handling (AC3)...")
    with tempfile.TemporaryDirectory() as empty_dir:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": empty_dir,
        }
        res = run_power_cmd(["--config", str(pathlib.Path(empty_dir) / "nonexistent.json"), "status"], env=env)
        assert res.returncode == 1, f"Expected exit code 1, got {res.returncode}"
        assert "does not exist" in res.stderr

        res_auto = run_power_cmd(["status"], env=env)
        assert res_auto.returncode == 1, f"Expected exit code 1, got {res_auto.returncode}"
        assert "No local outlet configuration found" in res_auto.stderr or "config_error" in res_auto.stderr
        assert "outlet.example.json" in res_auto.stderr

    print("  test_missing_config_fails_cleanly: PASSED")


def test_mock_driver_operations() -> None:
    """AC2: Test mock driver execution of on, off, cycle, and status."""
    print("Testing mock driver operations (AC2)...")
    # 1. Turn ON
    res_on = run_power_cmd(["--mock", "on", "--json"])
    assert res_on.returncode == 0, f"Expected 0, got {res_on.returncode}: {res_on.stderr}"
    data_on = json.loads(res_on.stdout)
    assert data_on["status"] == "success"
    assert data_on["action"] == "on"
    assert data_on["driver"] == "mock"

    # 2. Status check
    res_status = run_power_cmd(["--mock", "status", "--json"])
    assert res_status.returncode == 0
    data_status = json.loads(res_status.stdout)
    assert data_status["status"] == "success"

    # 3. Turn OFF
    res_off = run_power_cmd(["--mock", "off", "--json"])
    assert res_off.returncode == 0
    data_off = json.loads(res_off.stdout)
    assert data_off["status"] == "success"
    assert data_off["action"] == "off"

    # 4. Power cycle with short delay
    t_start = time.time()
    res_cycle = run_power_cmd(["--mock", "cycle", "--delay", "0.2", "--json"])
    elapsed = time.time() - t_start
    assert res_cycle.returncode == 0
    data_cycle = json.loads(res_cycle.stdout)
    assert data_cycle["status"] == "success"
    assert len(data_cycle["steps"]) == 3  # off, delay, on
    assert elapsed >= 0.18

    # 5. Simulated failure on mock action
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"driver": "mock", "mock_fail_action": "off"}, f)
        cfg_path = f.name
    try:
        res_fail = run_power_cmd(["--config", cfg_path, "off", "--json"])
        assert res_fail.returncode == power.EXIT_ACTION_FAILED
        data_fail = json.loads(res_fail.stdout)
        assert data_fail["status"] == "error"
    finally:
        if os.path.exists(cfg_path):
            os.unlink(cfg_path)

    print("  test_mock_driver_operations: PASSED")


def test_http_driver_mocked_requests() -> None:
    """AC2 & AC3: Test generic HTTP driver requests, authentication, and responses."""
    print("Testing generic HTTP driver with mocked HTTP requests (AC2, AC3)...")
    captured_requests: list[urllib.request.Request] = []

    def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
        captured_requests.append(req)
        full_url = req.full_url
        if "turn=on" in full_url:
            return DummyHttpResponse(200, b'{"ison": true}')
        elif "turn=off" in full_url:
            return DummyHttpResponse(200, b'{"ison": false}')
        elif "/relay/0" in full_url:
            return DummyHttpResponse(200, b'{"ison": true}')
        return DummyHttpResponse(200, b'{"status": "ok"}')

    cfg = {
        "driver": "http",
        "host": "http://outlet.local",
        "cycle_delay_seconds": 0.05,
        "timeout_seconds": 5.0,
        "auth": {
            "type": "bearer",
            "token": "secret-test-token-12345",
        },
        "actions": {
            "on": {"method": "GET", "path": "/relay/0?turn=on"},
            "off": {"method": "GET", "path": "/relay/0?turn=off"},
            "status": {
                "method": "GET",
                "path": "/relay/0",
                "on_pattern": r'"ison":\s*true',
                "off_pattern": r'"ison":\s*false',
            },
        },
    }

    driver = power.create_driver(cfg)
    with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # 1. Turn ON
        report_on = power.execute_power_action(driver, "on", quiet=True)
        assert report_on["status"] == "success"
        assert len(captured_requests) == 1
        last_req = captured_requests[-1]
        assert last_req.full_url == "http://outlet.local/relay/0?turn=on"
        assert last_req.get_header("Authorization") == "Bearer secret-test-token-12345"

        # 2. Status
        report_stat = power.execute_power_action(driver, "status", quiet=True)
        assert report_stat["status"] == "success"
        assert report_stat["state"] == "on"

        # 3. Turn OFF
        report_off = power.execute_power_action(driver, "off", quiet=True)
        assert report_off["status"] == "success"
        assert "turn=off" in captured_requests[-1].full_url

        # 4. Cycle
        report_cycle = power.execute_power_action(driver, "cycle", cycle_delay=0.01, quiet=True)
        assert report_cycle["status"] == "success"
        assert len(report_cycle["steps"]) == 3

    print("  test_http_driver_mocked_requests: PASSED")


def test_shelly_and_tasmota_driver_configurations_ac3() -> None:
    """AC3: Works with different outlet types by changing only configuration."""
    print("Testing Shelly Gen 1, Gen 2 and Tasmota drivers (AC3)...")
    captured_requests: list[urllib.request.Request] = []

    def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
        captured_requests.append(req)
        full_url = req.full_url
        if "/rpc" in full_url:
            # Shelly Gen 2 RPC
            body = req.data.decode("utf-8") if req.data else ""
            if "Switch.Set" in body:
                return DummyHttpResponse(200, b'{"result": {"was_on": false}}')
            elif "Switch.GetStatus" in body:
                return DummyHttpResponse(200, b'{"output": true}')
        elif "/cm" in full_url:
            # Tasmota
            return DummyHttpResponse(200, b'{"POWER": "ON"}')
        elif "/relay/0" in full_url:
            # Shelly Gen 1
            return DummyHttpResponse(200, b'{"ison": true}')
        return DummyHttpResponse(200, b"OK")

    with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # Test 1: Shelly Gen 1
        cfg_shelly1 = {"driver": "shelly", "generation": 1, "host": "http://shelly1.local"}
        d_shelly1 = power.create_driver(cfg_shelly1)
        res1 = power.execute_power_action(d_shelly1, "on", quiet=True)
        assert res1["status"] == "success"
        assert captured_requests[-1].full_url == "http://shelly1.local/relay/0?turn=on"

        # Test 2: Shelly Gen 2 (RPC API)
        cfg_shelly2 = {"driver": "shelly", "generation": 2, "host": "http://shelly2.local"}
        d_shelly2 = power.create_driver(cfg_shelly2)
        res2 = power.execute_power_action(d_shelly2, "on", quiet=True)
        assert res2["status"] == "success"
        assert captured_requests[-1].full_url == "http://shelly2.local/rpc"
        assert captured_requests[-1].get_method() == "POST"
        body2 = json.loads(captured_requests[-1].data.decode("utf-8"))
        assert body2["method"] == "Switch.Set"
        assert body2["params"]["on"] is True

        # Test 3: Tasmota
        cfg_tasmota = {"driver": "tasmota", "host": "http://tasmota.local", "relay": 1}
        d_tasmota = power.create_driver(cfg_tasmota)
        res3 = power.execute_power_action(d_tasmota, "off", quiet=True)
        assert res3["status"] == "success"
        assert "cmnd=Power1%20Off" in captured_requests[-1].full_url or "cmnd=Power1 Off" in captured_requests[-1].full_url

    print("  test_shelly_and_tasmota_driver_configurations_ac3: PASSED")


def test_credential_redaction_cq_r6() -> None:
    """CQ-R6: Credentials and tokens never appear in stdout, stderr, or JSON reports."""
    print("Testing credential redaction in logs and reports (CQ-R6)...")
    secret_token = "ultra-secret-bearer-token-xyz"
    secret_password = "super-secret-password-abc"

    cfg = {
        "driver": "http",
        "host": f"http://admin:{secret_password}@outlet.local:8080",
        "auth": {
            "type": "bearer",
            "token": secret_token,
        },
        "actions": {
            "on": {"method": "GET", "path": "/relay/0?turn=on"},
            "off": {"method": "GET", "path": "/relay/0?turn=off"},
        },
    }

    driver = power.create_driver(cfg)
    assert secret_password not in driver.safe_target
    assert "admin:***@" in driver.safe_target

    def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
        return DummyHttpResponse(200, b'{"ison": true}')

    with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # Capture stdout/stderr during execute_power_action
        old_stderr = sys.stderr
        capture_io = io.StringIO()
        try:
            sys.stderr = capture_io
            report = power.execute_power_action(driver, "on", quiet=False)
        finally:
            sys.stderr = old_stderr

        logged_text = capture_io.getvalue()
        json_report = json.dumps(report)

        assert secret_token not in logged_text, "Secret token leaked in logged text!"
        assert secret_password not in logged_text, "Secret password leaked in logged text!"
        assert secret_token not in json_report, "Secret token leaked in JSON report!"
        assert secret_password not in json_report, "Secret password leaked in JSON report!"

    print("  test_credential_redaction_cq_r6: PASSED")


def test_http_and_network_error_exit_codes() -> None:
    """Test distinct exit codes for HTTP errors (3) and network errors (2)."""
    print("Testing error exit codes (exit 2 network, exit 3 HTTP)...")
    cfg = {
        "driver": "http",
        "host": "http://outlet.local",
        "actions": {"on": {"method": "GET", "path": "/relay/0?turn=on"}},
    }
    driver = power.create_driver(cfg)

    # 1. HTTP 500 error from outlet -> exit code 3 (HttpError)
    def mock_http_500(req: urllib.request.Request, timeout: float = 10.0):
        fp = io.BytesIO(b'{"error": "Internal Relay Failure"}')
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, fp)

    with mock.patch("urllib.request.urlopen", side_effect=mock_http_500):
        try:
            power.execute_power_action(driver, "on", quiet=True)
            assert False, "Expected HttpError"
        except power.HttpError as exc:
            assert exc.exit_code == power.EXIT_HTTP_ERROR
            assert exc.status_code == 500

    # 2. Connection refused / host unreachable -> exit code 2 (NetworkError)
    def mock_network_error(req: urllib.request.Request, timeout: float = 10.0):
        raise urllib.error.URLError("Connection refused")

    with mock.patch("urllib.request.urlopen", side_effect=mock_network_error):
        try:
            power.execute_power_action(driver, "on", quiet=True)
            assert False, "Expected NetworkError"
        except power.NetworkError as exc:
            assert exc.exit_code == power.EXIT_NETWORK_ERROR

    print("  test_http_and_network_error_exit_codes: PASSED")


def test_power_sh_shell_wrapper() -> None:
    """Test POSIX sh shell wrapper scripts/bench/power.sh."""
    print("Testing scripts/bench/power.sh wrapper...")
    cmd = ["/bin/sh", str(POWER_SH), "--mock", "status", "--json"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Expected 0 from power.sh, got {res.returncode}: {res.stderr}"
    data = json.loads(res.stdout)
    assert data["status"] == "success"
    assert data["driver"] == "mock"

    print("  test_power_sh_shell_wrapper: PASSED")


def test_outlet_example_json_is_valid_and_safe() -> None:
    """Verify scripts/bench/outlet.example.json exists, parses, and has no real secrets."""
    print("Testing outlet.example.json template validity and safety...")
    example_path = ROOT / "scripts" / "bench" / "outlet.example.json"
    assert example_path.is_file(), f"Missing example configuration: {example_path}"

    text = example_path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert isinstance(data, dict)
    assert data.get("driver") in ["http", "shelly", "tasmota", "mock"]
    assert "outlet.local" in data.get("host", "")

    # Ensure no committed private IP addresses (e.g. 192.168.x.x or 10.x.x.x) or real keys
    assert not re.search(r"\b192\.168\.\d+\.\d+\b", text), "Found private IP in example config"
    assert not re.search(r"\b10\.\d+\.\d+\.\d+\b", text), "Found private IP in example config"

    print("  test_outlet_example_json_is_valid_and_safe: PASSED")


def main() -> int:
    print("=== Running nextoomio bench power self-tests (P0-F7.1) ===")
    test_missing_config_fails_cleanly()
    test_mock_driver_operations()
    test_http_driver_mocked_requests()
    test_shelly_and_tasmota_driver_configurations_ac3()
    test_credential_redaction_cq_r6()
    test_http_and_network_error_exit_codes()
    test_power_sh_shell_wrapper()
    test_outlet_example_json_is_valid_and_safe()
    print("=== All nextoomio bench power self-tests passed successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
