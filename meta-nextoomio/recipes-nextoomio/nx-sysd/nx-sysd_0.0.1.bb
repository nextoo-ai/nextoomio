SUMMARY = "nextoomio privileged operations helper daemon"
DESCRIPTION = "Minimal privileged operations helper (slot writes, hardware profile include, NAS mounts, nftables, reboot)"
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit cargo cargo-update-recipe-crates systemd

FILESEXTRAPATHS:prepend := "${THISDIR}/files:${THISDIR}/../../..:"

SRC_URI = " \
    file://core \
    file://nextoomio-device-id.service \
    file://nextoomio-update-init.service \
"

require ${BPN}-crates.inc

S = "${UNPACKDIR}/core"
CARGO_SRC_DIR = ""

CARGO_BUILD_FLAGS:append = " -p nx-sysd"

SYSTEMD_SERVICE:${PN} = " \
    nextoomio-device-id.service \
    nextoomio-update-init.service \
"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install:append() {
    install -d ${D}${bindir}
    ln -sf nx-sysd ${D}${bindir}/nx-identity

    if ${@bb.utils.contains('DISTRO_FEATURES', 'systemd', 'true', 'false', d)}; then
        install -d ${D}${systemd_system_unitdir}
        install -m 0644 "${UNPACKDIR}/nextoomio-device-id.service" "${D}${systemd_system_unitdir}/"
        install -m 0644 "${UNPACKDIR}/nextoomio-update-init.service" "${D}${systemd_system_unitdir}/"
    fi
}

FILES:${PN} += " \
    ${bindir}/nx-identity \
    ${systemd_system_unitdir}/nextoomio-device-id.service \
    ${systemd_system_unitdir}/nextoomio-update-init.service \
"
