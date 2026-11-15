SUMMARY = "nextoomio privileged operations helper daemon"
DESCRIPTION = "Minimal privileged operations helper (slot writes, hardware profile include, NAS mounts, nftables, reboot)"
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit cargo cargo-update-recipe-crates

FILESEXTRAPATHS:prepend := "${LAYERDIR}/..:"

SRC_URI = " \
    file://core \
"

require ${BPN}-crates.inc

S = "${WORKDIR}/core"
CARGO_SRC_DIR = ""

CARGO_BUILD_FLAGS:append = " -p nx-sysd"
