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

S = "${WORKDIR}/sources"
UNPACKDIR = "${S}"

do_compile() {
    # Derive boot include from selected hardware profile (D-011, ARCHITECTURE.md section 3.8)
    if [ -n "${NEXTOOMIO_AUDIO_PROFILE}" ] && [ -f "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" ]; then
        OVERLAY=$(grep -o '"dtoverlay"[^,]*' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" | sed -e 's/"dtoverlay"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -d '[:space:]')
        PARAMS=$(grep -A 5 '"dtoverlay_params"' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" | grep '"' | sed -e 's/.*"\(.*\)".*/\1/' | tr '\n' ',' | sed 's/,$//')
        ALSA_CARD_ID=$(grep -o '"alsa_card_id"[^,]*' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" | sed -e 's/"alsa_card_id"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -cd 'a-zA-Z0-9_-')

        echo "# nextoomio sound card hardware overlay include (ARCHITECTURE.md section 3.8, D-011)" > "${B}/nextoomio-hardware.txt"
        echo "# Derived from hardware profile: ${NEXTOOMIO_AUDIO_PROFILE}" >> "${B}/nextoomio-hardware.txt"
        if [ -n "$ALSA_CARD_ID" ]; then
            echo "# Expected ALSA card ID: ${ALSA_CARD_ID}" >> "${B}/nextoomio-hardware.txt"
        fi
        if [ -n "$OVERLAY" ]; then
            if [ -n "$PARAMS" ]; then
                echo "dtoverlay=${OVERLAY},${PARAMS}" >> "${B}/nextoomio-hardware.txt"
            else
                echo "dtoverlay=${OVERLAY}" >> "${B}/nextoomio-hardware.txt"
            fi
        fi
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
