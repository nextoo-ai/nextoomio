# nextoomio os-release configuration (AC1, AC2, P0-F2.3)
# Appends version, commit, build identifier, and hardware compatibility to /etc/os-release

OS_RELEASE_FIELDS:append = " BUILD_ID IMAGE_VERSION NEXTOOMIO_BUILD_ID NEXTOOMIO_SOURCE_COMMIT NEXTOOMIO_IMAGE_VERSION NEXTOOMIO_HARDWARE_COMPAT"

BUILD_ID ?= "${NEXTOOMIO_BUILD_ID}"
IMAGE_VERSION ?= "${NEXTOOMIO_IMAGE_VERSION}"
