#!/bin/sh
# nextoomio-broken-health: Deliberately fail the local health check
# Implements P0-F4.7 AC2 (boots but fails local health check) and ARCHITECTURE.md section 3.6
set -u

DIAGNOSTICS_DIR="/run/nextoomio/diagnostics"
mkdir -p "$DIAGNOSTICS_DIR"

echo "[nextoomio-test] DELIBERATE LOCAL HEALTH CHECK FAILURE (P0-F4.7 AC2)" > /dev/console 2>/dev/null || true

cat << 'EOF' > /run/nextoomio/diagnostics/health.json
{
  "status": "failed",
  "reason": "deliberate_test_health_failure",
  "error": "simulated local health check failure (P0-F4.7 AC2)",
  "healthy": false
}
EOF

cat << 'EOF' > /run/nextoomio/diagnostics/health.txt
Health status: failed
Reason: deliberate_test_health_failure
Message: simulated local health check failure (P0-F4.7 AC2)
EOF

# Force health check failure status
exit 1
