FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://swupdate.cfg \
    file://hwrevision \
"

do_install:append() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/swupdate.cfg ${D}${sysconfdir}/swupdate.cfg
    install -m 0644 ${WORKDIR}/hwrevision ${D}${sysconfdir}/hwrevision
}

FILES:${PN} += " \
    ${sysconfdir}/swupdate.cfg \
    ${sysconfdir}/hwrevision \
"
