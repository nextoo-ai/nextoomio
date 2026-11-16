SUMMARY = "Early init hang fault injection package for rollback testing (P0-F4.7 AC1)"
DESCRIPTION = "Provides early init hang script for testing update rollback behavior under P0-F4.7 and ARCHITECTURE.md section 3.6."
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = " \
    file://nextoomio-hang-early.sh \
"

S = "${UNPACKDIR}"

PACKAGES =+ "${PN}-hang"

do_install() {
    # AC1: Early init hang script
    install -d ${D}${sbindir}
    install -m 0755 ${S}/nextoomio-hang-early.sh ${D}${sbindir}/nextoomio-hang-early
}

FILES:${PN}-hang = " \
    ${sbindir}/nextoomio-hang-early \
"
