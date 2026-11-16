# nextoomio image types class: generates .swu compound update container
# per ARCHITECTURE.md section 3.6, UPD-03, UPD-04, and AD-13.

inherit image_types

IMAGE_TYPES += "swu"
IMAGE_TYPEDEP:swu = "ext4.gz"

# Size limits per ARCHITECTURE.md section 3.6 and AC3:
# Boot slot: 256 MiB = 268435456 bytes
# Root slot: 3072 MiB = 3221225472 bytes
# Staging reservation: 1024 MiB (1 GiB) = 1073741824 bytes
NEXTOOMIO_BOOT_SLOT_MAXSIZE ?= "268435456"
NEXTOOMIO_ROOT_SLOT_MAXSIZE ?= "3221225472"
NEXTOOMIO_STAGING_RESERVATION_MAXSIZE ?= "1073741824"
NEXTOOMIO_CONFIG_SCHEMA_RANGE ?= "1..1"
NEXTOOMIO_HW_COMPATIBILITY ?= "${MACHINE}"

# Path to sw-description template file (AC1, AC4)
SWUPDATE_DESCRIPTION_TEMPLATE ?= "${META_NEXTOOMIO_DIR}/recipes-support/swupdate/files/sw-description.in"

# Require no signing keys or signatures per UPD-03 and AC2
SWUPDATE_SIGNING = ""

do_image_swu[depends] += " \
    dosfstools-native:do_populate_sysroot \
    mtools-native:do_populate_sysroot \
    virtual/kernel:do_deploy \
    rpi-config:do_deploy \
    rpi-cmdline:do_deploy \
    nextoomio-audio-profile:do_deploy \
"

