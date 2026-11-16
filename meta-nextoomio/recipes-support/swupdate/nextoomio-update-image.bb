SUMMARY = "nextoomio software update (.swu) image"
DESCRIPTION = "Compound update package containing boot-slot and root-slot artifacts per ARCHITECTURE.md section 3.6 and UPD-03/UPD-04."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit swupdate

SRC_URI = "file://sw-description"

# Dependency on base image
IMAGE_DEPENDS = "nextoomio-image"

# Images and artifacts bundled into the SWU container
SWUPDATE_IMAGES = "nextoomio-image"
SWUPDATE_IMAGES_FSTYPES[nextoomio-image] = ".rootfs.ext4.gz"

# Enforce unsigned updates and require no signing keys per UPD-03, AD-13, and AC2
SWUPDATE_SIGNING = ""

# Size limits per ARCHITECTURE.md section 3.6 and AC3:
# Boot slot: 256 MiB = 268435456 bytes
# Root slot: 3072 MiB = 3221225472 bytes
# Staging reservation: 1024 MiB (1 GiB) = 1073741824 bytes
NEXTOOMIO_BOOT_SLOT_MAXSIZE ?= "268435456"
NEXTOOMIO_ROOT_SLOT_MAXSIZE ?= "3221225472"
NEXTOOMIO_STAGING_RESERVATION_MAXSIZE ?= "1073741824"
