#!/bin/sh
# nextoomio-ssh-keys-setup: Boot-time validator for SSH keys on selector partition
# Implements AD-23, ARCHITECTURE.md section 3.7, Specification section 10.1, and P0-F3.1

set -u

SELECT_DIR="/boot/select"
VOLATILE_SSH_DIR="/run/ssh"
AUTH_KEYS_FILE="/run/ssh/authorized_keys"
DIAGNOSTICS_DIR="/run/nextoomio/diagnostics"
DIAG_FILE="/run/nextoomio/diagnostics/ssh.json"
DATA_SSH_DIR="/data/ssh"

mkdir -p -m 0700 "$VOLATILE_SSH_DIR"
mkdir -p "$DIAGNOSTICS_DIR"

# Locate public key file on selector partition (authorized_keys or authorized_keys.txt)
KEY_FILE=""
if [ -f "${SELECT_DIR}/authorized_keys" ]; then
    KEY_FILE="${SELECT_DIR}/authorized_keys"
elif [ -f "${SELECT_DIR}/authorized_keys.txt" ]; then
    KEY_FILE="${SELECT_DIR}/authorized_keys.txt"
fi

if [ -z "$KEY_FILE" ]; then
    cat << 'EOF' > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "no_key_file",
  "message": "No authorized_keys or authorized_keys.txt found on selector partition",
  "fingerprints": []
}
EOF
    rm -f "$AUTH_KEYS_FILE"
    systemctl stop sshd.service 2>/dev/null || true
    if command -v nft >/dev/null 2>&1; then
        nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
    fi
    echo "nextoomio-ssh-keys: No key file on selector partition; SSH server disabled."
    exit 0
fi

# AC4: Validate file size (maximum 16 KiB = 16384 bytes)
FILE_SIZE=$(wc -c < "$KEY_FILE" 2>/dev/null || echo 0)
if [ "$FILE_SIZE" -gt 16384 ]; then
    cat << EOF > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "file_too_large",
  "message": "Key file exceeds 16 KiB limit (size: ${FILE_SIZE} bytes, maximum: 16384 bytes)",
  "fingerprints": []
}
EOF
    rm -f "$AUTH_KEYS_FILE"
    systemctl stop sshd.service 2>/dev/null || true
    if command -v nft >/dev/null 2>&1; then
        nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
    fi
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
            INVALID_REASON="Line $LINE_NUM: contains key options or unsupported key type ('$KEY_TYPE')"
            break
            ;;
    esac

    if [ -z "$KEY_DATA" ]; then
        INVALID_REASON="Line $LINE_NUM: missing public key data"
        break
    fi

    # Validate key using ssh-keygen if available; verify key structure and bits
    printf '%s\n' "$TRIMMED" > "$TMP_SINGLE"
    if command -v ssh-keygen >/dev/null 2>&1; then
        KEYGEN_OUT=$(ssh-keygen -l -f "$TMP_SINGLE" 2>/dev/null)
        if [ $? -ne 0 ] || [ -z "$KEYGEN_OUT" ]; then
            INVALID_REASON="Line $LINE_NUM: invalid public key format"
            break
        fi

        NUM_PARSED=$(echo "$KEYGEN_OUT" | wc -l)
        if [ "$NUM_PARSED" -ne 1 ]; then
            INVALID_REASON="Line $LINE_NUM: contains multiple keys on a single line"
            break
        fi

        BITS=$(echo "$KEYGEN_OUT" | awk '{print $1}')
        FP=$(echo "$KEYGEN_OUT" | awk '{print $2}')
        TYPE=$(echo "$KEYGEN_OUT" | awk '{print $NF}' | tr -d '()')

        if [ "$TYPE" = "RSA" ]; then
            if [ "$BITS" -lt 3072 ]; then
                INVALID_REASON="Line $LINE_NUM: RSA key has only $BITS bits; minimum required is 3072 bits"
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
    else
        FP="sha256-uncomputed"
    fi

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
    cat << EOF > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "invalid_key",
  "message": "${INVALID_REASON}",
  "fingerprints": []
}
EOF
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    systemctl stop sshd.service 2>/dev/null || true
    if command -v nft >/dev/null 2>&1; then
        nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
    fi
    echo "nextoomio-ssh-keys: Invalid key file on selector partition: ${INVALID_REASON}; SSH disabled." >&2
    exit 0
fi

# AC4: At most 16 keys allowed
if [ "$KEY_COUNT" -gt 16 ]; then
    cat << EOF > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "too_many_keys",
  "message": "Key file contains ${KEY_COUNT} keys; maximum allowed is 16 keys",
  "fingerprints": []
}
EOF
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    systemctl stop sshd.service 2>/dev/null || true
    if command -v nft >/dev/null 2>&1; then
        nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
    fi
    echo "nextoomio-ssh-keys: Key file contains ${KEY_COUNT} keys (max 16); SSH disabled." >&2
    exit 0
fi

if [ "$KEY_COUNT" -eq 0 ]; then
    cat << 'EOF' > "$DIAG_FILE"
{
  "enabled": false,
  "status": "disabled",
  "reason": "no_keys",
  "message": "Key file contains no public keys",
  "fingerprints": []
}
EOF
    rm -f "$AUTH_KEYS_FILE" "$TMP_KEYS"
    systemctl stop sshd.service 2>/dev/null || true
    if command -v nft >/dev/null 2>&1; then
        nft delete rule inet filter input tcp dport 22 accept 2>/dev/null || true
    fi
    echo "nextoomio-ssh-keys: Key file contains no public keys; SSH disabled." >&2
    exit 0
fi

# AC1, AC3: Install validated keys exclusively into volatile tmpfs storage
mv "$TMP_KEYS" "$AUTH_KEYS_FILE"
chmod 0600 "$AUTH_KEYS_FILE"
chown root:root "$AUTH_KEYS_FILE" 2>/dev/null || true

# AC5: Ensure persistent host keys exist on shared data partition
mkdir -p -m 0700 "$DATA_SSH_DIR"
if [ ! -f "${DATA_SSH_DIR}/ssh_host_ed25519_key" ]; then
    if command -v ssh-keygen >/dev/null 2>&1; then
        ssh-keygen -t ed25519 -N "" -f "${DATA_SSH_DIR}/ssh_host_ed25519_key"
        chmod 0600 "${DATA_SSH_DIR}/ssh_host_ed25519_key"
        chmod 0644 "${DATA_SSH_DIR}/ssh_host_ed25519_key.pub"
    fi
fi
if [ ! -f "${DATA_SSH_DIR}/ssh_host_rsa_key" ]; then
    if command -v ssh-keygen >/dev/null 2>&1; then
        ssh-keygen -t rsa -b 3072 -N "" -f "${DATA_SSH_DIR}/ssh_host_rsa_key"
        chmod 0600 "${DATA_SSH_DIR}/ssh_host_rsa_key"
        chmod 0644 "${DATA_SSH_DIR}/ssh_host_rsa_key.pub"
    fi
fi

# Open firewall port 22 for SSH traffic
if command -v nft >/dev/null 2>&1; then
    nft add rule inet filter input tcp dport 22 accept 2>/dev/null || true
fi

# Record successful diagnostic status
FP_JSON=""
for fp in $FINGERPRINTS; do
    if [ -n "$FP_JSON" ]; then
        FP_JSON="${FP_JSON}, \"${fp}\""
    else
        FP_JSON="\"${fp}\""
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

# AC1: Start SSH server
systemctl start sshd.service
echo "nextoomio-ssh-keys: Successfully validated ${KEY_COUNT} public key(s); SSH server started."
exit 0
