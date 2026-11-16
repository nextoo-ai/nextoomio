SUMMARY = "Fault injection packages for nextoomio update rollback verification (P0-F4.7)"
DESCRIPTION = "Provides test-only fault injection mechanisms for verifying automatic return from broken images per P0-F4.7 and ARCHITECTURE.md section 3.6."
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit systemd

SRC_URI = " \
    file://nextoomio-hang-early.sh \
    file://nextoomio-broken-health.sh \
    file://nextoomio-broken-health.service \
    file://99-block-network-test.nft \
"

S = "${WORKDIR}/sources"
UNPACKDIR = "${S}"

PACKAGES =+ "${PN}-hang ${PN}-health ${PN}-network"

SYSTEMD_PACKAGES = "${PN}-health"
SYSTEMD_SERVICE:${PN}-health = "nextoomio-broken-health.service"
SYSTEMD_AUTO_ENABLE:${PN}-health = "enable"

do_install() {
    # AC1: Early init hang script
    install -d ${D}${sbindir}
    install -m 0755 ${S}/nextoomio-hang-early.sh ${D}${sbindir}/nextoomio-hang-early

    # AC2: Broken health check script, failure marker, and service
    install -d ${D}${libexecdir}/nextoomio
    install -m 0755 ${S}/nextoomio-broken-health.sh ${D}${libexecdir}/nextoomio/nextoomio-broken-health
    install -d ${D}${sysconfdir}/nextoomio
    touch ${D}${sysconfdir}/nextoomio/force-health-failure

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/nextoomio-broken-health.service ${D}${systemd_system_unitdir}/nextoomio-broken-health.service

    # AC3: Firewall block rule for network/ssh isolation
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${S}/99-block-network-test.nft ${D}${sysconfdir}/nftables.d/99-block-network-test.nft
}

FILES:${PN}-hang = " \
    ${sbindir}/nextoomio-hang-early \
"

FILES:${PN}-health = " \
    ${libexecdir}/nextoomio/nextoomio-broken-health \
    ${sysconfdir}/nextoomio/force-health-failure \
    ${systemd_system_unitdir}/nextoomio-broken-health.service \
"

FILES:${PN}-network = " \
    ${sysconfdir}/nftables.d/99-block-network-test.nft \
"
