#!/bin/sh
# Workstation remote power control wrapper for nextoomio test bench (P0-F7.1).
# Usage: ./scripts/bench/power.sh [on|off|cycle|status] [options...]
set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
exec python3 "$SCRIPT_DIR/power.py" "$@"
