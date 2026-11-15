#!/bin/sh
# Expand data partition to remainder of card on first boot (AC2, D-006, ARCHITECTURE.md section 3.6)
set -eu

RUN_DIR="${NEXTOOMIO_RUN_DIR:-/run/nextoomio}"
SYS_BLOCK_DIR="${NEXTOOMIO_SYS_BLOCK_DIR:-/sys/class/block}"

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
if [ -f "$SYS_BLOCK_DIR/$DISK_NAME/size" ] && [ -f "$SYS_BLOCK_DIR/$PART_NAME/start" ] && [ -f "$SYS_BLOCK_DIR/$PART_NAME/size" ]; then
    DISK_SECTORS=$(cat "$SYS_BLOCK_DIR/$DISK_NAME/size")
    PART_START=$(cat "$SYS_BLOCK_DIR/$PART_NAME/start")
    PART_SECTORS=$(cat "$SYS_BLOCK_DIR/$PART_NAME/size")
    PART_END=$((PART_START + PART_SECTORS))
    FREE_SECTORS=$((DISK_SECTORS - PART_END))

    # If remaining unpartitioned space is 4 MiB (8192 sectors of 512 bytes) or less, partition already fills card
    if [ "$FREE_SECTORS" -le 8192 ]; then
        echo "nextoomio-data-grow: partition $PARTNUM already extends to end of disk ($FREE_SECTORS sectors free); skipping expansion"
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

# Resize ext4 filesystem to fill partition
if command -v resize2fs >/dev/null 2>&1; then
    echo "nextoomio-data-grow: expanding ext4 filesystem on $DATA_DEV..."
    if ! resize2fs "$DATA_DEV"; then
        echo "nextoomio-data-grow: ERROR resize2fs failed on $DATA_DEV" >&2
        exit 1
    fi
fi

mkdir -p "$RUN_DIR"
touch "$RUN_DIR/data-resized"
echo "nextoomio-data-grow: data partition expansion completed successfully"
