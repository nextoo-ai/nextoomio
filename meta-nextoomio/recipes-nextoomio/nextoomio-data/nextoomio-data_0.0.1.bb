SUMMARY = "nextoomio persistent data partition management"
DESCRIPTION = "Manages /data partition initialization, first-boot resize, boot check/repair, and safe recovery (ARCHITECTURE.md section 3.5, SYS-04)"
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit systemd

SRC_URI = " \
    file://nextoomio-boot-mount.sh \
    file://nextoomio-boot-mount.service \
    file://nextoomio-data-grow.sh \
    file://nextoomio-data-grow.service \
    file://nextoomio-data-check.sh \
    file://nextoomio-data-check.service \
    file://nextoomio-data-init.sh \
    file://nextoomio-data-init.service \
    file://data.mount.d/10-condition.conf \
"

S = "${WORKDIR}/sources"
UNPACKDIR = "${S}"

SYSTEMD_SERVICE:${PN} = " \
    nextoomio-boot-mount.service \
    nextoomio-data-grow.service \
    nextoomio-data-check.service \
    nextoomio-data-init.service \
"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = " \
    e2fsprogs-e2fsck \
    e2fsprogs-resize2fs \
    parted \
    util-linux-partx \
    util-linux-blockdev \
    util-linux-fallocate \
"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${UNPACKDIR}/nextoomio-boot-mount.sh ${D}${sbindir}/nextoomio-boot-mount
    install -m 0755 ${UNPACKDIR}/nextoomio-data-grow.sh ${D}${sbindir}/nextoomio-data-grow
    install -m 0755 ${UNPACKDIR}/nextoomio-data-check.sh ${D}${sbindir}/nextoomio-data-check
    install -m 0755 ${UNPACKDIR}/nextoomio-data-init.sh ${D}${sbindir}/nextoomio-data-init

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/nextoomio-boot-mount.service ${D}${systemd_system_unitdir}/nextoomio-boot-mount.service
    install -m 0644 ${UNPACKDIR}/nextoomio-data-grow.service ${D}${systemd_system_unitdir}/nextoomio-data-grow.service
    install -m 0644 ${UNPACKDIR}/nextoomio-data-check.service ${D}${systemd_system_unitdir}/nextoomio-data-check.service
    install -m 0644 ${UNPACKDIR}/nextoomio-data-init.service ${D}${systemd_system_unitdir}/nextoomio-data-init.service

    # data.mount condition drop-in to prevent mounting when data_unavailable is set (AC5)
    install -d ${D}${systemd_system_unitdir}/data.mount.d
    install -m 0644 ${UNPACKDIR}/data.mount.d/10-condition.conf ${D}${systemd_system_unitdir}/data.mount.d/10-condition.conf

    # Ensure /data and /boot mountpoint directories exist in the rootfs
    install -d ${D}/data
    install -d ${D}/boot
}

FILES:${PN} += " \
    ${sbindir}/nextoomio-boot-mount \
    ${sbindir}/nextoomio-data-grow \
    ${sbindir}/nextoomio-data-check \
    ${sbindir}/nextoomio-data-init \
    ${systemd_system_unitdir}/nextoomio-boot-mount.service \
    ${systemd_system_unitdir}/nextoomio-data-grow.service \
    ${systemd_system_unitdir}/nextoomio-data-check.service \
    ${systemd_system_unitdir}/nextoomio-data-init.service \
    ${systemd_system_unitdir}/data.mount.d/10-condition.conf \
    /data \
    /boot \
"
