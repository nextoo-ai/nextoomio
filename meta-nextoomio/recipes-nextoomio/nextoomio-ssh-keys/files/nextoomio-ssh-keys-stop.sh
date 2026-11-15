#!/bin/sh
# nextoomio-ssh-keys-stop: Service stop and volatile cleanup handler
# Stops sshd, purges volatile authorized_keys, removes nftables port 22 rule, and updates status

systemctl stop sshd.service 2>/dev/null || true
rm -f /run/ssh/authorized_keys

if command -v nft >/dev/null 2>&1; then
    nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
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
