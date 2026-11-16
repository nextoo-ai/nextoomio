require nextoomio-image.bb

SUMMARY = "nextoomio deliberately broken test image: failing local health check (P0-F4.7 AC2)"
DESCRIPTION = "Deliberately broken image variant that boots systemd but deliberately fails the local health check per P0-F4.7 and ARCHITECTURE.md section 3.6."

# Mask required health check services (PipeWire readiness and audio hardware profile per ARCHITECTURE.md section 3.6 step 5)
ROOTFS_POSTPROCESS_COMMAND += "disable_health_services; "

disable_health_services() {
    mkdir -p ${IMAGE_ROOTFS}/etc/systemd/system
    ln -sf /dev/null ${IMAGE_ROOTFS}/etc/systemd/system/pipewire.service
    ln -sf /dev/null ${IMAGE_ROOTFS}/etc/systemd/system/nextoomio-audio-profile.service
}
