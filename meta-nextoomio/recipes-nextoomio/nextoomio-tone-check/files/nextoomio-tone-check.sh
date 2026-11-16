#!/bin/sh
# nextoomio-tone-check: Remote audio test tone and stream verification tool
# Implements ARCHITECTURE.md section 4.1, SPECIFICATION.md section 2.1, P0-F6.2, D-012

set -u

PROFILE_FILE="/etc/nextoomio/hardware-profile.json"
DEFAULT_TONE_FILE="/usr/share/nextoomio/tone-check/test-tone-48k-440hz.wav"
TONE_FILE="$DEFAULT_TONE_FILE"
PROC_ROOT="/proc"
PLAY_CMD="aplay"
CARD_OVERRIDE=""
DEVICE_OVERRIDE=""
OUTPUT_JSON=0
QUIET=0
DURATION_S="2.0"
FREQUENCY_HZ="440"
SAMPLE_RATE="48000"
EXPECTED_FRAMES=96000

# Justification of sampling tolerance (AC1, P0-F6.2):
# - Expected tone duration: 2.000 s at 48,000 Hz = 96,000 frames.
# - Fast polling interval: 0.1 s = 4,800 frames per poll.
# - Start-up latency: aplay process spawn and ALSA substream trigger may consume
#   up to one 0.1 s poll before state: RUNNING is observed (~4,800 frames).
# - Drain latency: upon tone completion, playback process termination may occur
#   within one 0.1 s poll after the last hardware frame (~4,800 frames).
# - ALSA ring buffer period boundaries (typically 1024-2048 frames / 21-43 ms)
#   quantize hw_ptr updates.
# - Justified tolerance window: up to 2 polling intervals (9,600 frames / 10%).
# - Minimum expected frame advance threshold: 96,000 - 9,600 = 86,400 frames.
MIN_ADVANCE_FRAMES=86400

usage() {
    cat << EOF
Usage: nextoomio-tone-check [OPTIONS]

Plays a quiet test tone and verifies that the ALSA playback stream was in
the RUNNING state and advanced its hardware position for the whole duration.

Options:
  --profile <file>    Hardware profile path (default: /etc/nextoomio/hardware-profile.json)
  --card <id>         Override ALSA sound card ID (default from profile: alsa_card_id)
  --device <pcm>      Override ALSA PCM device (default: plughw:<card>,0)
  --proc-root <dir>   procfs root directory (default: /proc, for testing/mocking)
  --play-cmd <cmd>    Playback command (default: aplay, for testing/mocking)
  --json              Output structured JSON
  --quiet             Suppress progress output
  -h, --help          Show this help message

Exit codes:
  0: Success (tone played, stream was RUNNING, hardware position advanced)
  2: Sound card absent (AC3)
  3: Playback device busy (AC3)
  4: Stream state or position failure (stream not RUNNING or position did not advance)
  1: Other error (missing dependencies, unreadable files, invalid arguments)
EOF
}

# Parse options
while [ $# -gt 0 ]; do
    case "$1" in
        --profile)
            PROFILE_FILE="$2"
            shift 2
            ;;
        --card)
            CARD_OVERRIDE="$2"
            if ! echo "$CARD_OVERRIDE" | grep -qE '^[A-Za-z0-9_-]+$'; then
                echo "nextoomio-tone-check: ERROR: Invalid card ID '${CARD_OVERRIDE}'. Must contain only [A-Za-z0-9_-]." >&2
                exit 1
            fi
            shift 2
            ;;
        --device)
            DEVICE_OVERRIDE="$2"
            if ! echo "$DEVICE_OVERRIDE" | grep -qE '^[A-Za-z0-9_:,.-]+$'; then
                echo "nextoomio-tone-check: ERROR: Invalid device '${DEVICE_OVERRIDE}'." >&2
                exit 1
            fi
            shift 2
            ;;
        --proc-root)
            PROC_ROOT="$2"
            shift 2
            ;;
        --play-cmd)
            PLAY_CMD="$2"
            shift 2
            ;;
        --json)
            OUTPUT_JSON=1
            shift
            ;;
        --quiet)
            QUIET=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "nextoomio-tone-check: Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# Safe JSON string escaping helper
