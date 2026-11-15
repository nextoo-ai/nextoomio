#!/bin/sh
# Initialize nextoomio /data directories, permissions, and log ring (AC4, AC5, ARCHITECTURE.md section 3.5)
set -eu

# Handle data_unavailable recovery mode (AC5)
if [ -f /run/nextoomio/data_unavailable ] || ! mountpoint -q /data; then
    echo "nextoomio-data-init: persistent data partition is unavailable (AC5 recovery mode)" >&2
    mkdir -p /run/nextoomio
    touch /run/nextoomio/data_unavailable
    echo "data_unavailable" > /run/nextoomio/data_status

    # Mount in-memory volatile tmpfs on /data for recovery state without touching raw disk
    if ! mountpoint -q /data; then
        echo "nextoomio-data-init: providing default volatile storage in memory at /data"
        mount -t tmpfs -o size=32M,mode=0755 tmpfs /data 2>/dev/null || true
    fi

    mkdir -p /data/config /data/secrets /data/music /data/update /data/log
    chmod 0755 /data/config /data/music /data/update /data/log
    chmod 0700 /data/secrets
    exit 0
fi

echo "nextoomio-data-init: initializing persistent directories on /data..."

# 1. Configuration documents: /data/config (mode 0755, root:root)
mkdir -p /data/config
chmod 0755 /data/config
chown root:root /data/config

# 2. Secrets: /data/secrets (mode 0700, root:root, never world-readable - AC4, section 3.5)
mkdir -p /data/secrets
chmod 0700 /data/secrets
chown root:root /data/secrets

# 3. User music: /data/music (mode 0755, root:root)
mkdir -p /data/music
chmod 0755 /data/music
chown root:root /data/music

# 4. Updates: /data/update (mode 0755, root:root)
mkdir -p /data/update
chmod 0755 /data/update
chown root:root /data/update

# Preallocate fixed-capacity staging file (1 GiB) if not present (ARCHITECTURE.md section 3.5 & 3.6)
if [ ! -f /data/update/staging.swu ]; then
    echo "nextoomio-data-init: preallocating fixed-capacity /data/update/staging.swu (1 GiB)..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 1G /data/update/staging.swu 2>/dev/null || true
    elif command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of=/data/update/staging.swu bs=1M count=0 seek=1024 2>/dev/null || true
    fi
fi

# Preallocate bounded dual-record update journal (ARCHITECTURE.md section 3.5 & 3.6)
if [ ! -f /data/update/journal.0 ]; then
    echo "nextoomio-data-init: preallocating /data/update/journal.0..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 64K /data/update/journal.0 2>/dev/null || true
    elif command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of=/data/update/journal.0 bs=1k count=0 seek=64 2>/dev/null || true
    fi
fi
if [ ! -f /data/update/journal.1 ]; then
    echo "nextoomio-data-init: preallocating /data/update/journal.1..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 64K /data/update/journal.1 2>/dev/null || true
    elif command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of=/data/update/journal.1 bs=1k count=0 seek=64 2>/dev/null || true
    fi
fi

# 5. Persistent log ring: /data/log (mode 0755, root:root - section 3.5)
mkdir -p /data/log
chmod 0755 /data/log
chown root:root /data/log

mkdir -p /data/log/journal
chmod 0755 /data/log/journal

# Connect persistent log ring to /var/log/journal
mkdir -p /var/log/journal
if ! mountpoint -q /var/log/journal; then
    mount --bind /data/log/journal /var/log/journal 2>/dev/null || true
fi

# Request journald to flush in-memory buffers to persistent storage
if command -v killall >/dev/null 2>&1; then
    killall -USR1 systemd-journald 2>/dev/null || true
elif command -v systemctl >/dev/null 2>&1; then
    systemctl kill --signal=SIGUSR1 systemd-journald.service 2>/dev/null || true
fi

# Create markers for completion and resize idempotency
touch /data/.initialized
touch /data/.resized
rm -f /run/nextoomio/data_unavailable
mkdir -p /run/nextoomio
echo "data_available" > /run/nextoomio/data_status

echo "nextoomio-data-init: data partition initialized successfully"
