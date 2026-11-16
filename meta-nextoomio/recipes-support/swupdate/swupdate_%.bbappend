FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://swupdate.cfg \
    file://hwrevision \
"

do_install:append() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${UNPACKDIR}/swupdate.cfg ${D}${sysconfdir}/swupdate.cfg
    install -m 0644 ${UNPACKDIR}/hwrevision ${D}${sysconfdir}/hwrevision
}

FILES:${PN} += " \
    ${sysconfdir}/swupdate.cfg \
    ${sysconfdir}/hwrevision \
"
