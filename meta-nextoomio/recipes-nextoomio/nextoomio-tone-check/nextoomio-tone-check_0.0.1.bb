SUMMARY = "nextoomio remote audio test tone and stream verification tool"
DESCRIPTION = "Test tone generator and playback stream verification tool for remote audio path checks (ARCHITECTURE.md section 4.1, SPECIFICATION.md section 2.1, P0-F6.2, D-012)"
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI = " \
    file://generate-tone.py \
    file://nextoomio-tone-check.sh \
"

S = "${UNPACKDIR}"

DEPENDS = " \
    python3-native \
"

do_compile() {
    # Generate 2.0s 48kHz stereo test tone strictly bounded to <= 30% volume (AC2, D-012)
    python3 ${S}/generate-tone.py ${B}/test-tone-48k-440hz.wav
    python3 ${S}/generate-tone.py --check ${B}/test-tone-48k-440hz.wav
}

do_install() {
    # Install command-line tone check utility
    install -d ${D}${bindir}
    install -m 0755 ${S}/nextoomio-tone-check.sh ${D}${bindir}/nextoomio-tone-check

    # Install safe-level test tone
    install -d ${D}${datadir}/nextoomio/tone-check
    install -m 0644 ${B}/test-tone-48k-440hz.wav ${D}${datadir}/nextoomio/tone-check/test-tone-48k-440hz.wav
}

RDEPENDS:${PN} = " \
    alsa-utils \
"

FILES:${PN} += " \
    ${bindir}/nextoomio-tone-check \
    ${datadir}/nextoomio/tone-check/test-tone-48k-440hz.wav \
"
