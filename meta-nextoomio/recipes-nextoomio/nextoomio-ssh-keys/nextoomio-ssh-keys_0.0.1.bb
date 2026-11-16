SUMMARY = "nextoomio boot-time SSH key validator and service manager"
DESCRIPTION = "Validates developer public keys on selector partition, manages volatile authorized_keys, initializes persistent host keys on data partition, and activates sshd service (AD-23, ARCHITECTURE.md section 3.7, P0-F3.1, P0-F3.2)"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit systemd

SRC_URI = " \
    file://nextoomio-ssh-keys.service \
    file://nextoomio-ssh-keys-setup.sh \
    file://nextoomio-ssh-keys-stop.sh \
"

S = "${WORKDIR}/sources"
UNPACKDIR = "${S}"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${S}/nextoomio-ssh-keys-setup.sh ${D}${sbindir}/nextoomio-ssh-keys-setup
    install -m 0755 ${S}/nextoomio-ssh-keys-stop.sh ${D}${sbindir}/nextoomio-ssh-keys-stop

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/nextoomio-ssh-keys.service ${D}${systemd_system_unitdir}/nextoomio-ssh-keys.service
}

SYSTEMD_SERVICE:${PN} = "nextoomio-ssh-keys.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = " \
    openssh-keygen \
    nftables \
"

FILES:${PN} += " \
    ${sbindir}/nextoomio-ssh-keys-setup \
    ${sbindir}/nextoomio-ssh-keys-stop \
    ${systemd_system_unitdir}/nextoomio-ssh-keys.service \
"
