#!/bin/sh
# Initialize nextoomio /data directories, permissions, and log ring (AC4, AC5, ARCHITECTURE.md section 3.5)
set -eu

DATA_DIR="${NEXTOOMIO_DATA_DIR:-/data}"
RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"
VAR_LOG_DIR="${NEXTOOMIO_VAR_LOG_DIR:-/var/log}"

# Helper for preallocating fixed-capacity files with real disk blocks (AC4, ARCHITECTURE.md section 3.5 & 3.6)
preallocate_file() {
    target_file="$1"
    size_bytes="$2"

    if [ -f "$target_file" ]; then
        current_size=$(wc -c < "$target_file" 2>/dev/null | tr -d ' ' || echo 0)
        if [ "$current_size" -ne "$size_bytes" ]; then
            echo "nextoomio-data-init: $target_file size mismatch ($current_size != $size_bytes), re-allocating..."
            rm -f "$target_file"
        fi
    fi

    if [ ! -f "$target_file" ]; then
        echo "nextoomio-data-init: preallocating $target_file ($size_bytes bytes)..."
        if command -v fallocate >/dev/null 2>&1; then
            if ! fallocate -l "$size_bytes" "$target_file"; then
                echo "nextoomio-data-init: ERROR fallocate failed for $target_file" >&2
                rm -f "$target_file"
                return 1
            fi
        elif command -v dd >/dev/null 2>&1; then
            bs=1048576
            count=$((size_bytes / bs))
            if [ "$count" -eq 0 ]; then
                bs=1024
                count=$((size_bytes / bs))
            fi
            if ! dd if=/dev/zero of="$target_file" bs="$bs" count="$count" status=none; then
                echo "nextoomio-data-init: ERROR dd allocation failed for $target_file" >&2
                rm -f "$target_file"
                return 1
            fi
        else
            echo "nextoomio-data-init: ERROR neither fallocate nor dd found" >&2
            return 1
        fi
    fi
}

# Handle data_unavailable recovery mode (AC5)
if [ -f "$RUN_DIR/data_unavailable" ] || ! mountpoint -q "$DATA_DIR"; then
    echo "nextoomio-data-init: persistent data partition is unavailable (AC5 recovery mode)" >&2
    mkdir -p "$RUN_DIR"
    touch "$RUN_DIR/data_unavailable"
    echo "data_unavailable" > "$RUN_DIR/data_status"

    # Mount in-memory volatile tmpfs on /data for recovery state without touching raw disk
    if ! mountpoint -q "$DATA_DIR"; then
        echo "nextoomio-data-init: providing default volatile storage in memory at $DATA_DIR"
        if ! mount -t tmpfs -o size=32M,mode=0755 tmpfs "$DATA_DIR"; then
            echo "nextoomio-data-init: ERROR failed to mount recovery tmpfs at $DATA_DIR" >&2
            exit 1
        fi
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

# Preallocate fixed-capacity staging file (1 GiB = 1073741824 bytes) (ARCHITECTURE.md section 3.5 & 3.6)
preallocate_file "$DATA_DIR/update/staging.swu" 1073741824

# Preallocate bounded dual-record update journal (64 KiB = 65536 bytes each) (ARCHITECTURE.md section 3.5 & 3.6)
preallocate_file "$DATA_DIR/update/journal.0" 65536
preallocate_file "$DATA_DIR/update/journal.1" 65536

# 5. Persistent log ring: /data/log (mode 0755, root:root - section 3.5)
mkdir -p "$DATA_DIR/log"
chmod 0755 "$DATA_DIR/log"
chown root:root "$DATA_DIR/log" 2>/dev/null || true

mkdir -p "$DATA_DIR/log/journal"
chmod 0755 "$DATA_DIR/log/journal"

# Connect persistent log ring to /var/log/journal
mkdir -p "$VAR_LOG_DIR/journal"
if ! mountpoint -q "$VAR_LOG_DIR/journal"; then
    echo "nextoomio-data-init: bind-mounting $DATA_DIR/log/journal to $VAR_LOG_DIR/journal..."
    if ! mount --bind "$DATA_DIR/log/journal" "$VAR_LOG_DIR/journal"; then
        echo "nextoomio-data-init: ERROR failed to bind-mount persistent log ring to $VAR_LOG_DIR/journal" >&2
        exit 1
    fi
fi

# Request journald to flush in-memory buffers to persistent storage
if command -v systemctl >/dev/null 2>&1; then
    systemctl kill --signal=SIGUSR1 systemd-journald.service 2>/dev/null || true
elif command -v killall >/dev/null 2>&1; then
    killall -USR1 systemd-journald 2>/dev/null || true
fi

# Create marker for initialization completion
touch "$DATA_DIR/.initialized"
rm -f "$RUN_DIR/data_unavailable"
mkdir -p "$RUN_DIR"
echo "data_available" > "$RUN_DIR/data_status"

echo "nextoomio-data-init: data partition initialized successfully"