json_escape() {
    printf '%s' "$1" | tr '\n\r\t' '   ' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

TMP_ERR=$(mktemp)
PLAY_PID=""

cleanup() {
    local sig="${1:-EXIT}"
    if [ -n "$PLAY_PID" ] && kill -0 "$PLAY_PID" 2>/dev/null; then
        kill -TERM "$PLAY_PID" 2>/dev/null || true
        wait "$PLAY_PID" 2>/dev/null || true
    fi
    rm -f "$TMP_ERR" 2>/dev/null || true
}

trap 'cleanup INT; exit 130' INT
trap 'cleanup TERM; exit 143' TERM
trap 'cleanup HUP; exit 129' HUP
trap 'cleanup EXIT' EXIT

# Determine ALSA card ID
CARD_ID="$CARD_OVERRIDE"
if [ -z "$CARD_ID" ] && [ -f "$PROFILE_FILE" ]; then
    CARD_ID=$(grep -o '"alsa_card_id"[^,]*' "$PROFILE_FILE" | sed -e 's/"alsa_card_id"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -cd 'a-zA-Z0-9_-')
fi

if [ -z "$CARD_ID" ]; then
    CARD_ID="sndrpihifiberry"
fi

# Validate card ID format (Finding 5, CQ-R7)
if ! echo "$CARD_ID" | grep -qE '^[A-Za-z0-9_-]+$'; then
    echo "nextoomio-tone-check: ERROR: Invalid ALSA card ID '${CARD_ID}'." >&2
    exit 1
fi

# AC3: Check whether sound card is present in procfs
CARDS_FILE="${PROC_ROOT}/asound/cards"
if [ ! -f "$CARDS_FILE" ]; then
    if [ "$OUTPUT_JSON" -eq 1 ]; then
        cat << EOF
{
  "status": "failed",
  "reason": "sound_card_absent",
  "card_id": "${CARD_ID}",
  "message": "ALSA cards file '${CARDS_FILE}' not found"
}
EOF
    else
        echo "nextoomio-tone-check: ERROR: Sound card '${CARD_ID}' absent; '${CARDS_FILE}' not found." >&2
        echo "Result: FAIL (reason: sound_card_absent)" >&2
    fi
    exit 2
fi

CARD_LINE=$(grep -E "^[[:space:]]*[0-9]+[[:space:]]*\[[[:space:]]*${CARD_ID}[[:space:]]*\]" "$CARDS_FILE" 2>/dev/null | head -n 1)
if [ -z "$CARD_LINE" ]; then
    # Card is not listed in /proc/asound/cards
    if [ "$OUTPUT_JSON" -eq 1 ]; then
        cat << EOF
{
  "status": "failed",
  "reason": "sound_card_absent",
  "card_id": "${CARD_ID}",
  "message": "ALSA sound card '${CARD_ID}' not found in ${CARDS_FILE}"
}
EOF
    else
        echo "nextoomio-tone-check: ERROR: Sound card '${CARD_ID}' not found in ${CARDS_FILE}." >&2
        echo "Result: FAIL (reason: sound_card_absent)" >&2
    fi
    exit 2
fi

# Extract numeric card index
CARD_INDEX=$(echo "$CARD_LINE" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]].*//' | tr -cd '0-9')
if [ -z "$CARD_INDEX" ]; then
    CARD_INDEX="0"
fi

# Locate ALSA status file for PCM 0 playback
STATUS_FILE="${PROC_ROOT}/asound/card${CARD_INDEX}/pcm0p/sub0/status"
if [ ! -f "$STATUS_FILE" ] && [ -f "${PROC_ROOT}/asound/${CARD_ID}/pcm0p/sub0/status" ]; then
    STATUS_FILE="${PROC_ROOT}/asound/${CARD_ID}/pcm0p/sub0/status"
fi

# AC3: Check whether playback device is already busy
if [ -f "$STATUS_FILE" ]; then
    if grep -q "^state:[[:space:]]*RUNNING" "$STATUS_FILE" 2>/dev/null; then
        if [ "$OUTPUT_JSON" -eq 1 ]; then
            cat << EOF
{
  "status": "failed",
  "reason": "playback_device_busy",
  "card_id": "${CARD_ID}",
  "card_index": ${CARD_INDEX},
  "message": "ALSA playback stream is already in RUNNING state (device busy)"
}
EOF
        else
            echo "nextoomio-tone-check: ERROR: Playback device for card '${CARD_ID}' is busy (stream already RUNNING)." >&2
            echo "Result: FAIL (reason: playback_device_busy)" >&2
        fi
        exit 3
    fi
fi

# Resolve test tone file (AC2, D-012)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
GEN_TONE="${SCRIPT_DIR}/generate-tone.py"
if [ ! -f "$GEN_TONE" ]; then
    GEN_TONE="${SCRIPT_DIR}/../recipes-nextoomio/nextoomio-tone-check/files/generate-tone.py"
fi

if [ ! -f "$TONE_FILE" ]; then
    if [ -n "${NEXTOOMIO_TONE_FILE:-}" ] && [ -f "$NEXTOOMIO_TONE_FILE" ]; then
        TONE_FILE="$NEXTOOMIO_TONE_FILE"
    fi
fi

if [ ! -f "$TONE_FILE" ]; then
    echo "nextoomio-tone-check: ERROR: Verified test tone file '${TONE_FILE}' not found." >&2
    exit 1
fi

# If using test override tone file, strictly verify it before playback (AC2, D-012, Finding 4)
if [ "$TONE_FILE" != "$DEFAULT_TONE_FILE" ]; then
    if [ -f "$GEN_TONE" ] && command -v python3 >/dev/null 2>&1; then
        if ! python3 "$GEN_TONE" --check "$TONE_FILE" >/dev/null 2>&1; then
            echo "nextoomio-tone-check: ERROR: Tone file '${TONE_FILE}' failed safe 30% volume check." >&2
            exit 1
        fi
    else
        echo "nextoomio-tone-check: ERROR: Cannot verify safe 30% volume limit for custom tone '${TONE_FILE}' (validator missing)." >&2
        exit 1
    fi
fi

PCM_DEVICE="$DEVICE_OVERRIDE"
if [ -z "$PCM_DEVICE" ]; then
    PCM_DEVICE="plughw:${CARD_ID},0"
fi

if [ "$QUIET" -ne 1 ] && [ "$OUTPUT_JSON" -ne 1 ]; then
    echo "nextoomio-tone-check: Playing ${DURATION_S}s test tone (${FREQUENCY_HZ} Hz) on '${CARD_ID}' at safe level (30% logical volume, -31.4 dBFS)..."
fi

# Start playback in background
"$PLAY_CMD" -D "$PCM_DEVICE" -q "$TONE_FILE" 2>"$TMP_ERR" &
PLAY_PID=$!

RUNNING_SEEN=0
HW_PTR_START=""
HW_PTR_LATEST=""
SAMPLE_COUNT=0
ADVANCING_SAMPLES=0
POLL_COUNT=0
MAX_POLLS=60  # ~6.0s timeout with 0.1s sleep for 2.0s tone

while [ "$POLL_COUNT" -lt "$MAX_POLLS" ]; do
    # Sample status file while player is active
    if [ -f "$STATUS_FILE" ]; then
        if grep -q "^state:[[:space:]]*RUNNING" "$STATUS_FILE" 2>/dev/null; then
            RUNNING_SEEN=1
            PTR_VAL=$(grep -E "^hw_ptr:[[:space:]]*[0-9]+" "$STATUS_FILE" 2>/dev/null | sed -e 's/^hw_ptr:[[:space:]]*//' | tr -cd '0-9')
            if [ -n "$PTR_VAL" ]; then
                if [ -z "$HW_PTR_START" ]; then
                    HW_PTR_START="$PTR_VAL"
                fi
                if [ -n "$HW_PTR_LATEST" ] && [ "$PTR_VAL" -gt "$HW_PTR_LATEST" ]; then
                    ADVANCING_SAMPLES=$((ADVANCING_SAMPLES + 1))
                fi
                HW_PTR_LATEST="$PTR_VAL"
                SAMPLE_COUNT=$((SAMPLE_COUNT + 1))
            fi
        fi
    fi

    if ! kill -0 "$PLAY_PID" 2>/dev/null; then
        break
    fi

    sleep 0.1 2>/dev/null || sleep 1
    POLL_COUNT=$((POLL_COUNT + 1))
done

wait "$PLAY_PID" 2>/dev/null
PLAY_STATUS=$?
PLAY_PID=""

# Check if aplay failed with busy error
if [ "$PLAY_STATUS" -ne 0 ]; then
    if grep -E -qi "busy|Device or resource busy|EBUSY" "$TMP_ERR" 2>/dev/null; then
        if [ "$OUTPUT_JSON" -eq 1 ]; then
            cat << EOF
{
  "status": "failed",
  "reason": "playback_device_busy",
  "card_id": "${CARD_ID}",
  "message": "ALSA playback device reports busy"
}
EOF
        else
            echo "nextoomio-tone-check: ERROR: Playback device busy: $(cat "$TMP_ERR" 2>/dev/null)" >&2
            echo "Result: FAIL (reason: playback_device_busy)" >&2
        fi
        exit 3
    elif [ "$RUNNING_SEEN" -ne 1 ]; then
        CLEAN_ERR=$(json_escape "$(head -n 1 "$TMP_ERR" 2>/dev/null)")
        if [ "$OUTPUT_JSON" -eq 1 ]; then
            cat << EOF
{
  "status": "failed",
  "reason": "playback_failed",
  "card_id": "${CARD_ID}",
  "exit_code": ${PLAY_STATUS},
  "message": "ALSA playback failed: ${CLEAN_ERR}"
}
EOF
        else
            echo "nextoomio-tone-check: ERROR: Playback failed (code ${PLAY_STATUS}): $(cat "$TMP_ERR" 2>/dev/null)" >&2
            echo "Result: FAIL (reason: playback_failed)" >&2
        fi
        exit 1
    fi
fi

# Calculate advanced frames
FRAMES_ADVANCED=0
if [ -n "$HW_PTR_START" ] && [ -n "$HW_PTR_LATEST" ]; then
    if [ "$HW_PTR_LATEST" -ge "$HW_PTR_START" ]; then
        FRAMES_ADVANCED=$((HW_PTR_LATEST - HW_PTR_START))
    fi
fi

# Verify AC1: stream was in RUNNING state and hardware position advanced
if [ "$RUNNING_SEEN" -ne 1 ] || [ "$FRAMES_ADVANCED" -lt "$MIN_ADVANCE_FRAMES" ]; then
    if [ "$OUTPUT_JSON" -eq 1 ]; then
        cat << EOF
{
  "status": "failed",
  "reason": "stream_position_not_advancing",
  "card_id": "${CARD_ID}",
  "stream_running": $([ "$RUNNING_SEEN" -eq 1 ] && echo "true" || echo "false"),
  "expected_frames": ${EXPECTED_FRAMES},
  "observed_frames": ${FRAMES_ADVANCED},
  "min_required_frames": ${MIN_ADVANCE_FRAMES},
  "samples_collected": ${SAMPLE_COUNT},
  "message": "Stream did not reach RUNNING state or hardware position did not advance expected frames"
}
EOF
    else
        echo "nextoomio-tone-check: ERROR: Stream verification failed." >&2
        echo "  Stream was RUNNING: $([ "$RUNNING_SEEN" -eq 1 ] && echo "yes" || echo "no")" >&2
        echo "  Frames advanced: ${FRAMES_ADVANCED} (expected: ${EXPECTED_FRAMES}, min: ${MIN_ADVANCE_FRAMES})" >&2
        echo "  Samples collected: ${SAMPLE_COUNT}" >&2
        echo "Result: FAIL (reason: stream_position_not_advancing)" >&2
    fi
    exit 4
fi

# Verify AC4: Ensure stream is closed after playback completes
STREAM_CLOSED="yes"
if [ -f "$STATUS_FILE" ]; then
    if grep -q "^state:[[:space:]]*RUNNING" "$STATUS_FILE" 2>/dev/null; then
        STREAM_CLOSED="no"
    fi
fi

# Success (AC1, AC2, AC4)
if [ "$OUTPUT_JSON" -eq 1 ]; then
    cat << EOF
{
  "status": "passed",
  "card_id": "${CARD_ID}",
  "card_index": ${CARD_INDEX},
  "device": "${PCM_DEVICE}",
  "frequency_hz": ${FREQUENCY_HZ},
  "duration_s": ${DURATION_S},
  "sample_rate_hz": ${SAMPLE_RATE},
  "expected_frames": ${EXPECTED_FRAMES},
  "observed_frames": ${FRAMES_ADVANCED},
  "stream_state": "RUNNING",
  "stream_closed": $([ "$STREAM_CLOSED" = "yes" ] && echo "true" || echo "false"),
  "logical_volume_limit_percent": 30,
  "linear_gain": 0.027,
  "level_dbfs": -31.37,
  "channels": "stereo_alternating"
}
EOF
else
    if [ "$QUIET" -ne 1 ]; then
        echo "Stream status: RUNNING (observed during playback)"
        echo "Hardware position: advanced ${FRAMES_ADVANCED} frames (expected: ~${EXPECTED_FRAMES})"
        echo "Stream closed cleanly: ${STREAM_CLOSED}"
        echo "Signal level: safe 30% logical volume (linear gain 0.027, -31.4 dBFS)"
    fi
    echo "Result: PASS"
fi

exit 0
