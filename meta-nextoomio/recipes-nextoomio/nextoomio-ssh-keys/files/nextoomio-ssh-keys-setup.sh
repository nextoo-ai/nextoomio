#!/bin/sh
# nextoomio-ssh-keys-setup: Boot-time validator for SSH keys on selector partition
# Implements AD-23, ARCHITECTURE.md section 3.7, Specification section 10.1, P0-F3.1, and P0-F3.2

set -u

SELECT_DIR="/boot/select"
VOLATILE_SSH_DIR="/run/ssh"
AUTH_KEYS_FILE="/run/ssh/authorized_keys"
DIAGNOSTICS_DIR="/run/nextoomio/diagnostics"
DIAG_FILE="/run/nextoomio/diagnostics/ssh.json"
DIAG_TEXT_FILE="/run/nextoomio/diagnostics/ssh.txt"
DATA_SSH_DIR="/data/ssh"

mkdir -p -m 0700 "$VOLATILE_SSH_DIR"
mkdir -p "$DIAGNOSTICS_DIR"

# Helper to close firewall port 22 in nftables using rule handle lookup (CQ-R1, CQ-R2, AC3)
close_firewall_port() {
    if ! command -v nft >/dev/null 2>&1; then
        return 0
    fi
    HANDLES=$(nft -a list chain inet filter input 2>/dev/null | grep 'tcp dport 22 accept' | sed -n 's/.*handle \([0-9]\+\).*/\1/p')
    for h in $HANDLES; do
        nft delete rule inet filter input handle "$h" 2>/dev/null || true
    done
    return 0
}

# Helper to open firewall port 22 in nftables without duplicates on re-run (CQ-R1, AC1)
open_firewall_port() {
    if ! command -v nft >/dev/null 2>&1; then
        return 0
    fi
    # Delete any existing rules first to prevent duplicate rules on re-run
    close_firewall_port
    if ! nft add rule inet filter input tcp dport 22 accept; then
        echo "nextoomio-ssh-keys: Failed to add nftables firewall rule for port 22" >&2
        return 1
    fi
    return 0
}

# Helper to stop sshd asynchronously if currently active (CQ-R1, AC1)
stop_sshd_if_active() {
    if systemctl is-active --quiet sshd.service 2>/dev/null; then
        systemctl stop --no-block sshd.service 2>/dev/null || true
    fi
}

# Helper to write diagnostics in both JSON and human-readable text formats (AC3)
write_diagnostics_disabled() {
    local reason="$1"
    local message="$2"
    cat << EOF > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "${reason}",
  "message": "${message}",
  "fingerprints": []
}
EOF
    cat << EOF > "$DIAG_TEXT_FILE"
SSH access: disabled
Status: disabled
Reason: ${reason}
Message: ${message}
Fingerprints: none
EOF
}

# Locate public key file on selector partition (authorized_keys or authorized_keys.txt)
KEY_FILE=""
if [ -f "${SELECT_DIR}/authorized_keys" ]; then
    KEY_FILE="${SELECT_DIR}/authorized_keys"
elif [ -f "${SELECT_DIR}/authorized_keys.txt" ]; then
    KEY_FILE="${SELECT_DIR}/authorized_keys.txt"
fi

if [ -z "$KEY_FILE" ]; then
    write_diagnostics_disabled "no_key_file" "No authorized_keys or authorized_keys.txt found on selector partition"
    rm -f "$AUTH_KEYS_FILE"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: No key file on selector partition; SSH server disabled."
    exit 0
fi

# AC4: Fail closed if ssh-keygen validator is missing (CQ-R1)
if ! command -v ssh-keygen >/dev/null 2>&1; then
    write_diagnostics_disabled "missing_validator_utility" "Required validation utility 'ssh-keygen' not found; SSH access cannot be safely verified"
    rm -f "$AUTH_KEYS_FILE"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Required utility 'ssh-keygen' is missing; SSH disabled." >&2
    exit 0
fi

# AC4: Validate file size (maximum 16 KiB = 16384 bytes)
FILE_SIZE=$(wc -c < "$KEY_FILE" 2>/dev/null || echo 0)
if [ "$FILE_SIZE" -gt 16384 ]; then
    write_diagnostics_disabled "file_too_large" "Key file exceeds 16 KiB limit (size: ${FILE_SIZE} bytes, maximum: 16384 bytes)"
    rm -f "$AUTH_KEYS_FILE"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Key file ${KEY_FILE} exceeds 16 KiB (${FILE_SIZE} bytes); SSH disabled." >&2
    exit 0
