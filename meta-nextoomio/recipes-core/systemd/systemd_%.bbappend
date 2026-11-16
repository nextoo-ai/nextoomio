# nextoomio systemd configuration (AD-21, AC5)
# Ensure systemd-time-wait-sync is disabled so boot does not wait for NTP clock sync

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://99-timesync.preset \
"

do_install:append() {
    install -d ${D}${systemd_unitdir}/system-preset
    install -m 0644 ${UNPACKDIR}/99-timesync.preset ${D}${systemd_unitdir}/system-preset/99-timesync.preset
}

FILES:${PN} += " \
    ${systemd_unitdir}/system-preset/99-timesync.preset \
"