IMAGE_CMD:swu () {
    SWU_BUILD_DIR="${WORKDIR}/swu-build"
    rm -rf "${SWU_BUILD_DIR}"
    mkdir -p "${SWU_BUILD_DIR}"

    BOOT_ARTIFACT="${SWU_BUILD_DIR}/nextoomio-boot.vfat"
    ROOTFS_ARTIFACT="${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.rootfs.ext4.gz"

    if [ ! -f "${ROOTFS_ARTIFACT}" ]; then
        ROOTFS_ARTIFACT="${IMGDEPLOYDIR}/${IMAGE_NAME}.rootfs.ext4.gz"
    fi

    if [ ! -f "${ROOTFS_ARTIFACT}" ]; then
        bbfatal "Root slot artifact not found at ${ROOTFS_ARTIFACT}"
    fi

    # 1. Create boot slot VFAT filesystem (256 MiB = 262144 blocks of 1KB)
    # 262144 1 KiB blocks = 268435456 bytes, fitting the 256 MiB boot_a/boot_b slot in ARCHITECTURE.md section 3.6
    mkfs.vfat -F 32 -n "boot" -C "${BOOT_ARTIFACT}" 262144

    # 2. Populate boot files into boot VFAT filesystem
    # Required boot files must cause fatal build failure if absent (AC1, CQ-R2)
    for req in Image bcm2711-rpi-4-b.dtb cmdline.txt config.txt; do
        if [ ! -f "${DEPLOY_DIR_IMAGE}/${req}" ]; then
            bbfatal "Required boot file ${req} missing from ${DEPLOY_DIR_IMAGE}"
        fi
        mcopy -i "${BOOT_ARTIFACT}" "${DEPLOY_DIR_IMAGE}/${req}" ::/
    done

    if [ -f "${DEPLOY_DIR_IMAGE}/nextoomio-hardware.txt" ]; then
        mcopy -i "${BOOT_ARTIFACT}" "${DEPLOY_DIR_IMAGE}/nextoomio-hardware.txt" ::/
    fi

    if [ -d "${DEPLOY_DIR_IMAGE}/bcm2835-bootfiles" ]; then
        mcopy -i "${BOOT_ARTIFACT}" -s "${DEPLOY_DIR_IMAGE}/bcm2835-bootfiles/"* ::/
    fi

    if [ -d "${DEPLOY_DIR_IMAGE}/overlays" ]; then
        mmd -i "${BOOT_ARTIFACT}" ::/overlays
        mcopy -i "${BOOT_ARTIFACT}" -s "${DEPLOY_DIR_IMAGE}/overlays/"* ::/overlays/
    fi

    gzip -9 -c "${BOOT_ARTIFACT}" > "${SWU_BUILD_DIR}/nextoomio-boot.vfat.gz"
    BOOT_PKG_FILE="nextoomio-boot.vfat.gz"
    BOOT_PKG_PATH="${SWU_BUILD_DIR}/${BOOT_PKG_FILE}"

    # 3. Size enforcement checks (AC3, ARCHITECTURE.md section 3.6 step 2)
    BOOT_EXPANDED_SIZE=$(stat -c %s "${BOOT_ARTIFACT}")
    if [ "${BOOT_EXPANDED_SIZE}" -gt "${NEXTOOMIO_BOOT_SLOT_MAXSIZE}" ]; then
        bbfatal "Boot slot artifact expanded size ${BOOT_EXPANDED_SIZE} exceeds slot limit ${NEXTOOMIO_BOOT_SLOT_MAXSIZE} bytes (AC3)"
    fi

    if [ -f "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.rootfs.ext4" ]; then
        ROOT_EXPANDED_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.rootfs.ext4")
    elif [ -f "${IMGDEPLOYDIR}/${IMAGE_NAME}.rootfs.ext4" ]; then
        ROOT_EXPANDED_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_NAME}.rootfs.ext4")
    else
        ROOT_EXPANDED_SIZE=$(gzip -l "${ROOTFS_ARTIFACT}" | awk 'NR==2 {print $2}')
    fi

    if [ "${ROOT_EXPANDED_SIZE}" -gt "${NEXTOOMIO_ROOT_SLOT_MAXSIZE}" ]; then
        bbfatal "Root slot artifact expanded size ${ROOT_EXPANDED_SIZE} exceeds slot limit ${NEXTOOMIO_ROOT_SLOT_MAXSIZE} bytes (AC3)"
    fi

    # 4. Checksums & metadata preparation (AC1)
    BOOT_SHA256=$(sha256sum "${BOOT_PKG_PATH}" | awk '{print $1}')
    ROOT_SHA256=$(sha256sum "${ROOTFS_ARTIFACT}" | awk '{print $1}')
    ROOT_PKG_FILE=$(basename "${ROOTFS_ARTIFACT}")

    cp -f "${ROOTFS_ARTIFACT}" "${SWU_BUILD_DIR}/${ROOT_PKG_FILE}"

    # 5. Generate sw-description from template via sed (AC1, AC2, AC4)
    # Using template filled with sed avoids heredoc column-0 lines that cause BitBake parse errors
    if [ ! -f "${SWUPDATE_DESCRIPTION_TEMPLATE}" ]; then
        bbfatal "sw-description template not found at ${SWUPDATE_DESCRIPTION_TEMPLATE}"
    fi

    sed -e "s/@DISTRO_VERSION@/${DISTRO_VERSION}/g" \
        -e "s/@HW_COMPATIBILITY@/${NEXTOOMIO_HW_COMPATIBILITY}/g" \
        -e "s/@CONFIG_SCHEMA_RANGE@/${NEXTOOMIO_CONFIG_SCHEMA_RANGE}/g" \
        -e "s/@BOOT_PKG_FILE@/${BOOT_PKG_FILE}/g" \
        -e "s/@BOOT_SHA256@/${BOOT_SHA256}/g" \
        -e "s/@ROOT_PKG_FILE@/${ROOT_PKG_FILE}/g" \
        -e "s/@ROOT_SHA256@/${ROOT_SHA256}/g" \
        "${SWUPDATE_DESCRIPTION_TEMPLATE}" > "${SWU_BUILD_DIR}/sw-description"

    # 6. Package into .swu container via cpio (AC1, AC2)
    # Order is mandatory: sw-description must be the first file in the archive
    (cd "${SWU_BUILD_DIR}" && for f in sw-description "${BOOT_PKG_FILE}" "${ROOT_PKG_FILE}"; do
        echo "$f"
    done | cpio -ov -H crc > "${IMGDEPLOYDIR}/${IMAGE_NAME}.swu")

    ln -sf "${IMAGE_NAME}.swu" "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.swu"

    # 7. Check update package file size against staging reservation limit (AC3)
    SWU_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_NAME}.swu")
    if [ "${SWU_SIZE}" -gt "${NEXTOOMIO_STAGING_RESERVATION_MAXSIZE}" ]; then
        bbfatal "Update file size ${SWU_SIZE} exceeds staging reservation ${NEXTOOMIO_STAGING_RESERVATION_MAXSIZE} bytes (AC3)"
    fi
}
