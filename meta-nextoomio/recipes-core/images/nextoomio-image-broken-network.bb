require nextoomio-image.bb

SUMMARY = "nextoomio deliberately broken test image: unreachable network / no SSH service (P0-F4.7 AC3)"
DESCRIPTION = "Deliberately broken image variant that boots and is locally healthy but has no working network or SSH service per P0-F4.7 and ARCHITECTURE.md section 3.6."

# Mask network and ssh services in the root filesystem so no external connection is possible (AC3)
ROOTFS_POSTPROCESS_COMMAND += "disable_network_and_ssh; "

disable_network_and_ssh() {
    mkdir -p ${IMAGE_ROOTFS}/etc/systemd/system
    ln -sf /dev/null ${IMAGE_ROOTFS}/etc/systemd/system/NetworkManager.service
    ln -sf /dev/null ${IMAGE_ROOTFS}/etc/systemd/system/sshd.service
    ln -sf /dev/null ${IMAGE_ROOTFS}/etc/systemd/system/nextoomio-ssh-keys.service
}
