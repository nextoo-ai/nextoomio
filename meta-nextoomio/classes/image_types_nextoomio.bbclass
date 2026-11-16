# nextoomio image types class: generates .swu compound update container
# per ARCHITECTURE.md section 3.6, UPD-03, UPD-04, and AD-13.

inherit image_types

# Share the slot A root reference with the deployed rpi-cmdline recipe.
require ${META_NEXTOOMIO_DIR}/recipes-bsp/bootfiles/nextoomio-cmdline.inc
NEXTOOMIO_ROOT_B = "PARTUUID=4e58544d-06"

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
NEXTOOMIO_HW_REVISION ?= "1.0"
NEXTOOMIO_HW_COMPATIBILITY ?= "${NEXTOOMIO_HW_REVISION}"

# Path to sw-description template file (AC1, AC4)
SWUPDATE_DESCRIPTION_TEMPLATE ?= "${META_NEXTOOMIO_DIR}/recipes-support/swupdate/files/sw-description.in"
do_image_swu[file-checksums] += "${SWUPDATE_DESCRIPTION_TEMPLATE}:True"

do_image_swu[depends] += " \
    dosfstools-native:do_populate_sysroot \
    mtools-native:do_populate_sysroot \
    virtual/kernel:do_deploy \
    rpi-config:do_deploy \
    rpi-cmdline:do_deploy \
    rpi-bootfiles:do_deploy \
    nextoomio-audio-profile:do_deploy \
"

