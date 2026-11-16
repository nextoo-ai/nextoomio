#!/bin/sh
# nextoomio-audio-profile-setup: Boot-time audio hardware profile and mixer initializer
# Implements SYS-02, ARCHITECTURE.md sections 3.8 and 5.3, SPECIFICATION.md section 2.1, D-011, D-012, P0-F6.1

set -u

PROFILE_FILE="/etc/nextoomio/hardware-profile.json"
DIAGNOSTICS_DIR="/run/nextoomio/diagnostics"
DIAG_FILE="/run/nextoomio/diagnostics/audio.json"
DIAG_TEXT_FILE="/run/nextoomio/diagnostics/audio.txt"

mkdir -p "$DIAGNOSTICS_DIR"

write_diagnostics_disabled() {
    local reason="$1"
    local message="$2"
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": null,
  "status": "disabled",
  "card_detected": false,
  "reason": "${reason}",
  "message": "${message}"
}
EOF
    cat << EOF > "$DIAG_TEXT_FILE"
Audio profile: none
Status: disabled
Card detected: no
Reason: ${reason}
Message: ${message}
EOF
}

write_diagnostics_error() {
    local profile_id="$1"
    local error_type="$2"
    local message="$3"
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": "${profile_id}",
  "status": "error",
  "card_detected": false,
  "error": "${error_type}",
  "message": "${message}"
}
EOF
    cat << EOF > "$DIAG_TEXT_FILE"
Audio profile: ${profile_id}
Status: error
Card detected: no
Error: ${error_type}
Message: ${message}
EOF
}

write_diagnostics_configured() {
    local profile_id="$1"
    local card_name="$2"
    local mixer_control="$3"
    local fixed_percent="$4"
    local mixer_status="$5"
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": "${profile_id}",
  "status": "ok",
  "card_detected": true,
  "card_name": "${card_name}",
  "mixer_control": "${mixer_control}",
  "fixed_hardware_mixer_percent": ${fixed_percent},
  "mixer_status": "${mixer_status}"
}
EOF
    cat << EOF > "$DIAG_TEXT_FILE"
Audio profile: ${profile_id}
Status: ok
Card detected: yes
Card name: ${card_name}
Mixer control: ${mixer_control}
Fixed hardware mixer: ${fixed_percent}%
Mixer status: ${mixer_status}
EOF
}

# If no hardware profile is configured on the root filesystem, record and exit (AC5)
if [ ! -f "$PROFILE_FILE" ]; then
    write_diagnostics_disabled "no_profile_file" "No hardware profile installed at /etc/nextoomio/hardware-profile.json"
    echo "nextoomio-audio-profile: No profile configured; audio initialization skipped."
    exit 0
fi

# Extract profile parameters using POSIX text processing (no jq requirement in early boot)
PROFILE_ID=$(grep -o '"profile_id"[^,]*' "$PROFILE_FILE" | sed -e 's/"profile_id"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -d '[:space:]')
MIXER_CONTROL=$(grep -o '"alsa_mixer_control"[^,]*' "$PROFILE_FILE" | sed -e 's/"alsa_mixer_control"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -d '[:space:]')
FIXED_LEVEL=$(grep -o '"fixed_hardware_mixer_percent"[^,]*' "$PROFILE_FILE" | sed -e 's/"fixed_hardware_mixer_percent"[[:space:]]*:[[:space:]]*//' -e 's/[^0-9]//g' | tr -d '[:space:]')

if [ -z "$PROFILE_ID" ]; then
    write_diagnostics_error "unknown" "malformed_profile" "Profile JSON missing profile_id field"
    echo "nextoomio-audio-profile: Malformed profile file; missing profile_id." >&2
    exit 1
fi

# Wait for ALSA sound card to become available after kernel module/overlay load (up to 10 seconds)
CARD_FOUND=0
WAITED=0
while [ "$WAITED" -lt 10 ]; do
    if [ -f /proc/asound/cards ] && grep -q -E "[0-9]+ \[" /proc/asound/cards; then
        CARD_FOUND=1
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ "$CARD_FOUND" -ne 1 ]; then
    write_diagnostics_error "$PROFILE_ID" "card_absent" "Configured ALSA sound card was not detected after timeout"
    echo "nextoomio-audio-profile: ALSA sound card for profile '${PROFILE_ID}' not detected." >&2
    exit 1
fi

CARD_LINE=$(grep -E "[0-9]+ \[" /proc/asound/cards 2>/dev/null | head -n 1 | sed 's/^[[:space:]]*//')
CARD_NAME=$(echo "$CARD_LINE" | sed -e 's/^[0-9]\+[[:space:]]*\[//' -e 's/\].*//')

# Set fixed hardware mixer level if the profile defines a mixer control (D-012, AC2)
if [ -n "$MIXER_CONTROL" ] && [ "$MIXER_CONTROL" != "none" ]; then
    if [ -z "$FIXED_LEVEL" ]; then
        write_diagnostics_error "$PROFILE_ID" "missing_fixed_level" "Profile specifies mixer control '${MIXER_CONTROL}' but no fixed_hardware_mixer_percent"
        echo "nextoomio-audio-profile: Profile specifies mixer control '${MIXER_CONTROL}' without fixed level." >&2
        exit 1
    fi

    if ! command -v amixer >/dev/null 2>&1; then
        write_diagnostics_error "$PROFILE_ID" "missing_amixer_utility" "Required ALSA utility 'amixer' not found"
        echo "nextoomio-audio-profile: Required utility 'amixer' is missing." >&2
        exit 1
    fi

    # Apply fixed mixer level and unmute hardware control (AC2, D-012)
    if amixer sset "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1 || \
       amixer set "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1; then
        if command -v alsactl >/dev/null 2>&1; then
            alsactl store >/dev/null 2>&1 || true
        fi
        write_diagnostics_configured "$PROFILE_ID" "$CARD_NAME" "$MIXER_CONTROL" "$FIXED_LEVEL" "configured"
        echo "nextoomio-audio-profile: Initialized sound card '${CARD_NAME}' mixer '${MIXER_CONTROL}' to fixed ${FIXED_LEVEL}%."
        exit 0
    else
        write_diagnostics_error "$PROFILE_ID" "mixer_control_not_found" "ALSA mixer control '${MIXER_CONTROL}' not found on sound card"
        echo "nextoomio-audio-profile: Mixer control '${MIXER_CONTROL}' not found on card '${CARD_NAME}'." >&2
        exit 1
    fi
else
    # Sound card profile does not require a hardware mixer (e.g. hifiberry-dac)
    write_diagnostics_configured "$PROFILE_ID" "$CARD_NAME" "none" 0 "no_hardware_mixer"
    echo "nextoomio-audio-profile: Initialized sound card '${CARD_NAME}' (no hardware mixer required)."
    exit 0
fi
