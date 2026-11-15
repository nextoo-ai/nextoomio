#!/bin/sh
# Mount active boot partition read-only at /boot (AC1, ARCHITECTURE.md sections 3.5 & 3.6)
set -eu

BOOT_DIR="${NEXTOOMIO_BOOT_DIR:-/boot}"
CMDLINE_FILE="${NEXTOOMIO_CMDLINE_FILE:-/proc/cmdline}"

if mountpoint -q "$BOOT_DIR"; then
    echo "nextoomio-boot-mount: $BOOT_DIR is already mounted, ensuring read-only"
    mount -o remount,ro "$BOOT_DIR" 2>/dev/null || true
    exit 0
fi

# Determine active slot (a or b) from kernel commandline or root mount source
SLOT="a"
CMDLINE=""
if [ -f "$CMDLINE_FILE" ]; then
    CMDLINE=$(cat "$CMDLINE_FILE" 2>/dev/null || true)
fi

if echo "$CMDLINE" | grep -qE "root_b|mmcblk0p6|boot=boot_b"; then
    SLOT="b"
elif [ -n "${NEXTOOMIO_ROOT_SRC:-}" ]; then
    case "$NEXTOOMIO_ROOT_SRC" in
        *root_b*|*mmcblk0p6*|*p6)
            SLOT="b"
            ;;
        *)
            SLOT="a"
            ;;
    esac
else
    ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null || true)
    case "$ROOT_SRC" in
        *root_b*|*mmcblk0p6*|*p6)
            SLOT="b"
            ;;
        *)
            SLOT="a"
            ;;
    esac
fi

BOOT_DEV="/dev/disk/by-label/boot_${SLOT}"
if [ ! -b "$BOOT_DEV" ]; then
    if [ "$SLOT" = "b" ]; then
        BOOT_DEV="/dev/mmcblk0p3"
    else
        BOOT_DEV="/dev/mmcblk0p2"
    fi
fi

mkdir -p "$BOOT_DIR"

if [ -n "${NEXTOOMIO_MOCK_MOUNT:-}" ]; then
    echo "nextoomio-boot-mount: [mock] mounting $BOOT_DEV on $BOOT_DIR read-only (slot $SLOT)"
    echo "slot=$SLOT label=boot_$SLOT device=$BOOT_DEV target=$BOOT_DIR options=ro,defaults,nofail" > "$BOOT_DIR/.mounted_info"
    exit 0
fi

if [ -b "$BOOT_DEV" ]; then
    echo "nextoomio-boot-mount: mounting active boot slot $SLOT ($BOOT_DEV) on $BOOT_DIR read-only"
    mount -t vfat -o ro,defaults,nofail "$BOOT_DEV" "$BOOT_DIR"
else
    echo "nextoomio-boot-mount: warning: boot device $BOOT_DEV not found" >&2
fi
