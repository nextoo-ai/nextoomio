#!/usr/bin/env python3
"""Workstation remote power control tool for the nextoomio test bench (P0-F7.1).

Controls the switched outlet powering the bench device (Raspberry Pi 4 + HiFiBerry Amp+)
with actions: 'on', 'off', 'cycle', or 'status'.

Acceptance criteria (P0-F7.1):
- AC2: Given the switched outlet, when the power command is run from the workstation with
       'off', 'on', or 'cycle', then the bench device loses or regains power accordingly,
       and the command reports the result.
- AC3: Given the power command, when the repository is inspected, then the outlet's address
       and credentials come from local configuration and are not committed; the command works
       with a different outlet by changing only that configuration.

Local configuration search order:
  1. --config <path> CLI argument
  2. NEXTOOMIO_OUTLET_CONFIG environment variable
  3. scripts/bench/outlet.json
  4. .outlet.json (repository root)
  5. ~/.config/nextoomio/outlet.json

Credentials and sensitive headers are redacted in all logs and outputs (CQ-R6).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

DEFAULT_CYCLE_DELAY_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 10.0

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_NETWORK_ERROR = 2
EXIT_HTTP_ERROR = 3
EXIT_ACTION_FAILED = 4

SENSITIVE_KEY_RE = re.compile(
    r"(?:auth|token|secret|password|key|credential)", re.IGNORECASE
)
URL_CREDENTIALS_RE = re.compile(r"://([^:@]+):([^@]+)@")


def redact_text(text: str) -> str:
    """Redact embedded username:password in URLs."""
    return URL_CREDENTIALS_RE.sub(r"://\1:***@", text)


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Redact sensitive header values such as Authorization."""
    redacted = {}
    for key, value in headers.items():
        if SENSITIVE_KEY_RE.search(key):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


class PowerError(Exception):
    """Base error for bench power operations."""

    def __init__(self, message: str, exit_code: int = EXIT_ACTION_FAILED) -> None:
        super().__init__(redact_text(message))
        self.message = redact_text(message)
        self.exit_code = exit_code


class ConfigError(PowerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_CONFIG_ERROR)


class NetworkError(PowerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_NETWORK_ERROR)


class HttpError(PowerError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message, exit_code=EXIT_HTTP_ERROR)
        self.status_code = status_code


