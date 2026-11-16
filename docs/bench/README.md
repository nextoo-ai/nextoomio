# Test Bench Guide

This directory documents the nextoomio remote test bench and hardware configuration for Phase 0 and subsequent releases ([ROADMAP.md](../../ROADMAP.md) section 2.1).

## Bench Documents

- [record.md](record.md): The official bench record detailing the seven reference components: board, sound card, speaker, storage card, power supply, network connection, and switched outlet (AC1, #31).

## Remote Power Control (`scripts/bench/power.sh`)

Power-loss resilience tests, unattended deployments, and last-resort recovery require switching the bench device's power remotely from the workstation without physical on-site intervention (AC2, AC4).

### 1. Configuration Setup (AC3)

In accordance with acceptance criterion AC3 and rule CQ-R6, the outlet's network address and credentials are kept strictly outside version control:

1. Copy the example configuration template:
   ```bash
   cp scripts/bench/outlet.example.json scripts/bench/outlet.json
   ```
2. Edit `scripts/bench/outlet.json` to specify your outlet driver (`http`, `shelly`, `tasmota`, or `mock`) and local address/credentials.
3. Verify that `scripts/bench/outlet.json` is ignored by Git:
   ```bash
   git status --ignored scripts/bench/outlet.json
   ```

The script searches for configuration in the following order:
1. `--config <path>` command-line argument
2. `NEXTOOMIO_OUTLET_CONFIG` environment variable
3. `scripts/bench/outlet.json`
4. `.outlet.json` in repository root
5. `~/.config/nextoomio/outlet.json`

### 2. Basic Usage

Run `scripts/bench/power.sh` with one of the four actions:

```bash
# Turn outlet ON
./scripts/bench/power.sh on

# Turn outlet OFF
./scripts/bench/power.sh off

# Power cycle the device (turns OFF, waits delay, turns ON)
./scripts/bench/power.sh cycle

# Query current outlet state
./scripts/bench/power.sh status
```

### 3. Command Options

- `--delay <seconds>`: Custom delay during `cycle` (default: 5.0 seconds).
- `--timeout <seconds>`: Network request timeout (default: 10.0 seconds).
- `--config <path>`: Path to a specific configuration file.
- `--mock`: Execute in simulated mock mode without making network requests (useful for offline testing and CI).
- `--json`: Format command results as structured JSON.
- `--quiet`: Suppress human-readable progress logs on stderr.

### 4. Exit Codes

- `0`: Success.
- `1`: Configuration error (missing config, invalid JSON, or unsupported driver).
- `2`: Network / connectivity error (host unreachable, timeout, connection refused).
- `3`: HTTP error returned by outlet (4xx or 5xx).
- `4`: Action failed or unexpected response.
