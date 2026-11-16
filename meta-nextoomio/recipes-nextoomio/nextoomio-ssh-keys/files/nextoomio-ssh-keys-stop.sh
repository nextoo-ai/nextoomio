#!/bin/sh
# nextoomio-ssh-keys-stop: Service stop and volatile cleanup handler
# Stops sshd, purges volatile authorized_keys, removes nftables port 22 rule, and updates status

set -u

systemctl stop sshd.service 2>/dev/null || true
rm -f /run/ssh/authorized_keys

if command -v nft >/dev/null 2>&1; then
    HANDLES=$(nft -a list chain inet filter input 2>/dev/null | grep 'tcp dport 22 accept' | sed -n 's/.*handle \([0-9]\+\).*/\1/p')
    for h in $HANDLES; do
        nft delete rule inet filter input handle "$h" 2>/dev/null || true
    done
fi

mkdir -p /run/nextoomio/diagnostics
cat << 'EOF' > /run/nextoomio/diagnostics/ssh.json
{
  "enabled": false,
  "status": "disabled",
  "reason": "service_stopped",
  "message": "SSH service stopped and volatile authorized_keys cleared",
  "fingerprints": []
}
EOF
