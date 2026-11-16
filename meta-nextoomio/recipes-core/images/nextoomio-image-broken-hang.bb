require nextoomio-image.bb

SUMMARY = "nextoomio deliberately broken test image: early kernel hang (P0-F4.7 AC1)"
DESCRIPTION = "Deliberately broken image variant whose early init hangs before systemd starts per P0-F4.7 and ARCHITECTURE.md section 3.6."

IMAGE_INSTALL:append = " nextoomio-test-faults-hang"

# Install early hang init in place of systemd to halt before systemd starts (AC1).
# Note: Automatic board reset requires continuous pre-systemd watchdog arming
# (kernel_watchdog_timeout / bootloader watchdog per AR-01 and spike S1 / #17),
# which is not yet configured in the layer; without it, this image halts early.
ROOTFS_POSTPROCESS_COMMAND += "install_broken_hang_init; "

install_broken_hang_init() {
    rm -f ${IMAGE_ROOTFS}/sbin/init
    ln -sf /sbin/nextoomio-hang-early ${IMAGE_ROOTFS}/sbin/init
}
