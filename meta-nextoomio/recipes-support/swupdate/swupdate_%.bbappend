FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://swupdate.cfg \
    file://hwrevision \
"

# Enforce unsigned updates per UPD-03, AD-13, and AC2
SWUPDATE_SIGNING = ""

do_install:append() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/swupdate.cfg ${D}${sysconfdir}/swupdate.cfg
    install -m 0644 ${WORKDIR}/hwrevision ${D}${sysconfdir}/hwrevision
}

FILES:${PN} += " \
    ${sysconfdir}/swupdate.cfg \
    ${sysconfdir}/hwrevision \
"
