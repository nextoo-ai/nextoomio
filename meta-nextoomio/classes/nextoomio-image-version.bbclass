# nextoomio-image-version.bbclass
# Image-level release metadata generation and rootfs staging (AC1, AC4, P0-F2.3)
# Stages /etc/nextoomio-release into the root filesystem. Fails if SOURCE_COMMIT is missing.

def nextoomio_derive_source_commit(d):
    import subprocess
    commit = d.getVar('NEXTOOMIO_SOURCE_COMMIT')
    if commit and commit != 'unknown':
        return commit
    commit = os.environ.get('NEXTOOMIO_SOURCE_COMMIT')
    if commit and commit != 'unknown':
        return commit
    for search_dir in [d.getVar('LAYERDIR_nextoomio'), d.getVar('COREBASE'), d.getVar('TOPDIR'), '.']:
        if not search_dir:
            continue
        try:
            rev = subprocess.check_output(
                ['git', '-C', search_dir, 'rev-parse', 'HEAD'],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()
            if rev:
                return rev
        except Exception:
            continue
    return ""

python () {
    commit = d.getVar('NEXTOOMIO_SOURCE_COMMIT')
    if not commit or commit == 'unknown':
        derived = nextoomio_derive_source_commit(d)
        if derived:
            d.setVar('NEXTOOMIO_SOURCE_COMMIT', derived)
}

IMAGE_PREPROCESS_COMMAND:append = " write_nextoomio_image_version; "

write_nextoomio_image_version() {
    if [ -z "${NEXTOOMIO_SOURCE_COMMIT}" ] || [ "${NEXTOOMIO_SOURCE_COMMIT}" = "unknown" ]; then
        bbfatal "NEXTOOMIO_SOURCE_COMMIT is empty or unknown. Image release file cannot be staged."
    fi
    install -d ${IMAGE_ROOTFS}${sysconfdir}
    cat << EOF > ${IMAGE_ROOTFS}${sysconfdir}/nextoomio-release
IMAGE_VERSION="${NEXTOOMIO_IMAGE_VERSION}"
BUILD_ID="${NEXTOOMIO_BUILD_ID}"
SOURCE_COMMIT="${NEXTOOMIO_SOURCE_COMMIT}"
HARDWARE_COMPATIBILITY="${NEXTOOMIO_HARDWARE_COMPAT}"
EOF
}
