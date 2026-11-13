SUMMARY = "nextoomio read-only release image (v0.0.1 baseline)"
DESCRIPTION = "Production-style read-only image with PipeWire, MPD, SWUpdate, NetworkManager, Avahi, nftables, and nextoomio daemons. Excludes SSH server and debug tooling per ARCHITECTURE.md section 3.7."
LICENSE = "MIT"

inherit core-image

# Enforce read-only rootfs and remove debug/SSH from release image (ARCHITECTURE.md section 3.7)
IMAGE_FEATURES += "read-only-rootfs"
IMAGE_FEATURES:remove = "debug-tweaks ssh-server-dropbear ssh-server-openssh package-management"

WKS_FILE = "nextoomio-ab.wks.in"
IMAGE_FSTYPES = "wic.bz2 wic.bmap ext4.gz"

# Size budget guard (AR-08: root slot is 3 GiB = 3,145,728 KiB)
IMAGE_ROOTFS_MAXSIZE = "2800000"

IMAGE_INSTALL:append = " \
    packagegroup-core-boot \
    kernel-modules \
    linux-firmware-rpidistro-bcm43455 \
    alsa-utils \
    pipewire \
    pipewire-alsa \
    pipewire-pulse \
    wireplumber \
    mpd \
    swupdate \
    networkmanager \
    avahi-daemon \
    nftables \
"