IMAGE_CMD:swu () {
    SWU_BUILD_DIR="${WORKDIR}/swu-build"
    rm -rf "${SWU_BUILD_DIR}"
    mkdir -p "${SWU_BUILD_DIR}"

    BOOT_ARTIFACT="${SWU_BUILD_DIR}/nextoomio-boot.vfat"
    ROOTFS_ARTIFACT="${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.ext4.gz"

    if [ ! -f "${ROOTFS_ARTIFACT}" ]; then
        ROOTFS_ARTIFACT="${IMGDEPLOYDIR}/${IMAGE_NAME}.ext4.gz"
    fi

    if [ ! -f "${ROOTFS_ARTIFACT}" ]; then
        bbfatal "Root slot artifact not found at ${ROOTFS_ARTIFACT}"
    fi

    # 1. Create boot slot VFAT filesystem (256 MiB = 262144 blocks of 1KB)
    # 262144 1 KiB blocks = 268435456 bytes, fitting the 256 MiB boot_a/boot_b slot in ARCHITECTURE.md section 3.6
    mkfs.vfat -F 32 -n "boot" -C "${BOOT_ARTIFACT}" 262144

    # Use the same boot file list and destinations as wic's boot_a partition.
    BOOT_FILE_ENTRIES="${IMAGE_BOOT_FILES_label-boot_a}"
    for entry in ${BOOT_FILE_ENTRIES}; do
        src="${entry%%;*}"
        dst=""
        case "${entry}" in
            *";"*) dst="${entry#*;}" ;;
        esac
        matched=0
        for f in ${DEPLOY_DIR_IMAGE}/${src}; do
            [ -e "${f}" ] || continue
            matched=1
            if [ -n "${dst}" ]; then
                dstdir=$(dirname "${dst}")
                if [ "${dstdir}" != "." ]; then
                    mdir -i "${BOOT_ARTIFACT}" "::/${dstdir}" >/dev/null 2>&1 || mmd -i "${BOOT_ARTIFACT}" "::/${dstdir}"
                fi
                mcopy -o -i "${BOOT_ARTIFACT}" "${f}" "::/${dst}"
            else
                mcopy -o -i "${BOOT_ARTIFACT}" -s "${f}" ::/
            fi
        done
        if [ "${matched}" = "0" ]; then
            bbfatal "Boot file entry ${entry} matches nothing in ${DEPLOY_DIR_IMAGE}"
        fi
    done

    for req in ${SDIMG_KERNELIMAGE} bcm2711-rpi-4-b.dtb cmdline.txt config.txt nextoomio-hardware.txt start4.elf fixup4.dat; do
        if ! mdir -i "${BOOT_ARTIFACT}" "::/${req}" >/dev/null 2>&1; then
            bbfatal "Required boot file ${req} is not in the boot payload"
        fi
    done
    if ! mdir -i "${BOOT_ARTIFACT}" ::/overlays 2>/dev/null | grep -qi "dtbo"; then
        bbfatal "The boot payload has no device tree overlays"
    fi

    # Reject a changed slot A reference before deriving the slot-specific payloads.
    if [ "${CMDLINE_ROOT_PARTITION}" != "${@d.getVar('NEXTOOMIO_ROOT_B').replace('-06', '-05')}" ]; then
        bbfatal "Slot A root reference does not match the A/B partition layout"
    fi

    # Create independent slot images and verify the command line inside each FAT.
    BOOT_PKG_A="nextoomio-boot-a.vfat.gz"
    BOOT_PKG_B="nextoomio-boot-b.vfat.gz"
    for slot in a b; do
        SLOT_ARTIFACT="${SWU_BUILD_DIR}/boot-$slot.vfat"
        cp "${BOOT_ARTIFACT}" "${SLOT_ARTIFACT}"
        if [ "$slot" = a ]; then
            EXPECTED_ROOT="${CMDLINE_ROOT_PARTITION}"
            OTHER_ROOT="${NEXTOOMIO_ROOT_B}"
        else
            EXPECTED_ROOT="${NEXTOOMIO_ROOT_B}"
            OTHER_ROOT="${CMDLINE_ROOT_PARTITION}"
            sed "s|root=${CMDLINE_ROOT_PARTITION}|root=${NEXTOOMIO_ROOT_B}|g" \
                "${DEPLOY_DIR_IMAGE}/${BOOTFILES_DIR_NAME}/cmdline.txt" > "${SWU_BUILD_DIR}/cmdline-b.txt"
            mdel -i "${SLOT_ARTIFACT}" ::/cmdline.txt
            mcopy -i "${SLOT_ARTIFACT}" "${SWU_BUILD_DIR}/cmdline-b.txt" ::/cmdline.txt
        fi
        if ! mcopy -i "${SLOT_ARTIFACT}" ::/cmdline.txt "${SWU_BUILD_DIR}/readback-$slot.txt"; then
            bbfatal "Cannot read cmdline.txt from boot slot $slot"
        fi
        if ! grep -Eq "(^|[[:space:]])root=$EXPECTED_ROOT([[:space:]]|$)" "${SWU_BUILD_DIR}/readback-$slot.txt" || \
           grep -Fq "$OTHER_ROOT" "${SWU_BUILD_DIR}/readback-$slot.txt"; then
            bbfatal "Invalid root reference in boot slot $slot cmdline.txt"
        fi
        BOOT_EXPANDED_SIZE=$(stat -c %s "${SLOT_ARTIFACT}")
        if [ "${BOOT_EXPANDED_SIZE}" -gt "${NEXTOOMIO_BOOT_SLOT_MAXSIZE}" ]; then
            bbfatal "Boot slot $slot artifact exceeds slot limit ${NEXTOOMIO_BOOT_SLOT_MAXSIZE} bytes"
        fi
        gzip -9 -c "${SLOT_ARTIFACT}" > "${SWU_BUILD_DIR}/nextoomio-boot-$slot.vfat.gz"
    done

    # 3. Enforce the root slot size limit.
    if [ -f "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.ext4" ]; then
        ROOT_EXPANDED_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.ext4")
    elif [ -f "${IMGDEPLOYDIR}/${IMAGE_NAME}.ext4" ]; then
        ROOT_EXPANDED_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_NAME}.ext4")
    else
        ROOT_EXPANDED_SIZE=$(gzip -dc "${ROOTFS_ARTIFACT}" | wc -c)
    fi

    if [ "${ROOT_EXPANDED_SIZE}" -gt "${NEXTOOMIO_ROOT_SLOT_MAXSIZE}" ]; then
        bbfatal "Root slot artifact expanded size ${ROOT_EXPANDED_SIZE} exceeds slot limit ${NEXTOOMIO_ROOT_SLOT_MAXSIZE} bytes (AC3)"
    fi

    # 4. Checksums & metadata preparation (AC1)
    BOOT_A_SHA256=$(sha256sum "${SWU_BUILD_DIR}/${BOOT_PKG_A}" | awk '{print $1}')
    BOOT_B_SHA256=$(sha256sum "${SWU_BUILD_DIR}/${BOOT_PKG_B}" | awk '{print $1}')
    BOOT_A_SIZE=$(stat -c %s "${SWU_BUILD_DIR}/${BOOT_PKG_A}")
    BOOT_B_SIZE=$(stat -c %s "${SWU_BUILD_DIR}/${BOOT_PKG_B}")
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
        -e "s/@BOOT_PKG_A@/${BOOT_PKG_A}/g" \
        -e "s/@BOOT_PKG_B@/${BOOT_PKG_B}/g" \
        -e "s/@BOOT_A_SHA256@/${BOOT_A_SHA256}/g" \
        -e "s/@BOOT_B_SHA256@/${BOOT_B_SHA256}/g" \
        -e "s/@BOOT_A_SIZE@/${BOOT_A_SIZE}/g" \
        -e "s/@BOOT_B_SIZE@/${BOOT_B_SIZE}/g" \
        -e "s/@ROOT_PKG_FILE@/${ROOT_PKG_FILE}/g" \
        -e "s/@ROOT_SHA256@/${ROOT_SHA256}/g" \
        "${SWUPDATE_DESCRIPTION_TEMPLATE}" > "${SWU_BUILD_DIR}/sw-description"

    # 6. Package into .swu container via cpio (AC1, AC2)
    # Order is mandatory: sw-description must be the first file in the archive
    (cd "${SWU_BUILD_DIR}" && for f in sw-description "${BOOT_PKG_A}" "${BOOT_PKG_B}" "${ROOT_PKG_FILE}"; do
        echo "$f"
    done | cpio -ov -H crc > "${IMGDEPLOYDIR}/${IMAGE_NAME}.swu")

    ln -sf "${IMAGE_NAME}.swu" "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.swu"

    # 7. Check update package file size against staging reservation limit (AC3)
    SWU_SIZE=$(stat -c %s "${IMGDEPLOYDIR}/${IMAGE_NAME}.swu")
    if [ "${SWU_SIZE}" -gt "${NEXTOOMIO_STAGING_RESERVATION_MAXSIZE}" ]; then
        bbfatal "Update file size ${SWU_SIZE} exceeds staging reservation ${NEXTOOMIO_STAGING_RESERVATION_MAXSIZE} bytes (AC3)"
    fi
}
