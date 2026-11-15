#!/bin/sh
# Initialize nextoomio /data directories, permissions, and log ring (AC4, AC5, ARCHITECTURE.md section 3.5)
set -eu

DATA_DIR="${NEXTOOMIO_DATA_DIR:-/data}"
RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"
VAR_LOG_DIR="${NEXTOOMIO_VAR_LOG_DIR:-/var/log}"

# Handle data_unavailable recovery mode (AC5)
if [ -f "$RUN_DIR/data_unavailable" ] || { [ -z "${NEXTOOMIO_TEST_MOUNT:-}" ] && ! mountpoint -q "$DATA_DIR"; }; then
    echo "nextoomio-data-init: persistent data partition is unavailable (AC5 recovery mode)" >&2
    mkdir -p "$RUN_DIR"
    touch "$RUN_DIR/data_unavailable"
    echo "data_unavailable" > "$RUN_DIR/data_status"

    # Mount in-memory volatile tmpfs on /data for recovery state without touching raw disk
    if ! mountpoint -q "$DATA_DIR"; then
        echo "nextoomio-data-init: providing default volatile storage in memory at $DATA_DIR"
        mount -t tmpfs -o size=32M,mode=0755 tmpfs "$DATA_DIR" 2>/dev/null || true
    fi

    mkdir -p "$DATA_DIR/config" "$DATA_DIR/secrets" "$DATA_DIR/music" "$DATA_DIR/update" "$DATA_DIR/log"
    chmod 0755 "$DATA_DIR/config" "$DATA_DIR/music" "$DATA_DIR/update" "$DATA_DIR/log"
    chmod 0700 "$DATA_DIR/secrets"
    exit 0
fi

echo "nextoomio-data-init: initializing persistent directories on $DATA_DIR..."

# 1. Configuration documents: /data/config (mode 0755, root:root)
mkdir -p "$DATA_DIR/config"
chmod 0755 "$DATA_DIR/config"
chown root:root "$DATA_DIR/config" 2>/dev/null || true

# 2. Secrets: /data/secrets (mode 0700, root:root, never world-readable - AC4, section 3.5)
mkdir -p "$DATA_DIR/secrets"
chmod 0700 "$DATA_DIR/secrets"
chown root:root "$DATA_DIR/secrets" 2>/dev/null || true

# 3. User music: /data/music (mode 0755, root:root)
mkdir -p "$DATA_DIR/music"
chmod 0755 "$DATA_DIR/music"
chown root:root "$DATA_DIR/music" 2>/dev/null || true

# 4. Updates: /data/update (mode 0755, root:root)
mkdir -p "$DATA_DIR/update"
chmod 0755 "$DATA_DIR/update"
chown root:root "$DATA_DIR/update" 2>/dev/null || true

# Preallocate fixed-capacity staging file (1 GiB) if not present (ARCHITECTURE.md section 3.5 & 3.6)
if [ ! -f "$DATA_DIR/update/staging.swu" ]; then
    echo "nextoomio-data-init: preallocating fixed-capacity $DATA_DIR/update/staging.swu (1 GiB)..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 1G "$DATA_DIR/update/staging.swu" 2>/dev/null || true
    fi
    if [ ! -f "$DATA_DIR/update/staging.swu" ] && command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of="$DATA_DIR/update/staging.swu" bs=1048576 count=0 seek=1024 2>/dev/null || true
    fi
fi

# Preallocate bounded dual-record update journal (ARCHITECTURE.md section 3.5 & 3.6)
if [ ! -f "$DATA_DIR/update/journal.0" ]; then
    echo "nextoomio-data-init: preallocating $DATA_DIR/update/journal.0..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 64K "$DATA_DIR/update/journal.0" 2>/dev/null || true
    fi
    if [ ! -f "$DATA_DIR/update/journal.0" ] && command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of="$DATA_DIR/update/journal.0" bs=1024 count=0 seek=64 2>/dev/null || true
    fi
fi
if [ ! -f "$DATA_DIR/update/journal.1" ]; then
    echo "nextoomio-data-init: preallocating $DATA_DIR/update/journal.1..."
    if command -v fallocate >/dev/null 2>&1; then
        fallocate -l 64K "$DATA_DIR/update/journal.1" 2>/dev/null || true
    fi
    if [ ! -f "$DATA_DIR/update/journal.1" ] && command -v dd >/dev/null 2>&1; then
        dd if=/dev/zero of="$DATA_DIR/update/journal.1" bs=1024 count=0 seek=64 2>/dev/null || true
    fi
fi

# 5. Persistent log ring: /data/log (mode 0755, root:root - section 3.5)
mkdir -p "$DATA_DIR/log"
chmod 0755 "$DATA_DIR/log"
chown root:root "$DATA_DIR/log" 2>/dev/null || true

mkdir -p "$DATA_DIR/log/journal"
chmod 0755 "$DATA_DIR/log/journal"

# Connect persistent log ring to /var/log/journal
mkdir -p "$VAR_LOG_DIR/journal"
if ! mountpoint -q "$VAR_LOG_DIR/journal" 2>/dev/null; then
    mount --bind "$DATA_DIR/log/journal" "$VAR_LOG_DIR/journal" 2>/dev/null || true
fi

# Request journald to flush in-memory buffers to persistent storage
if command -v killall >/dev/null 2>&1; then
    killall -USR1 systemd-journald 2>/dev/null || true
elif command -v systemctl >/dev/null 2>&1; then
    systemctl kill --signal=SIGUSR1 systemd-journald.service 2>/dev/null || true
fi

# Create markers for completion and resize idempotency
touch "$DATA_DIR/.initialized"
touch "$DATA_DIR/.resized"
rm -f "$RUN_DIR/data_unavailable"
mkdir -p "$RUN_DIR"
echo "data_available" > "$RUN_DIR/data_status"

echo "nextoomio-data-init: data partition initialized successfully"
