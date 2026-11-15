# nextoomio nftables configuration (ARCHITECTURE.md section 3.7, AC3)
# Default-deny inbound firewall baseline with modular extension support

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://nftables.conf \
    file://00-default.nft \
"

do_install:append() {
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${WORKDIR}/nftables.conf ${D}${sysconfdir}/nftables.conf
    install -m 0644 ${WORKDIR}/00-default.nft ${D}${sysconfdir}/nftables.d/00-default.nft
}

FILES:${PN} += " \
    ${sysconfdir}/nftables.d \
    ${sysconfdir}/nftables.d/00-default.nft \
"