fi

# Validate lines: strip Windows CRLF, reject key options, verify Ed25519, ECDSA, or RSA >= 3072 bits
TMP_KEYS="/run/ssh/keys.tmp"
TMP_SINGLE="/run/ssh/single_key.tmp"
rm -f "$TMP_KEYS" "$TMP_SINGLE"

KEY_COUNT=0
FINGERPRINTS=""
INVALID_REASON=""
LINE_NUM=0

while IFS= read -r RAW_LINE || [ -n "$RAW_LINE" ]; do
    LINE_NUM=$((LINE_NUM + 1))
    # Strip carriage returns (Windows line endings)
    LINE=$(printf '%s' "$RAW_LINE" | tr -d '\r')
    # Strip leading/trailing whitespace
    TRIMMED=$(echo "$LINE" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')

    if [ -z "$TRIMMED" ]; then
        continue
    fi

    # Check key type prefix: must be single Ed25519, ECDSA, or RSA without key options
    KEY_TYPE=$(printf '%s' "$TRIMMED" | awk '{print $1}')
    KEY_DATA=$(printf '%s' "$TRIMMED" | awk '{print $2}')

    case "$KEY_TYPE" in
        ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|ssh-rsa)
            ;;
        *)
            INVALID_REASON="Line $LINE_NUM: contains key options or unsupported key type"
            break
            ;;
    esac

    if [ -z "$KEY_DATA" ]; then
        INVALID_REASON="Line $LINE_NUM: missing public key data"
        break
    fi

    # Write key to temporary file for inspection with ssh-keygen
    printf '%s\n' "$TRIMMED" > "$TMP_SINGLE"
    chmod 0600 "$TMP_SINGLE"

    # Use ssh-keygen -l to extract key details (fails on invalid keys or embedded options)
    OUT=$(ssh-keygen -l -f "$TMP_SINGLE" 2>/dev/null)
    if [ $? -ne 0 ] || [ -z "$OUT" ]; then
        INVALID_REASON="Line $LINE_NUM: invalid public key format"
        break
    fi

    BITS=$(echo "$OUT" | awk '{print $1}')
    FP=$(echo "$OUT" | awk '{print $2}')
    TYPE=$(echo "$OUT" | awk '{print $NF}' | tr -d '()')

    # Fail closed if fingerprint could not be computed
    if [ -z "$FP" ]; then
        INVALID_REASON="Line $LINE_NUM: key fingerprint could not be computed"
        break
    fi

    # Enforce minimum 3072 bits for RSA keys
    if [ "$KEY_TYPE" = "ssh-rsa" ]; then
        if [ "$BITS" -lt 3072 ] 2>/dev/null; then
            INVALID_REASON="Line $LINE_NUM: RSA key too short (${BITS} bits, minimum 3072 bits required)"
            break
        fi
    fi

    case "$TYPE" in
        ED25519|ECDSA|RSA)
            ;;
        *)
            INVALID_REASON="Line $LINE_NUM: unsupported key algorithm ($TYPE)"
            break
            ;;
    esac

    printf '%s\n' "$TRIMMED" >> "$TMP_KEYS"
    KEY_COUNT=$((KEY_COUNT + 1))
    if [ -n "$FINGERPRINTS" ]; then
        FINGERPRINTS="${FINGERPRINTS} ${FP}"
    else
        FINGERPRINTS="${FP}"
    fi
done < "$KEY_FILE"

rm -f "$TMP_SINGLE"

# AC4: If validation failed, SSH stays off and diagnostics record the reason
if [ -n "$INVALID_REASON" ]; then
    write_diagnostics_disabled "invalid_key" "${INVALID_REASON}"
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Invalid key file on selector partition: ${INVALID_REASON}; SSH disabled." >&2
    exit 0
fi

# AC4: At most 16 keys allowed
if [ "$KEY_COUNT" -gt 16 ]; then
    write_diagnostics_disabled "too_many_keys" "Key file contains ${KEY_COUNT} keys; maximum allowed is 16 keys"
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Key file contains ${KEY_COUNT} keys (max 16); SSH disabled." >&2
    exit 0
