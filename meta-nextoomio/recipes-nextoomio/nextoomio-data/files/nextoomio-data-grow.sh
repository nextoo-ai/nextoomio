#!/bin/sh
# Expand data partition to remainder of card on first boot (AC2, D-006, ARCHITECTURE.md section 3.6)
set -eu

# Service ordering and safety rationale (ARCHITECTURE.md sections 3.5 & 3.6):
# nextoomio-data-grow.service is ordered Before=nextoomio-data-check.service and Before=data.mount.
# On first boot of a freshly flashed image, the ext4 filesystem on the data partition
# was created clean by wic and has never been mounted. resize2fs on an unmounted filesystem
# only requires a prior check if the filesystem was mounted since its last check; for a virgin
# image filesystem, it expands the block groups directly and cleanly.
# Running grow before nextoomio-data-check ensures that nextoomio-data-check (e2fsck -p -f)
# inspects and validates the final, expanded filesystem before data.mount mounts it for
# multi-user.target and nextoomio-data-init.service.

RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"
SYS_BLOCK_DIR="${NEXTOOMIO_SYS_BLOCK_DIR:-/sys/class/block}"

# Helper to read ext4 filesystem size in bytes using dumpe2fs or tune2fs
get_fs_bytes() {
    dev="$1"
    blocks=""
    block_size=""
    if command -v dumpe2fs >/dev/null 2>&1; then
        out=$(dumpe2fs -h "$dev" 2>/dev/null || true)
        blocks=$(echo "$out" | awk -F: '/^Block count:/ { gsub(/[ \t]/, "", $2); print $2 }')
        block_size=$(echo "$out" | awk -F: '/^Block size:/ { gsub(/[ \t]/, "", $2); print $2 }')
    fi
    if [ -z "$blocks" ] || [ -z "$block_size" ]; then
        if command -v tune2fs >/dev/null 2>&1; then
            out=$(tune2fs -l "$dev" 2>/dev/null || true)
            blocks=$(echo "$out" | awk -F: '/^Block count:/ { gsub(/[ \t]/, "", $2); print $2 }')
            block_size=$(echo "$out" | awk -F: '/^Block size:/ { gsub(/[ \t]/, "", $2); print $2 }')
        fi
    fi
    if [ -n "$blocks" ] && [ -n "$block_size" ] && [ "$blocks" -gt 0 ] 2>/dev/null && [ "$block_size" -gt 0 ] 2>/dev/null; then
        echo $((blocks * block_size))
    else
        echo 0
    fi
}

# Helper to read partition capacity in bytes using blockdev, sysfs, or file size
get_part_bytes() {
    dev="$1"
    if command -v blockdev >/dev/null 2>&1; then
        bytes=$(blockdev --getsize64 "$dev" 2>/dev/null || true)
        if [ -n "$bytes" ] && [ "$bytes" -gt 0 ] 2>/dev/null; then
            echo "$bytes"
            return 0
        fi
    fi
    part_name=$(basename "$dev")
    if [ -f "$SYS_BLOCK_DIR/$part_name/size" ]; then
        sectors=$(cat "$SYS_BLOCK_DIR/$part_name/size" 2>/dev/null || echo 0)
        if [ "$sectors" -gt 0 ] 2>/dev/null; then
            echo $((sectors * 512))
            return 0
        fi
    fi
    if [ -f "$dev" ]; then
        wc -c < "$dev" 2>/dev/null | tr -d ' ' || echo 0
        return 0
    fi
    echo 0
}

# Determine block device for data partition
DATA_DEV="${NEXTOOMIO_DATA_DEV:-/dev/disk/by-label/data}"
if [ -L "$DATA_DEV" ]; then
    DATA_DEV=$(readlink -f "$DATA_DEV")
elif [ ! -b "$DATA_DEV" ] && [ -z "${NEXTOOMIO_DATA_DEV:-}" ]; then
    DATA_DEV="/dev/mmcblk0p7"
fi

if [ ! -b "$DATA_DEV" ] && [ ! -f "$DATA_DEV" ]; then
    echo "nextoomio-data-grow: data partition $DATA_DEV not found" >&2
    exit 0
fi

# Extract disk device (e.g. /dev/mmcblk0 from /dev/mmcblk0p7) and partition number (7)
DISK=$(echo "$DATA_DEV" | sed -E 's/p?[0-9]+$//')
PARTNUM=$(echo "$DATA_DEV" | grep -oE '[0-9]+$')

if [ -z "$DISK" ] || [ -z "$PARTNUM" ]; then
    echo "nextoomio-data-grow: could not parse disk ($DISK) or partition ($PARTNUM)" >&2
    exit 0
fi

DISK_NAME=$(basename "$DISK")
PART_NAME=$(basename "$DATA_DEV")

