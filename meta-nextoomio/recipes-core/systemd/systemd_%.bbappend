# nextoomio systemd configuration (AD-21, AC5)
# Ensure systemd-time-wait-sync is disabled so boot does not wait for NTP clock sync

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://99-timesync.preset \
    file://50-nextoomio-services.preset \
"

do_install:append() {
    install -d ${D}${systemd_unitdir}/system-preset
    install -m 0644 ${UNPACKDIR}/50-nextoomio-services.preset ${D}${systemd_unitdir}/system-preset/50-nextoomio-services.preset
    install -m 0644 ${UNPACKDIR}/99-timesync.preset ${D}${systemd_unitdir}/system-preset/99-timesync.preset
    # Also= and upstream presets can enable these; boot must not wait for network (AD-21).
    install -d ${D}${sysconfdir}/systemd/system
    ln -sf /dev/null ${D}${sysconfdir}/systemd/system/NetworkManager-wait-online.service
    ln -sf /dev/null ${D}${sysconfdir}/systemd/system/systemd-networkd-wait-online.service
}

FILES:${PN} += " \
    ${systemd_unitdir}/system-preset/99-timesync.preset \
    ${systemd_unitdir}/system-preset/50-nextoomio-services.preset \
"

# NetworkManager is the sole network manager; do not enable networkd in postinst.
SYSTEMD_AUTO_ENABLE:${PN}-networkd = "disable"