fi

if [ "$KEY_COUNT" -eq 0 ]; then
    write_diagnostics_disabled "no_keys" "Key file contains no public keys"
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Key file contains no public keys; SSH disabled." >&2
    exit 0
fi

# AC5: Ensure persistent host keys exist on shared data partition (CQ-R1, fail closed)
mkdir -p -m 0700 "$DATA_SSH_DIR"
if [ ! -f "${DATA_SSH_DIR}/ssh_host_ed25519_key" ]; then
    ssh-keygen -t ed25519 -N "" -f "${DATA_SSH_DIR}/ssh_host_ed25519_key" >/dev/null 2>&1 || true
    chmod 0600 "${DATA_SSH_DIR}/ssh_host_ed25519_key" 2>/dev/null || true
    chmod 0644 "${DATA_SSH_DIR}/ssh_host_ed25519_key.pub" 2>/dev/null || true
fi
if [ ! -f "${DATA_SSH_DIR}/ssh_host_rsa_key" ]; then
    ssh-keygen -t rsa -b 3072 -N "" -f "${DATA_SSH_DIR}/ssh_host_rsa_key" >/dev/null 2>&1 || true
    chmod 0600 "${DATA_SSH_DIR}/ssh_host_rsa_key" 2>/dev/null || true
    chmod 0644 "${DATA_SSH_DIR}/ssh_host_rsa_key.pub" 2>/dev/null || true
fi

# Fail closed if host keys are missing or empty
if [ ! -s "${DATA_SSH_DIR}/ssh_host_ed25519_key" ] || [ ! -s "${DATA_SSH_DIR}/ssh_host_rsa_key" ]; then
    write_diagnostics_disabled "missing_host_keys" "Persistent host keys in /data/ssh are missing or could not be generated; SSH disabled"
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Missing persistent host keys in ${DATA_SSH_DIR}; SSH disabled." >&2
    exit 0
fi

# AC1, AC3: Install validated keys exclusively into volatile tmpfs storage
mv "$TMP_KEYS" "$AUTH_KEYS_FILE"
chmod 0600 "$AUTH_KEYS_FILE"
chown root:root "$AUTH_KEYS_FILE" 2>/dev/null || true

# Open firewall port 22 for SSH traffic (fail closed if nft add fails)
if ! open_firewall_port; then
    write_diagnostics_disabled "firewall_rule_failed" "Failed to open firewall port 22 in nftables; SSH disabled"
    rm -f "$AUTH_KEYS_FILE"
    stop_sshd_if_active
    close_firewall_port
    echo "nextoomio-ssh-keys: Failed to open firewall port 22; SSH disabled." >&2
    exit 0
fi

# Record successful diagnostic status in JSON and text format (AC3)
FP_JSON=""
FP_TEXT=""
for fp in $FINGERPRINTS; do
    if [ -n "$FP_JSON" ]; then
        FP_JSON="${FP_JSON}, \"${fp}\""
    else
        FP_JSON="\"${fp}\""
    fi
    if [ -n "$FP_TEXT" ]; then
        FP_TEXT="${FP_TEXT}\n  - ${fp}"
    else
        FP_TEXT="  - ${fp}"
    fi
done

cat << EOF > "$DIAG_FILE"
{
  "enabled": true,
  "status": "enabled",
  "key_count": ${KEY_COUNT},
  "message": "SSH access enabled with valid keys from selector partition",
  "fingerprints": [${FP_JSON}]
}
EOF

cat << EOF > "$DIAG_TEXT_FILE"
SSH access: enabled
Status: enabled
Keys: ${KEY_COUNT}
Message: SSH access enabled with valid keys from selector partition
Authorized key fingerprints:
$(printf '%b' "$FP_TEXT")
EOF

# AC1: Start SSH server (fail closed if start fails)
if ! systemctl start --no-block sshd.service; then
    write_diagnostics_disabled "sshd_start_failed" "Failed to start sshd.service; SSH disabled"
    rm -f "$AUTH_KEYS_FILE"
    close_firewall_port
    echo "nextoomio-ssh-keys: Failed to start sshd.service; SSH disabled." >&2
    exit 1
fi

echo "nextoomio-ssh-keys: Successfully validated ${KEY_COUNT} public key(s); SSH server started."
exit 0
