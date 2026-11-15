#!/bin/sh
# Expand data partition to remainder of card on first boot (AC2, D-006, ARCHITECTURE.md section 3.6)
set -eu

DATA_DIR="${NEXTOOMIO_DATA_DIR:-/data}"
RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"

# If already resized marker exists on data filesystem or in run, do nothing
if [ -f "$DATA_DIR/.resized" ] || [ -f "$RUN_DIR/data-resized" ]; then
    echo "nextoomio-data-grow: resize marker found, skipping expansion"
    exit 0
fi

# Determine block device for data partition
DATA_DEV="${NEXTOOMIO_DATA_DEV:-/dev/disk/by-label/data}"
if [ -L "$DATA_DEV" ]; then
    DATA_DEV=$(readlink -f "$DATA_DEV")
elif [ ! -b "$DATA_DEV" ] && [ -z "${NEXTOOMIO_DATA_DEV:-}" ]; then
    DATA_DEV="/dev/mmcblk0p7"
fi

if [ ! -b "$DATA_DEV" ]; then
    echo "nextoomio-data-grow: data partition $DATA_DEV not found" >&2
    exit 0
fi

# Extract disk device (e.g. /dev/mmcblk0 from /dev/mmcblk0p7) and partition number (7)
DISK=$(echo "$DATA_DEV" | sed -E 's/p?[0-9]+$//')
PARTNUM=$(echo "$DATA_DEV" | grep -oE '[0-9]+$')

if [ ! -b "$DISK" ] || [ -z "$PARTNUM" ]; then
    echo "nextoomio-data-grow: could not parse disk ($DISK) or partition ($PARTNUM)" >&2
    exit 0
fi

DISK_NAME=$(basename "$DISK")
PART_NAME=$(basename "$DATA_DEV")

# Check disk and partition geometry via sysfs
if [ -f "/sys/class/block/$DISK_NAME/size" ] && [ -f "/sys/class/block/$PART_NAME/start" ] && [ -f "/sys/class/block/$PART_NAME/size" ]; then
    DISK_SECTORS=$(cat "/sys/class/block/$DISK_NAME/size")
    PART_START=$(cat "/sys/class/block/$PART_NAME/start")
    PART_SECTORS=$(cat "/sys/class/block/$PART_NAME/size")
    PART_END=$((PART_START + PART_SECTORS))
    FREE_SECTORS=$((DISK_SECTORS - PART_END))

    # If remaining unpartitioned space is 4 MiB (8192 sectors of 512 bytes) or less, partition already fills card
    if [ "$FREE_SECTORS" -le 8192 ]; then
        echo "nextoomio-data-grow: partition $PARTNUM already extends to end of disk ($FREE_SECTORS sectors free)"
        mkdir -p "$RUN_DIR"
        touch "$RUN_DIR/data-resized"
        exit 0
    fi

    echo "nextoomio-data-grow: card is larger than image: $FREE_SECTORS free sectors found on $DISK. Resizing..."
fi

# Resize MBR partition table
# In MBR layout with logical partitions (5, 6, 7), extended partition 4 must also be resized
EXTENDED_PART=4
if command -v parted >/dev/null 2>&1; then
    echo "nextoomio-data-grow: resizing extended partition $EXTENDED_PART and partition $PARTNUM with parted..."
    if [ -d "/sys/class/block/${DISK_NAME}p${EXTENDED_PART}" ] || [ -d "/sys/class/block/${DISK_NAME}${EXTENDED_PART}" ]; then
        parted -s "$DISK" resizepart "$EXTENDED_PART" 100% 2>/dev/null || true
    fi
    parted -s "$DISK" resizepart "$PARTNUM" 100% 2>/dev/null || true
elif command -v sfdisk >/dev/null 2>&1; then
    echo "nextoomio-data-grow: resizing partition $PARTNUM with sfdisk..."
    echo ", +" | sfdisk -N "$PARTNUM" --no-reread "$DISK" 2>/dev/null || true
fi

# Inform kernel of updated partition table
if command -v partx >/dev/null 2>&1; then
    partx -u "$DISK" 2>/dev/null || true
fi

# Resize ext4 filesystem to fill partition
if command -v resize2fs >/dev/null 2>&1; then
    echo "nextoomio-data-grow: expanding ext4 filesystem on $DATA_DEV..."
    resize2fs "$DATA_DEV" 2>/dev/null || true
fi

mkdir -p "$RUN_DIR"
touch "$RUN_DIR/data-resized"
echo "nextoomio-data-grow: data partition expansion completed"
