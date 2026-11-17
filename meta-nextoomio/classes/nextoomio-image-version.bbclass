# nextoomio-image-version.bbclass
# Image-level release metadata generation and rootfs staging (AC1, AC4, P0-F2.3)
# Stages /etc/nextoomio-release into the root filesystem.

def nextoomio_derive_source_commit(d):
    import subprocess
    commit = d.getVar('NEXTOOMIO_SOURCE_COMMIT')
    if commit and commit != 'unknown':
        return commit
    commit = os.environ.get('NEXTOOMIO_SOURCE_COMMIT')
    if commit and commit != 'unknown':
        return commit
    try:
        layer = d.getVar('META_NEXTOOMIO_DIR')
        if layer:
            rev = subprocess.check_output(
                ['git', '-C', layer, 'rev-parse', '--show-toplevel'],
                stderr=subprocess.DEVNULL, text=True
            ).strip()
            # A missing layer checkout must not resolve to an enclosing repository.
            if os.path.realpath(os.path.join(rev, 'meta-nextoomio')) == os.path.realpath(layer):
                return subprocess.check_output(
                    ['git', '-C', layer, 'rev-parse', 'HEAD'],
                    stderr=subprocess.DEVNULL, text=True
                ).strip() or 'unknown'
    except (OSError, subprocess.CalledProcessError):
        pass
    return 'unknown'

python () {
    commit = d.getVar('NEXTOOMIO_SOURCE_COMMIT')
    if not commit or commit == 'unknown':
        derived = nextoomio_derive_source_commit(d)
        if derived:
            d.setVar('NEXTOOMIO_SOURCE_COMMIT', derived)
}

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
