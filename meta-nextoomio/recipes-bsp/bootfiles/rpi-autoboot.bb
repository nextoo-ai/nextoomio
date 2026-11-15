SUMMARY = "Raspberry Pi autoboot.txt configuration for nextoomio A/B selection"
DESCRIPTION = "Provides autoboot.txt for tryboot A/B partition selection on Raspberry Pi 4 (ARCHITECTURE.md section 3.6, AD-14, D-005)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit deploy

# Default to slot A (partition 2 = boot_a). On fresh installation, slot B is empty / not installed (AC5).
AUTOBOOT_DEFAULT_PARTITION ?= "2"
AUTOBOOT_TRYBOOT_PARTITION ?= "2"

do_compile[noexec] = "1"
do_install[noexec] = "1"

do_deploy() {
    install -d ${DEPLOYDIR}
    cat > ${DEPLOYDIR}/autoboot.txt <<EOF
[all]
tryboot_a_b=1
boot_partition=${AUTOBOOT_DEFAULT_PARTITION}

[tryboot]
boot_partition=${AUTOBOOT_TRYBOOT_PARTITION}
EOF
}

addtask deploy before do_build after do_compile
