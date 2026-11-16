# nextoomio NetworkManager configuration (AC1, AC4, AD-21)
# Default wired DHCP profile, persistent configuration, and non-blocking boot

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://10-default-wired.nmconnection \
    file://99-nextoomio.preset \
"

do_install:append() {
    # Install default wired Ethernet connection with DHCP (AC1, AC4)
    install -d ${D}${sysconfdir}/NetworkManager/system-connections
    install -m 0600 ${UNPACKDIR}/10-default-wired.nmconnection ${D}${sysconfdir}/NetworkManager/system-connections/10-default-wired.nmconnection

    # Install systemd preset to disable wait-online services (AD-21, AC4)
    install -d ${D}${systemd_unitdir}/system-preset
    install -m 0644 ${UNPACKDIR}/99-nextoomio.preset ${D}${systemd_unitdir}/system-preset/99-nextoomio-nm.preset
}

# Ensure wait-online service is disabled so boot does not block without network cable (AD-21, AC4)
SYSTEMD_AUTO_ENABLE:${PN}-wait-online = "disable"

FILES:${PN} += " \
    ${sysconfdir}/NetworkManager/system-connections/10-default-wired.nmconnection \
    ${systemd_unitdir}/system-preset/99-nextoomio-nm.preset \
"
