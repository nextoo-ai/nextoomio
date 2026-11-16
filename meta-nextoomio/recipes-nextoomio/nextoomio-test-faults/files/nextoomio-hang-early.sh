#!/bin/sh
# nextoomio-hang-early: Deliberately hang kernel early init before systemd starts
# Implements P0-F4.7 AC1 (kernel/init hang before systemd) and ARCHITECTURE.md section 3.6
set -u

echo "[nextoomio-test] DELIBERATE EARLY INIT HANG (P0-F4.7 AC1)" > /dev/console 2>/dev/null || true
echo "[nextoomio-test] Kernel booted into early init; systemd is NOT started." > /dev/console 2>/dev/null || true
echo "[nextoomio-test] Awaiting hardware watchdog reset to trigger A/B return..." > /dev/console 2>/dev/null || true

# Block indefinitely; watchdog is not serviced by systemd
while true; do
    sleep 3600 2>/dev/null || true
done
