#!/bin/sh
# nextoomio-audio-profile-setup: Boot-time audio hardware profile and mixer initializer
# Implements SYS-02, ARCHITECTURE.md sections 3.8 and 5.3, SPECIFICATION.md section 2.1, D-011, D-012, P0-F6.1

set -u

PROFILE_FILE="/etc/nextoomio/hardware-profile.json"
DIAGNOSTICS_DIR="/run/nextoomio/diagnostics"
DIAG_FILE="/run/nextoomio/diagnostics/audio.json"
DIAG_TEXT_FILE="/run/nextoomio/diagnostics/audio.txt"

mkdir -p "$DIAGNOSTICS_DIR"

json_escape() {
    # Escape backslashes and double quotes, and sanitize control characters for valid JSON
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   '
}

write_diagnostics_disabled() {
    local reason="$1"
    local message="$2"
    local esc_reason
    local esc_message
    esc_reason=$(json_escape "$reason")
    esc_message=$(json_escape "$message")
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": null,
  "status": "disabled",
  "card_detected": false,
  "reason": "${esc_reason}",
  "message": "${esc_message}"
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
    local esc_profile_id
    local esc_error_type
    local esc_message
    esc_profile_id=$(json_escape "$profile_id")
    esc_error_type=$(json_escape "$error_type")
    esc_message=$(json_escape "$message")
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": "${esc_profile_id}",
  "status": "error",
  "card_detected": false,
  "error": "${esc_error_type}",
  "message": "${esc_message}"
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
    local esc_profile_id
    local esc_card_name
    local esc_mixer_control
    local esc_mixer_status
    esc_profile_id=$(json_escape "$profile_id")
    esc_card_name=$(json_escape "$card_name")
    esc_mixer_control=$(json_escape "$mixer_control")
    esc_mixer_status=$(json_escape "$mixer_status")
    cat << EOF > "$DIAG_FILE"
{
  "profile_id": "${esc_profile_id}",
  "status": "ok",
  "card_detected": true,
  "card_name": "${esc_card_name}",
  "mixer_control": "${esc_mixer_control}",
  "fixed_hardware_mixer_percent": ${fixed_percent},
  "mixer_status": "${esc_mixer_status}"
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
PROFILE_ID=$(grep -o '"profile_id"[^,]*' "$PROFILE_FILE" | sed -e 's/"profile_id"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -cd 'a-zA-Z0-9_-')
ALSA_CARD_ID=$(grep -o '"alsa_card_id"[^,]*' "$PROFILE_FILE" | sed -e 's/"alsa_card_id"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -cd 'a-zA-Z0-9_-')
MIXER_CONTROL=$(grep -o '"alsa_mixer_control"[^,]*' "$PROFILE_FILE" | sed -e 's/"alsa_mixer_control"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -cd 'a-zA-Z0-9_ -')
FIXED_LEVEL=$(grep -o '"fixed_hardware_mixer_percent"[^,]*' "$PROFILE_FILE" | sed -e 's/"fixed_hardware_mixer_percent"[[:space:]]*:[[:space:]]*//' -e 's/[^0-9]//g')

if [ -z "$PROFILE_ID" ]; then
    write_diagnostics_error "unknown" "malformed_profile" "Profile JSON missing profile_id field"
    echo "nextoomio-audio-profile: Malformed profile file; missing profile_id." >&2
    exit 1
fi

# Wait for ALSA sound card named in profile to become available after kernel module/overlay load (up to 10 seconds)
# Specifically match the card that the profile's overlay produces (e.g. sndrpihifiberry) rather than
# accepting any first card, to avoid mistaking Raspberry Pi onboard/HDMI audio for the HiFiBerry card (AC1, AC5).
CARD_FOUND=0
WAITED=0
CARD_LINE=""

while [ "$WAITED" -lt 10 ]; do
    if [ -f /proc/asound/cards ]; then
        if [ -n "$ALSA_CARD_ID" ]; then
            # Match line starting with card number and having [ <alsa_card_id> ]
            CARD_LINE=$(grep -E "^[[:space:]]*[0-9]+[[:space:]]*\[[[:space:]]*${ALSA_CARD_ID}[[:space:]]*\]" /proc/asound/cards 2>/dev/null | head -n 1)
        else
            CARD_LINE=$(grep -E "^[[:space:]]*[0-9]+[[:space:]]*\[" /proc/asound/cards 2>/dev/null | head -n 1)
        fi
        if [ -n "$CARD_LINE" ]; then
            CARD_FOUND=1
            break
        fi
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ "$CARD_FOUND" -ne 1 ]; then
    if [ -n "$ALSA_CARD_ID" ]; then
        write_diagnostics_error "$PROFILE_ID" "card_absent" "Configured ALSA sound card '${ALSA_CARD_ID}' was not detected after timeout"
        echo "nextoomio-audio-profile: ALSA sound card '${ALSA_CARD_ID}' for profile '${PROFILE_ID}' not detected." >&2
    else
        write_diagnostics_error "$PROFILE_ID" "card_absent" "Configured ALSA sound card was not detected after timeout"
        echo "nextoomio-audio-profile: ALSA sound card for profile '${PROFILE_ID}' not detected." >&2
    fi
    exit 1
fi

# Extract card index and clean card name/id
CARD_INDEX=$(echo "$CARD_LINE" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]].*//' | tr -cd '0-9')
CARD_NAME=$(echo "$CARD_LINE" | sed -e 's/.*\[//' -e 's/\].*//' | tr -cd 'a-zA-Z0-9_-')

if [ -z "$CARD_NAME" ]; then
    CARD_NAME="$CARD_INDEX"
fi

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

    # Apply fixed mixer level and unmute hardware control explicitly on target card via -c <card> (AC2, D-012)
    if amixer -c "$CARD_NAME" sset "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1 || \
       amixer -c "$CARD_NAME" set "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1 || \
       amixer -c "$CARD_INDEX" sset "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1 || \
       amixer -c "$CARD_INDEX" set "$MIXER_CONTROL" "${FIXED_LEVEL}%" unmute >/dev/null 2>&1; then
        if command -v alsactl >/dev/null 2>&1; then
            alsactl store "$CARD_NAME" >/dev/null 2>&1 || alsactl store "$CARD_INDEX" >/dev/null 2>&1 || alsactl store >/dev/null 2>&1 || true
        fi
        write_diagnostics_configured "$PROFILE_ID" "$CARD_NAME" "$MIXER_CONTROL" "$FIXED_LEVEL" "configured"
        echo "nextoomio-audio-profile: Initialized sound card '${CARD_NAME}' mixer '${MIXER_CONTROL}' to fixed ${FIXED_LEVEL}%."
        exit 0
    else
        write_diagnostics_error "$PROFILE_ID" "mixer_control_not_found" "ALSA mixer control '${MIXER_CONTROL}' not found on sound card '${CARD_NAME}'"
        echo "nextoomio-audio-profile: Mixer control '${MIXER_CONTROL}' not found on card '${CARD_NAME}'." >&2
        exit 1
    fi
else
    # Sound card profile does not require a hardware mixer (e.g. hifiberry-dac)
    write_diagnostics_configured "$PROFILE_ID" "$CARD_NAME" "none" 0 "no_hardware_mixer"
    echo "nextoomio-audio-profile: Initialized sound card '${CARD_NAME}' (no hardware mixer required)."
    exit 0
fi
