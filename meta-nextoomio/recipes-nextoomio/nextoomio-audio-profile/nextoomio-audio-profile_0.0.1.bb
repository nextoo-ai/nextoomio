SUMMARY = "nextoomio audio hardware profile and boot include manager"
DESCRIPTION = "Installs audio hardware profiles, derives boot include from active profile, and sets fixed hardware mixer at boot (SYS-02, ARCHITECTURE.md sections 3.8 and 5.3, D-011, D-012)"
HOMEPAGE = "https://github.com/nextoo-ai/nextoomio"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit systemd deploy

NEXTOOMIO_AUDIO_PROFILE ?= "hifiberry-amp"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:${THISDIR}/../../files/hardware-profiles:"

SRC_URI = " \
    file://hifiberry-amp.json \
    file://nextoomio-audio-profile.service \
    file://nextoomio-audio-profile-setup.sh \
"

S = "${UNPACKDIR}"

do_compile() {
    # Derive boot include from selected hardware profile (D-011, ARCHITECTURE.md section 3.8, CQ-R1)
    if [ -n "${NEXTOOMIO_AUDIO_PROFILE}" ] && [ -f "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" ]; then
        python3 -c '
import json
import sys

profile_path = sys.argv[1]
profile_id = sys.argv[2]
out_path = sys.argv[3]

with open(profile_path, "r", encoding="utf-8") as f:
    data = json.load(f)

overlay = data.get("dtoverlay") or ""
raw_params = data.get("dtoverlay_params") or []
params = [str(p).strip() for p in raw_params if str(p).strip()]
card_id = data.get("alsa_card_id") or ""

with open(out_path, "w", encoding="utf-8") as out:
    out.write("# nextoomio sound card hardware overlay include (ARCHITECTURE.md section 3.8, D-011)\n")
    out.write("# Derived from hardware profile: %s\n" % profile_id)
    if card_id:
        out.write("# Expected ALSA card ID: %s\n" % card_id)
    if overlay:
        if params:
            out.write("dtoverlay=%s,%s\n" % (overlay, ",".join(params)))
        else:
            out.write("dtoverlay=%s\n" % overlay)
' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" "${NEXTOOMIO_AUDIO_PROFILE}" "${B}/nextoomio-hardware.txt"
    else
        echo "# No audio hardware profile configured" > "${B}/nextoomio-hardware.txt"
    fi
}

do_install() {
    # Install hardware profile definitions
    install -d ${D}${datadir}/nextoomio/hardware-profiles
    install -m 0644 ${S}/hifiberry-amp.json ${D}${datadir}/nextoomio/hardware-profiles/

    # Install active profile to /etc/nextoomio/hardware-profile.json
    install -d ${D}${sysconfdir}/nextoomio
    if [ -n "${NEXTOOMIO_AUDIO_PROFILE}" ] && [ -f "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" ]; then
        install -m 0644 ${S}/${NEXTOOMIO_AUDIO_PROFILE}.json ${D}${sysconfdir}/nextoomio/hardware-profile.json
    fi

    # Install boot-time mixer setup script and systemd service
    install -d ${D}${sbindir}
    install -m 0755 ${S}/nextoomio-audio-profile-setup.sh ${D}${sbindir}/nextoomio-audio-profile-setup

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/nextoomio-audio-profile.service ${D}${systemd_system_unitdir}/nextoomio-audio-profile.service
}

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0644 ${B}/nextoomio-hardware.txt ${DEPLOYDIR}/nextoomio-hardware.txt
}

addtask deploy before do_build after do_compile

SYSTEMD_SERVICE:${PN} = "nextoomio-audio-profile.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = " \
    alsa-utils \
"

FILES:${PN} += " \
    ${datadir}/nextoomio/hardware-profiles \
    ${sysconfdir}/nextoomio/hardware-profile.json \
    ${sbindir}/nextoomio-audio-profile-setup \
    ${systemd_system_unitdir}/nextoomio-audio-profile.service \
"