class OutletDriver:
    """Base interface for switched outlet control drivers."""

    def __init__(self, name: str, host: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.name = name
        self.host = host
        self.timeout = timeout

    @property
    def safe_target(self) -> str:
        return redact_text(self.host)

    def turn_on(self) -> Tuple[bool, str, Optional[int]]:
        raise NotImplementedError

    def turn_off(self) -> Tuple[bool, str, Optional[int]]:
        raise NotImplementedError

    def get_status(self) -> Tuple[bool, str, Optional[str]]:
        raise NotImplementedError


class MockOutletDriver(OutletDriver):
    """In-memory simulated outlet for verification, CI, and test fixtures."""

    def __init__(
        self,
        name: str = "mock",
        host: str = "mock://outlet",
        initial_state: str = "on",
        fail_action: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(name, host, timeout)
        self.state = initial_state
        self.fail_action = fail_action
        self.action_history: list[str] = []

    def turn_on(self) -> Tuple[bool, str, Optional[int]]:
        self.action_history.append("on")
        if self.fail_action == "on":
            return False, "Simulated mock failure on 'on'", 500
        self.state = "on"
        return True, "Mock outlet turned ON", 200

    def turn_off(self) -> Tuple[bool, str, Optional[int]]:
        self.action_history.append("off")
        if self.fail_action == "off":
            return False, "Simulated mock failure on 'off'", 500
        self.state = "off"
        return True, "Mock outlet turned OFF", 200

    def get_status(self) -> Tuple[bool, str, Optional[str]]:
        self.action_history.append("status")
        return True, f"Mock outlet state is {self.state}", self.state


class HttpOutletDriver(OutletDriver):
    """Generic REST / HTTP switched outlet driver.

    Supports configurable HTTP methods, paths, headers, and payloads for
    any smart plug or network PDU with a REST/HTTP interface.
    """

    def __init__(
        self,
        host: str,
        config: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__("http", host, timeout)
        self.config = config
        self.actions = config.get("actions", {})
        self.base_headers = config.get("headers", {})
        self.auth_config = config.get("auth", {})

    def _build_headers(self, action_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "User-Agent": "nextoomio-bench-power/1.0",
        }
        headers.update(self.base_headers)
        if action_headers:
            headers.update(action_headers)

        auth_type = str(self.auth_config.get("type", "none")).lower()
        if auth_type == "basic":
            user = self.auth_config.get("username", "")
            pwd = self.auth_config.get("password", "")
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        elif auth_type == "bearer":
            token = self.auth_config.get("token", "")
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "header":
            header_name = self.auth_config.get("header_name", "X-Auth-Token")
            header_val = self.auth_config.get("header_value", "")
            headers[header_name] = header_val

        return headers

    def _execute_http(
        self, action_def: Dict[str, Any], action_name: str
    ) -> Tuple[int, bytes, Dict[str, str]]:
        path = action_def.get("path", "")
        method = action_def.get("method", "GET").upper()
        custom_url = action_def.get("url")

        if custom_url:
            full_url = custom_url
        else:
            base = self.host.rstrip("/")
            sub = path.lstrip("/")
            full_url = f"{base}/{sub}" if sub else base

        headers = self._build_headers(action_def.get("headers"))
        body_data = action_def.get("body")
        payload: Optional[bytes] = None

        if body_data is not None:
            if isinstance(body_data, (dict, list)):
                payload = json.dumps(body_data).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif isinstance(body_data, str):
                payload = body_data.encode("utf-8")
            elif isinstance(body_data, bytes):
                payload = body_data

        req = urllib.request.Request(full_url, data=payload, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                body = resp.read()
                resp_headers = dict(resp.headers)
                return status, body, resp_headers
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise HttpError(
                f"Outlet returned HTTP error {exc.code} for action '{action_name}': {err_body[:200]}",
                status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise NetworkError(
                f"Failed to reach outlet at '{self.safe_target}' for action '{action_name}': {reason}"
            ) from exc

    def turn_on(self) -> Tuple[bool, str, Optional[int]]:
        act = self.actions.get("on")
        if not act:
            raise ConfigError("Outlet configuration missing 'actions.on' definition")
        status, body, _ = self._execute_http(act, "on")
        body_text = body.decode("utf-8", errors="replace")
        success_pattern = act.get("success_pattern")
        if success_pattern and not re.search(success_pattern, body_text):
            return False, f"Outlet response did not match expected success pattern: {body_text[:100]}", status
        return True, f"Outlet turned ON (HTTP {status})", status

    def turn_off(self) -> Tuple[bool, str, Optional[int]]:
        act = self.actions.get("off")
        if not act:
            raise ConfigError("Outlet configuration missing 'actions.off' definition")
        status, body, _ = self._execute_http(act, "off")
        body_text = body.decode("utf-8", errors="replace")
        success_pattern = act.get("success_pattern")
        if success_pattern and not re.search(success_pattern, body_text):
            return False, f"Outlet response did not match expected success pattern: {body_text[:100]}", status
        return True, f"Outlet turned OFF (HTTP {status})", status

    def get_status(self) -> Tuple[bool, str, Optional[str]]:
        act = self.actions.get("status")
        if not act:
            return True, "No status check configured for outlet", None
        status, body, _ = self._execute_http(act, "status")
        body_text = body.decode("utf-8", errors="replace")
        on_pattern = act.get("on_pattern")
        off_pattern = act.get("off_pattern")

        state: Optional[str] = None
        if on_pattern and re.search(on_pattern, body_text, re.IGNORECASE):
            state = "on"
        elif off_pattern and re.search(off_pattern, body_text, re.IGNORECASE):
            state = "off"

        msg = f"Outlet status returned HTTP {status}"
        if state:
            msg += f", detected state: {state.upper()}"
        return True, msg, state


class ShellyOutletDriver(HttpOutletDriver):
    """Specialized driver for Shelly smart plugs (Gen 1 and Gen 2)."""

    def __init__(
        self,
        host: str,
        config: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        generation = int(config.get("generation", 1))
        channel = int(config.get("channel", 0))

        # Default Shelly configurations if not explicitly overridden
        actions = dict(config.get("actions", {}))
        if generation == 1:
            actions.setdefault("on", {"method": "GET", "path": f"/relay/{channel}?turn=on"})
            actions.setdefault("off", {"method": "GET", "path": f"/relay/{channel}?turn=off"})
            actions.setdefault(
                "status",
                {
                    "method": "GET",
                    "path": f"/relay/{channel}",
                    "on_pattern": r'"ison":\s*true',
                    "off_pattern": r'"ison":\s*false',
                },
            )
        else:
            # Gen 2 / Plus / Pro (RPC API)
            actions.setdefault(
                "on",
                {
                    "method": "POST",
                    "path": "/rpc",
                    "body": {"id": 1, "method": "Switch.Set", "params": {"id": channel, "on": True}},
                },
            )
            actions.setdefault(
                "off",
                {
                    "method": "POST",
                    "path": "/rpc",
                    "body": {"id": 1, "method": "Switch.Set", "params": {"id": channel, "on": False}},
                },
            )
            actions.setdefault(
                "status",
                {
                    "method": "POST",
                    "path": "/rpc",
                    "body": {"id": 1, "method": "Switch.GetStatus", "params": {"id": channel}},
                    "on_pattern": r'"output":\s*true',
                    "off_pattern": r'"output":\s*false',
                },
            )

        merged_config = dict(config, actions=actions)
        super().__init__(host, merged_config, timeout)
        self.name = f"shelly-gen{generation}"


class TasmotaOutletDriver(HttpOutletDriver):
    """Specialized driver for Tasmota-flashed smart outlets."""

    def __init__(
        self,
        host: str,
        config: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        actions = dict(config.get("actions", {}))
        relay = config.get("relay", "")
        power_cmd = f"Power{relay}" if relay else "Power"

        actions.setdefault("on", {"method": "GET", "path": f"/cm?cmnd={power_cmd}%20On"})
        actions.setdefault("off", {"method": "GET", "path": f"/cm?cmnd={power_cmd}%20Off"})
        actions.setdefault(
            "status",
            {
                "method": "GET",
                "path": f"/cm?cmnd={power_cmd}",
                "on_pattern": r'"POWER[0-9]*":\s*"ON"',
                "off_pattern": r'"POWER[0-9]*":\s*"OFF"',
            },
        )
        merged_config = dict(config, actions=actions)
        super().__init__(host, merged_config, timeout)
        self.name = "tasmota"


def resolve_config_path(cli_path: Optional[str] = None) -> Optional[pathlib.Path]:
    """Find the local outlet configuration file in precedence order."""
    if cli_path:
        p = pathlib.Path(cli_path).resolve()
        if p.is_file():
            return p
        raise ConfigError(f"Specified configuration file does not exist: {cli_path}")

    env_path = os.environ.get("NEXTOOMIO_OUTLET_CONFIG")
    if env_path:
        p = pathlib.Path(env_path).resolve()
        if p.is_file():
            return p
        raise ConfigError(f"Configuration file in NEXTOOMIO_OUTLET_CONFIG does not exist: {env_path}")

    search_locations = [
        ROOT / "scripts" / "bench" / "outlet.json",
        ROOT / ".outlet.json",
        pathlib.Path.home() / ".config" / "nextoomio" / "outlet.json",
    ]

    for cand in search_locations:
        if cand.is_file():
            return cand

    return None


def load_outlet_config(config_path: Optional[pathlib.Path]) -> Dict[str, Any]:
    """Load and parse the JSON configuration file."""
    if not config_path or not config_path.is_file():
        raise ConfigError(
            "No local outlet configuration found. Bench outlet address and credentials are kept "
            "outside version control (AC3). Create 'scripts/bench/outlet.json' from "
            "'scripts/bench/outlet.example.json' or specify --config <path>."
        )

    try:
        content = config_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ConfigError(f"Configuration at {config_path} must be a JSON object")
        return data
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in configuration file {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file {config_path}: {exc}") from exc


def create_driver(
    config: Dict[str, Any],
    force_mock: bool = False,
    timeout_override: Optional[float] = None,
) -> OutletDriver:
    """Instantiate the appropriate outlet driver based on configuration."""
    if force_mock:
        return MockOutletDriver(
            name="mock",
            host=config.get("host", "mock://outlet"),
            initial_state=config.get("mock_initial_state", "on"),
            fail_action=config.get("mock_fail_action"),
            timeout=timeout_override or DEFAULT_TIMEOUT_SECONDS,
        )

    driver_type = str(config.get("driver", "http")).lower()
    host = config.get("host", "")
    timeout = timeout_override or float(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    if driver_type == "mock":
        return MockOutletDriver(
            name="mock",
            host=host or "mock://outlet",
            initial_state=config.get("mock_initial_state", "on"),
            fail_action=config.get("mock_fail_action"),
            timeout=timeout,
        )

    if not host:
        raise ConfigError("Outlet configuration must specify 'host' (e.g. 'http://outlet.local')")

    if driver_type == "shelly":
        return ShellyOutletDriver(host, config, timeout)
    elif driver_type == "tasmota":
        return TasmotaOutletDriver(host, config, timeout)
    elif driver_type == "http":
        return HttpOutletDriver(host, config, timeout)
    else:
        raise ConfigError(
            f"Unsupported outlet driver '{driver_type}'. Supported drivers: 'http', 'shelly', 'tasmota', 'mock'."
        )


def execute_power_action(
    driver: OutletDriver,
    action: str,
    cycle_delay: float = DEFAULT_CYCLE_DELAY_SECONDS,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Execute the requested power action and return a structured result."""
    report: Dict[str, Any] = {
        "action": action,
        "driver": driver.name,
        "target": driver.safe_target,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "pending",
        "steps": [],
    }

    def log(msg: str) -> None:
        if not quiet:
            print(f"[power] {msg}", file=sys.stderr)

    if action == "on":
        log(f"Turning ON outlet at {driver.safe_target} (driver: {driver.name})...")
        ok, msg, code = driver.turn_on()
        report["steps"].append({"action": "on", "success": ok, "message": msg, "http_code": code})
        if not ok:
            report["status"] = "failed"
            report["error"] = msg
            raise PowerError(f"Turn ON failed: {msg}")
        report["status"] = "success"
        log(f"-> {msg}")

    elif action == "off":
        log(f"Turning OFF outlet at {driver.safe_target} (driver: {driver.name})...")
        ok, msg, code = driver.turn_off()
        report["steps"].append({"action": "off", "success": ok, "message": msg, "http_code": code})
        if not ok:
            report["status"] = "failed"
            report["error"] = msg
            raise PowerError(f"Turn OFF failed: {msg}")
        report["status"] = "success"
        log(f"-> {msg}")

    elif action == "cycle":
        log(f"Power cycling outlet at {driver.safe_target} (driver: {driver.name}, delay: {cycle_delay:.1f}s)...")
        # Step 1: Turn off
        log("Step 1/3: Turning OFF...")
        ok_off, msg_off, code_off = driver.turn_off()
        report["steps"].append({"action": "off", "success": ok_off, "message": msg_off, "http_code": code_off})
        if not ok_off:
            report["status"] = "failed"
            report["error"] = f"Power cycle aborted on turn-off: {msg_off}"
            raise PowerError(f"Power cycle failed during turn-off: {msg_off}")
        log(f"-> {msg_off}")

        # Step 2: Delay
        log(f"Step 2/3: Waiting {cycle_delay:.1f}s power-down delay...")
        time.sleep(cycle_delay)
        report["steps"].append({"action": "delay", "seconds": cycle_delay, "success": True})

        # Step 3: Turn on
        log("Step 3/3: Turning ON...")
        ok_on, msg_on, code_on = driver.turn_on()
        report["steps"].append({"action": "on", "success": ok_on, "message": msg_on, "http_code": code_on})
        if not ok_on:
            report["status"] = "failed"
            report["error"] = f"Power cycle turn-on failed: {msg_on}"
            raise PowerError(f"Power cycle failed during turn-on: {msg_on}")
        log(f"-> {msg_on}")
        report["status"] = "success"
        log("Power cycle completed successfully.")

    elif action == "status":
        log(f"Querying outlet status at {driver.safe_target} (driver: {driver.name})...")
        ok, msg, state = driver.get_status()
        report["steps"].append({"action": "status", "success": ok, "message": msg, "state": state})
        if not ok:
            report["status"] = "failed"
            report["error"] = msg
            raise PowerError(f"Status check failed: {msg}")
        report["status"] = "success"
        report["state"] = state
        log(f"-> {msg}")

    else:
        raise ConfigError(f"Unknown power action '{action}'. Valid actions: 'on', 'off', 'cycle', 'status'.")

    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Workstation remote power control for nextoomio test bench (P0-F7.1)."
    )
    parser.add_argument(
        "action",
        choices=["on", "off", "cycle", "status"],
        help="Power action to execute on the bench switched outlet",
    )
    parser.add_argument(
        "--config",
        "-c",
        help="Path to local outlet configuration file (default: scripts/bench/outlet.json)",
    )
    parser.add_argument(
        "--delay",
        "-d",
        type=float,
        help=f"Power cycle delay in seconds (default: {DEFAULT_CYCLE_DELAY_SECONDS}s)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        help=f"Network request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}s)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute against an in-memory mock driver (for offline tests / dry run)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON results to stdout",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress human-readable progress logs on stderr",
    )

    args = parser.parse_args(argv)

    try:
        if args.mock:
            config: Dict[str, Any] = {"driver": "mock", "host": "mock://bench-outlet"}
            config_source = "CLI --mock"
        else:
            cfg_path = resolve_config_path(args.config)
            config = load_outlet_config(cfg_path)
            config_source = str(cfg_path)

        delay = (
            args.delay
            if args.delay is not None
            else float(config.get("cycle_delay_seconds", DEFAULT_CYCLE_DELAY_SECONDS))
        )
        driver = create_driver(config, force_mock=args.mock, timeout_override=args.timeout)

        if not args.quiet and not args.json:
            print(f"[power] Using config source: {config_source}", file=sys.stderr)

        report = execute_power_action(
            driver=driver,
            action=args.action,
            cycle_delay=delay,
            quiet=args.quiet or args.json,
        )

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Result: {report['status'].upper()} (action: {args.action}, target: {driver.safe_target})")

        return EXIT_OK

    except ConfigError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": exc.message, "exit_code": exc.exit_code}))
        else:
            print(f"[power:config_error] {exc.message}", file=sys.stderr)
        return exc.exit_code

    except NetworkError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": exc.message, "exit_code": exc.exit_code}))
        else:
            print(f"[power:network_error] {exc.message}", file=sys.stderr)
        return exc.exit_code

    except HttpError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": exc.message,
                        "status_code": exc.status_code,
                        "exit_code": exc.exit_code,
                    }
                )
            )
        else:
            print(f"[power:http_error] {exc.message}", file=sys.stderr)
        return exc.exit_code

    except PowerError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": exc.message, "exit_code": exc.exit_code}))
        else:
            print(f"[power:error] {exc.message}", file=sys.stderr)
        return exc.exit_code

    except Exception as exc:
        redacted = redact_text(str(exc))
        if args.json:
            print(json.dumps({"status": "error", "error": redacted, "exit_code": EXIT_ACTION_FAILED}))
        else:
            print(f"[power:unexpected_error] {redacted}", file=sys.stderr)
        return EXIT_ACTION_FAILED


if __name__ == "__main__":
    sys.exit(main())