# Check disk and partition geometry via sysfs (idempotency: AC2)
PARTITION_NEEDS_GROW=0
if [ -f "$SYS_BLOCK_DIR/$DISK_NAME/size" ] && [ -f "$SYS_BLOCK_DIR/$PART_NAME/start" ] && [ -f "$SYS_BLOCK_DIR/$PART_NAME/size" ]; then
    DISK_SECTORS=$(cat "$SYS_BLOCK_DIR/$DISK_NAME/size")
    PART_START=$(cat "$SYS_BLOCK_DIR/$PART_NAME/start")
    PART_SECTORS=$(cat "$SYS_BLOCK_DIR/$PART_NAME/size")
    PART_END=$((PART_START + PART_SECTORS))
    FREE_SECTORS=$((DISK_SECTORS - PART_END))

    # If remaining unpartitioned space is more than 4 MiB (8192 sectors of 512 bytes), partition needs expansion
    if [ "$FREE_SECTORS" -gt 8192 ]; then
        PARTITION_NEEDS_GROW=1
        echo "nextoomio-data-grow: card is larger than image: $FREE_SECTORS free sectors found on $DISK. Resizing partition..."
    fi
fi

# Check whether ext4 filesystem needs expansion (AC2, CQ-R1)
# Decide from filesystem block count vs partition capacity
FS_NEEDS_GROW=0
PART_BYTES=$(get_part_bytes "$DATA_DEV")
FS_BYTES=$(get_fs_bytes "$DATA_DEV")

if [ "$PARTITION_NEEDS_GROW" -eq 1 ]; then
    FS_NEEDS_GROW=1
elif [ "$PART_BYTES" -gt 0 ] && [ "$FS_BYTES" -gt 0 ]; then
    # If unallocated space in partition exceeds 4 MiB (4194304 bytes), filesystem needs expansion
    FS_DIFF=$((PART_BYTES - FS_BYTES))
    if [ "$FS_DIFF" -gt 4194304 ]; then
        FS_NEEDS_GROW=1
        echo "nextoomio-data-grow: partition $PARTNUM is already extended ($PART_BYTES bytes), but ext4 filesystem ($FS_BYTES bytes) does not fill partition ($FS_DIFF bytes unallocated). Resizing filesystem..."
    fi
fi

# If neither partition nor filesystem needs growth, expansion is complete (idempotent: AC2)
if [ "$PARTITION_NEEDS_GROW" -eq 0 ] && [ "$FS_NEEDS_GROW" -eq 0 ]; then
    echo "nextoomio-data-grow: partition $PARTNUM and ext4 filesystem already extend to end of disk; skipping expansion"
    mkdir -p "$RUN_DIR"
    touch "$RUN_DIR/data-resized"
    exit 0
fi

# Resize MBR partition table if partition needs growth
if [ "$PARTITION_NEEDS_GROW" -eq 1 ]; then
    # In MBR layout with logical partitions (5, 6, 7), extended partition 4 must also be resized
    EXTENDED_PART=4
    if command -v parted >/dev/null 2>&1; then
        echo "nextoomio-data-grow: resizing extended partition $EXTENDED_PART and partition $PARTNUM with parted..."
        if [ -d "$SYS_BLOCK_DIR/${DISK_NAME}p${EXTENDED_PART}" ] || [ -d "$SYS_BLOCK_DIR/${DISK_NAME}${EXTENDED_PART}" ]; then
            if ! parted -s "$DISK" resizepart "$EXTENDED_PART" 100%; then
                echo "nextoomio-data-grow: ERROR failed to resize extended partition $EXTENDED_PART" >&2
                exit 1
            fi
        fi
        if ! parted -s "$DISK" resizepart "$PARTNUM" 100%; then
            echo "nextoomio-data-grow: ERROR failed to resize partition $PARTNUM" >&2
            exit 1
        fi
    elif command -v sfdisk >/dev/null 2>&1; then
        echo "nextoomio-data-grow: resizing partition $PARTNUM with sfdisk..."
        if ! echo ", +" | sfdisk -N "$PARTNUM" --no-reread "$DISK"; then
            echo "nextoomio-data-grow: ERROR failed to resize partition $PARTNUM with sfdisk" >&2
            exit 1
        fi
    fi

    # Inform kernel of updated partition table
    if command -v partx >/dev/null 2>&1; then
        if ! partx -u "$DISK"; then
            echo "nextoomio-data-grow: WARNING partx -u failed on $DISK" >&2
        fi
    fi
fi

# Resize ext4 filesystem to fill partition
if [ "$FS_NEEDS_GROW" -eq 1 ]; then
    if command -v resize2fs >/dev/null 2>&1; then
        echo "nextoomio-data-grow: expanding ext4 filesystem on $DATA_DEV..."
        if ! resize2fs "$DATA_DEV"; then
            echo "nextoomio-data-grow: ERROR resize2fs failed on $DATA_DEV" >&2
            exit 1
        fi
    fi
fi

mkdir -p "$RUN_DIR"
touch "$RUN_DIR/data-resized"
echo "nextoomio-data-grow: data partition expansion completed successfully"
