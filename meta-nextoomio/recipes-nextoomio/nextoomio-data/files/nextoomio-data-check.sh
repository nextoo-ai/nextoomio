#!/bin/sh
# Check and repair /data partition before mount (AC3, AC5, ARCHITECTURE.md section 3.5)
set -eu

DATA_DEV="/dev/disk/by-label/data"
if [ -L "$DATA_DEV" ]; then
    DATA_DEV=$(readlink -f "$DATA_DEV")
elif [ ! -b "$DATA_DEV" ]; then
    DATA_DEV="/dev/mmcblk0p7"
fi

if [ ! -b "$DATA_DEV" ]; then
    echo "nextoomio-data-check: data partition block device not found" >&2
    mkdir -p /run/nextoomio
    echo "data_unavailable" > /run/nextoomio/data_status
    touch /run/nextoomio/data_unavailable
    exit 0
fi

echo "nextoomio-data-check: checking and repairing data partition ($DATA_DEV)..."

# e2fsck options:
# -p: preen mode, automatically repairs harmless errors
# -f: force check even if clean flag is set
set +e
e2fsck -p -f "$DATA_DEV"
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
    rm -f /run/nextoomio/data_unavailable
    mkdir -p /run/nextoomio
    echo "data_available" > /run/nextoomio/data_status
elif [ "$RC" -eq 2 ]; then
    echo "nextoomio-data-check: file system errors corrected (exit code 2)"
    rm -f /run/nextoomio/data_unavailable
    mkdir -p /run/nextoomio
    echo "data_available" > /run/nextoomio/data_status
else
    echo "nextoomio-data-check: e2fsck failed with exit code $RC; partition cannot be safely mounted" >&2
    echo "nextoomio-data-check: reporting data_unavailable without auto-reformatting (AC5)" >&2
    mkdir -p /run/nextoomio
    echo "data_unavailable" > /run/nextoomio/data_status
    touch /run/nextoomio/data_unavailable
    # Exit 0 so systemd boot flow continues to multi-user.target rather than halting in emergency mode
    exit 0
fi
