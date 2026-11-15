#!/bin/sh
# Check and repair /data partition before mount (AC3, AC5, ARCHITECTURE.md section 3.5)
set -eu

DATA_DEV="${NEXTOOMIO_DATA_DEV:-/dev/disk/by-label/data}"
RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"
E2FSCK_CMD="${NEXTOOMIO_E2FSCK_CMD:-e2fsck}"

if [ -L "$DATA_DEV" ]; then
    DATA_DEV=$(readlink -f "$DATA_DEV")
elif [ ! -b "$DATA_DEV" ] && [ -z "${NEXTOOMIO_DATA_DEV:-}" ]; then
    DATA_DEV="/dev/mmcblk0p7"
fi

if [ ! -b "$DATA_DEV" ] && [ ! -f "$DATA_DEV" ]; then
    echo "nextoomio-data-check: data partition block device not found" >&2
    mkdir -p "$RUN_DIR"
    echo "data_unavailable" > "$RUN_DIR/data_status"
    touch "$RUN_DIR/data_unavailable"
    exit 0
fi

echo "nextoomio-data-check: checking and repairing data partition ($DATA_DEV)..."

# e2fsck options:
# -p: preen mode, automatically repairs harmless errors
# -f: force check even if clean flag is set
set +e
"$E2FSCK_CMD" -p -f "$DATA_DEV"
RC=$?
set -e

# e2fsck exit codes:
# 0 - No errors found
# 1 - File system errors corrected
# 2 - File system errors corrected, system should be rebooted
# 4 - File system errors left uncorrected
# 8 - Operational error
# 16 - Usage or syntax error
# 32 - Cancelled by user
if [ "$RC" -le 1 ]; then
    echo "nextoomio-data-check: file system check passed (exit code $RC)"
    rm -f "$RUN_DIR/data_unavailable"
    mkdir -p "$RUN_DIR"
    echo "data_available" > "$RUN_DIR/data_status"
elif [ "$RC" -eq 2 ]; then
    echo "nextoomio-data-check: file system errors corrected (exit code 2)"
    rm -f "$RUN_DIR/data_unavailable"
    mkdir -p "$RUN_DIR"
    echo "data_available" > "$RUN_DIR/data_status"
else
    echo "nextoomio-data-check: e2fsck failed with exit code $RC; partition cannot be safely mounted" >&2
    echo "nextoomio-data-check: reporting data_unavailable without auto-reformatting (AC5)" >&2
    mkdir -p "$RUN_DIR"
    echo "data_unavailable" > "$RUN_DIR/data_status"
    touch "$RUN_DIR/data_unavailable"
    # Exit 0 so systemd boot flow continues to multi-user.target rather than halting in emergency mode
    exit 0
fi
