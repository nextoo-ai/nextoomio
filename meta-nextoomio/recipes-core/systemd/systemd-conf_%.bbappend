FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " file://10-nextoomio-journald.conf"

do_install:append() {
    install -d ${D}${sysconfdir}/systemd/journald.conf.d
    install -m 0644 ${WORKDIR}/10-nextoomio-journald.conf ${D}${sysconfdir}/systemd/journald.conf.d/10-nextoomio-journald.conf
}

FILES:${PN}:append = " ${sysconfdir}/systemd/journald.conf.d/10-nextoomio-journald.conf"
