SUMMARY = "nextoomio read-only release image (v0.0.1 baseline)"
DESCRIPTION = "Production-style read-only image with PipeWire, MPD, SWUpdate, NetworkManager, Avahi, nftables, nx-sysd, and key-activated OpenSSH per ARCHITECTURE.md section 3.7 and AD-23."
LICENSE = "MIT"

inherit core-image

# Enforce read-only rootfs and remove debug tooling (ARCHITECTURE.md section 3.7)
# OpenSSH is installed in the single image, stopped by default, and activated via key file (AD-23, D-007)
IMAGE_FEATURES += "read-only-rootfs"
IMAGE_FEATURES:remove = "debug-tweaks ssh-server-dropbear package-management"

WKS_FILE = "nextoomio-ab.wks.in"
IMAGE_FSTYPES = "wic.bz2 wic.bmap ext4.gz swu"

# Depend on rpi-autoboot and nextoomio-audio-profile deploy tasks for boot partitions (D-005, D-011)
do_image_wic[depends] += "rpi-autoboot:do_deploy nextoomio-audio-profile:do_deploy"

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
    nx-sysd \
    nextoomio-data \
    e2fsprogs-e2fsck \
    e2fsprogs-resize2fs \
    parted \
    openssh-sshd \
    openssh-keygen \
    nextoomio-ssh-keys \
    nextoomio-audio-profile \
    nextoomio-tone-check \
"
