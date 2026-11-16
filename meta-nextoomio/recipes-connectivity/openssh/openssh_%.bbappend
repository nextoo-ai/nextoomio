# nextoomio OpenSSH configuration (AD-23, ARCHITECTURE.md section 3.7, AC1-AC5)
# Enforces key-only authentication, volatile authorized keys, persistent host keys, and disabled sshd by default

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://sshd_config.nextoomio \
    file://99-nextoomio-ssh.preset \
    file://10-authorized-keys.conf \
"

# Enforce standalone sshd.service mode in OpenEmbedded-core OpenSSH recipe (AD-23, AC1)
# OpenEmbedded-core defines SYSTEMD_SERVICE:${PN}-sshd dynamically via PACKAGECONFIG:
# - systemd-sshd-socket-mode installs sshd.socket
# - systemd-sshd-service-mode installs sshd.service
# Enforcing service mode guarantees /lib/systemd/system/sshd.service is installed and packaged.
PACKAGECONFIG:append = " systemd-sshd-service-mode"
PACKAGECONFIG:remove = " systemd-sshd-socket-mode"

do_install:append() {
    # Replace default sshd_config with nextoomio hardened key-only configuration
    sdir="${UNPACKDIR}"
    [ -d "$sdir" ] || sdir="${UNPACKDIR}"

    install -d ${D}${sysconfdir}/ssh
    install -m 0600 $sdir/sshd_config.nextoomio ${D}${sysconfdir}/ssh/sshd_config

    # Install systemd preset to disable sshd and sshd.socket by default (AD-23)
    install -d ${D}${systemd_unitdir}/system-preset
    install -m 0644 $sdir/99-nextoomio-ssh.preset ${D}${systemd_unitdir}/system-preset/99-nextoomio-ssh.preset

    # Install systemd drop-in requiring volatile authorized_keys before sshd can start (AC5)
    install -d ${D}${systemd_system_unitdir}/sshd.service.d
    install -m 0644 $sdir/10-authorized-keys.conf ${D}${systemd_system_unitdir}/sshd.service.d/10-authorized-keys.conf
}

# Ensure sshd is disabled by default in BitBake systemd class (AD-23, AC1)
SYSTEMD_AUTO_ENABLE:${PN}-sshd = "disable"

FILES:${PN}-sshd += " \
    ${sysconfdir}/ssh/sshd_config \
    ${systemd_unitdir}/system-preset/99-nextoomio-ssh.preset \
    ${systemd_system_unitdir}/sshd.service.d/10-authorized-keys.conf \
"
