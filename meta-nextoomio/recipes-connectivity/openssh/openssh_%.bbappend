# nextoomio OpenSSH configuration (AD-23, ARCHITECTURE.md section 3.7, AC1-AC5)
# Enforces key-only authentication, volatile authorized keys, persistent host keys, and disabled sshd by default

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://sshd_config.nextoomio \
    file://99-nextoomio-ssh.preset \
    file://10-authorized-keys.conf \
"

do_install:append() {
    # Replace default sshd_config with nextoomio hardened key-only configuration
    install -d ${D}${sysconfdir}/ssh
    install -m 0600 ${WORKDIR}/sshd_config.nextoomio ${D}${sysconfdir}/ssh/sshd_config

    # Install systemd preset to disable sshd and sshd.socket by default (AD-23)
    install -d ${D}${systemd_unitdir}/system-preset
    install -m 0644 ${WORKDIR}/99-nextoomio-ssh.preset ${D}${systemd_unitdir}/system-preset/99-nextoomio-ssh.preset

    # Install systemd drop-in requiring volatile authorized_keys before sshd can start (AC5)
    install -d ${D}${systemd_system_unitdir}/sshd.service.d
    install -m 0644 ${WORKDIR}/10-authorized-keys.conf ${D}${systemd_system_unitdir}/sshd.service.d/10-authorized-keys.conf
}

# Ensure sshd is disabled by default in BitBake systemd class (AD-23, AC1)
SYSTEMD_AUTO_ENABLE:${PN}-sshd = "disable"

FILES:${PN}-sshd += " \
    ${sysconfdir}/ssh/sshd_config \
    ${systemd_unitdir}/system-preset/99-nextoomio-ssh.preset \
    ${systemd_system_unitdir}/sshd.service.d/10-authorized-keys.conf \
"
