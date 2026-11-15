# nextoomio Avahi daemon configuration (AC2, ARCHITECTURE.md section 3.7)
# Publishes device name on .local without exposing system hardware info or user accounts

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://avahi-daemon.conf \
"

do_install:append() {
    install -d ${D}${sysconfdir}/avahi
    install -m 0644 ${WORKDIR}/avahi-daemon.conf ${D}${sysconfdir}/avahi/avahi-daemon.conf
}
