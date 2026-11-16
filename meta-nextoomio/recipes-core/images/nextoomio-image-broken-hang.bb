require nextoomio-image.bb

SUMMARY = "nextoomio deliberately broken test image: early kernel hang (P0-F4.7 AC1)"
DESCRIPTION = "Deliberately broken image variant whose early init hangs before systemd starts, triggering hardware watchdog rollback per P0-F4.7 and ARCHITECTURE.md section 3.6."

IMAGE_INSTALL:append = " nextoomio-test-faults-hang"

# Install early hang init in place of systemd to trigger hardware watchdog timeout before PID 1 systemd starts (AC1)
ROOTFS_POSTPROCESS_COMMAND += "install_broken_hang_init; "

install_broken_hang_init() {
    rm -f ${IMAGE_ROOTFS}/sbin/init
    ln -sf /sbin/nextoomio-hang-early ${IMAGE_ROOTFS}/sbin/init
}
