#!/bin/sh
# Mount active boot partition read-only at /boot/firmware (AC1, ARCHITECTURE.md sections 3.5 & 3.6)
set -eu

BOOT_DIR="${NEXTOOMIO_BOOT_DIR:-/boot/firmware}"
CMDLINE_FILE="${NEXTOOMIO_CMDLINE_FILE:-/proc/cmdline}"

if mountpoint -q "$BOOT_DIR"; then
    echo "nextoomio-boot-mount: $BOOT_DIR is already mounted, ensuring read-only"
    mount -o remount,ro "$BOOT_DIR" 2>/dev/null || true
    exit 0
fi

# Determine active slot (a or b) from kernel commandline or root mount source
SLOT=""
CMDLINE=""
if [ -f "$CMDLINE_FILE" ]; then
    CMDLINE=$(cat "$CMDLINE_FILE" 2>/dev/null || true)
fi

if echo "$CMDLINE" | grep -qE "root_b|mmcblk0p6|boot=boot_b|4e58544d-06"; then
    SLOT="b"
elif echo "$CMDLINE" | grep -qE "root_a|mmcblk0p5|boot=boot_a|4e58544d-05"; then
    SLOT="a"
fi

if [ -z "$SLOT" ]; then
    ROOT_SRC="${NEXTOOMIO_ROOT_SRC:-}"
    if [ -z "$ROOT_SRC" ]; then
        ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null || true)
    fi
    case "$ROOT_SRC" in
        *root_b*|*mmcblk0p6*|*p6)
            SLOT="b"
            ;;
        *root_a*|*mmcblk0p5*|*p5)
            SLOT="a"
            ;;
        *)
            echo "nextoomio-boot-mount: error: unable to determine active boot slot from commandline or root mount source" >&2
            exit 1
            ;;
    esac
fi

BOOT_DEV_A="${NEXTOOMIO_BOOT_DEV_A:-/dev/disk/by-label/boot_a}"
BOOT_DEV_B="${NEXTOOMIO_BOOT_DEV_B:-/dev/disk/by-label/boot_b}"

if [ "$SLOT" = "b" ]; then
    BOOT_DEV="$BOOT_DEV_B"
    FALLBACK_DEV="/dev/mmcblk0p3"
else
    BOOT_DEV="$BOOT_DEV_A"
    FALLBACK_DEV="/dev/mmcblk0p2"
fi

if [ ! -e "$BOOT_DEV" ] && [ -b "$FALLBACK_DEV" ]; then
    BOOT_DEV="$FALLBACK_DEV"
fi

if [ ! -d "$BOOT_DIR" ]; then
    mkdir -p "$BOOT_DIR" 2>/dev/null || true
fi

if [ -e "$BOOT_DEV" ]; then
    echo "nextoomio-boot-mount: mounting active boot slot $SLOT ($BOOT_DEV) on $BOOT_DIR read-only"
    mount -t vfat -o ro,defaults,nofail "$BOOT_DEV" "$BOOT_DIR"
else
    echo "nextoomio-boot-mount: warning: boot device $BOOT_DEV not found" >&2
fi
