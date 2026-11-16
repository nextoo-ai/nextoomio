#!/bin/sh
# nextoomio-hang-early: Deliberately hang kernel early init before systemd starts
# Used for testing update rollback behavior under P0-F4.7 AC1 and ARCHITECTURE.md section 3.6.
# Note: Automatic reset requires continuous pre-systemd watchdog arming (AR-01, spike S1 / #17).
set -u

echo "[nextoomio-test] DELIBERATE EARLY INIT HANG (P0-F4.7 AC1)" > /dev/console 2>/dev/null || true
echo "[nextoomio-test] Kernel booted into early init; systemd is NOT started." > /dev/console 2>/dev/null || true
echo "[nextoomio-test] Looping indefinitely before PID 1 systemd." > /dev/console 2>/dev/null || true

# Block indefinitely before systemd starts
while true; do
    sleep 3600 2>/dev/null || true
done
