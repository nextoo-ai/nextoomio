# nextoomio-image-version.bbclass
# Image-level release metadata generation and rootfs staging (AC1, AC4, P0-F2.3)

IMAGE_PREPROCESS_COMMAND:append = " write_nextoomio_image_version; "

write_nextoomio_image_version() {
    install -d ${IMAGE_ROOTFS}${sysconfdir}
    cat << EOF > ${IMAGE_ROOTFS}${sysconfdir}/nextoomio-release
IMAGE_VERSION="${NEXTOOMIO_IMAGE_VERSION}"
BUILD_ID="${NEXTOOMIO_BUILD_ID}"
SOURCE_COMMIT="${NEXTOOMIO_SOURCE_COMMIT}"
HARDWARE_COMPATIBILITY="${NEXTOOMIO_HARDWARE_COMPAT}"
EOF
}
