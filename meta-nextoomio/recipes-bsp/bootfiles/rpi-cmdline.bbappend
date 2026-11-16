# nextoomio A/B slot A root partition specification (D-004, ARCHITECTURE.md section 3.6)
# Reference root partition by MBR partition UUID (PARTUUID=<disk-signature>-05)
# rather than fixed device node (/dev/mmcblk0p2).
# Unconditional assignment (=) overrides upstream meta-raspberrypi default (?=).
# Disk signature 0x4e58544d is defined in meta-nextoomio/files/wic/nextoomio-ab.wks.in.
# Slot B dynamic root selection is handled in P0-F4 (#4) / P0-F4.1 (#17).
CMDLINE_ROOT_PARTITION = "PARTUUID=4e58544d-05"
