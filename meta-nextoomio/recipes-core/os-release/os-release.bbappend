# nextoomio os-release configuration (AC1, AC2, P0-F2.3)
# Appends version, commit, build identifier, and hardware compatibility to /etc/os-release

OS_RELEASE_FIELDS:append = " BUILD_ID IMAGE_VERSION NEXTOOMIO_BUILD_ID NEXTOOMIO_SOURCE_COMMIT NEXTOOMIO_IMAGE_VERSION NEXTOOMIO_HARDWARE_COMPAT"

BUILD_ID ?= "${NEXTOOMIO_BUILD_ID}"
IMAGE_VERSION ?= "${NEXTOOMIO_IMAGE_VERSION}"

NEXTOOMIO_BUILD_ID ?= "${DATETIME}"
NEXTOOMIO_IMAGE_VERSION ?= "${DISTRO_VERSION}"
NEXTOOMIO_SOURCE_COMMIT ?= "unknown"
NEXTOOMIO_HARDWARE_COMPAT ?= "raspberrypi4-64"

do_install:append() {
    # Provide convenience release file in /etc/nextoomio-release (AC1, P0-F2.3)
    install -d ${D}${sysconfdir}
    cat << EOF > ${D}${sysconfdir}/nextoomio-release
IMAGE_VERSION="${IMAGE_VERSION}"
BUILD_ID="${BUILD_ID}"
SOURCE_COMMIT="${NEXTOOMIO_SOURCE_COMMIT}"
HARDWARE_COMPATIBILITY="${NEXTOOMIO_HARDWARE_COMPAT}"
EOF
}

FILES:${PN} += "${sysconfdir}/nextoomio-release"
