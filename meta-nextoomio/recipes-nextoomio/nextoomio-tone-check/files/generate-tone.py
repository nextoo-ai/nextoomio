#!/usr/bin/env python3
"""Generate and verify the quiet test tone for nextoomio-tone-check (P0-F6.2, D-012).

Generates a 2.0-second 48 kHz stereo WAV file with a 440 Hz tone.
Enforces the safe level constraint: signal level must not exceed the level
corresponding to the default 30% logical volume (AC2, D-012).
Under the shared cubic volume curve (SPECIFICATION.md section 3,
ARCHITECTURE.md section 3.2), 30% volume corresponds to linear gain (0.3)^3 = 0.027
(-31.37 dBFS). For 16-bit signed PCM, peak amplitude is limited to <= 885.

Channels are played in turn:
- 0.0s to 1.0s: Left channel active, Right channel silent.
- 1.0s to 2.0s: Left channel silent, Right channel active.
This allows verifying stereo output path and supports listening check (P0-F6.3).
"""

from __future__ import annotations

import math
import struct
import sys
import wave

SAMPLE_RATE_HZ = 48000
CHANNELS = 2
DURATION_S = 2.0
TOTAL_FRAMES = int(SAMPLE_RATE_HZ * DURATION_S)  # 96,000 frames
FREQUENCY_HZ = 440.0
MAX_LOGICAL_VOLUME_PCT = 30.0
# Cubic volume curve: (V / 100)^3
MAX_LINEAR_GAIN = (MAX_LOGICAL_VOLUME_PCT / 100.0) ** 3  # 0.02700
MAX_PEAK_AMPLITUDE_16BIT = int(round(MAX_LINEAR_GAIN * 32767))  # 885


def generate_tone(out_path: str) -> None:
    """Generate the reference test tone WAV file."""
    half_frames = TOTAL_FRAMES // 2
    frames = bytearray()

    for i in range(TOTAL_FRAMES):
        t = i / SAMPLE_RATE_HZ
        # 440 Hz sine wave with amplitude <= 30% logical volume
        sample = int(round(MAX_PEAK_AMPLITUDE_16BIT * math.sin(2.0 * math.pi * FREQUENCY_HZ * t)))
        if i < half_frames:
            # Channel 0 (Left) active, Channel 1 (Right) silent
            left = sample
            right = 0
        else:
            # Channel 0 (Left) silent, Channel 1 (Right) active
            left = 0
            right = sample
        frames.extend(struct.pack("<hh", left, right))

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit signed PCM
        wf.setframerate(SAMPLE_RATE_HZ)
        wf.writeframes(frames)


def verify_tone(wav_path: str) -> tuple[bool, str]:
    """Verify that a WAV file conforms to AC1, AC2, and D-012 constraints."""
    try:
        with wave.open(wav_path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            data = wf.readframes(nframes)
    except Exception as e:
        return False, f"Cannot read WAV file '{wav_path}': {e}"

    if channels != CHANNELS:
        return False, f"Expected {CHANNELS} channels, found {channels}"
    if sampwidth != 2:
        return False, f"Expected 16-bit samples (sampwidth=2), found {sampwidth}"
    if framerate != SAMPLE_RATE_HZ:
        return False, f"Expected {SAMPLE_RATE_HZ} Hz sample rate, found {framerate}"
    if nframes != TOTAL_FRAMES:
        return False, f"Expected {TOTAL_FRAMES} frames ({DURATION_S}s), found {nframes}"

    # Verify peak amplitude does not exceed 30% volume limit (AC2, D-012)
    max_val = 0
    num_samples = nframes * channels
    samples = struct.unpack(f"<{num_samples}h", data)
    for s in samples:
        if abs(s) > max_val:
            max_val = abs(s)

    linear_gain = max_val / 32767.0
    if max_val > MAX_PEAK_AMPLITUDE_16BIT:
        return False, (
            f"Peak amplitude {max_val} exceeds safe 30% volume limit {MAX_PEAK_AMPLITUDE_16BIT} "
            f"(linear gain {linear_gain:.4f} > {MAX_LINEAR_GAIN:.4f})"
        )

    return True, f"OK: peak amplitude {max_val} <= {MAX_PEAK_AMPLITUDE_16BIT} (gain {linear_gain:.4f})"


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output.wav> | --check <file.wav>", file=sys.stderr)
        return 1

    if sys.argv[1] == "--check":
        if len(sys.argv) != 3:
            print(f"Usage: {sys.argv[0]} --check <file.wav>", file=sys.stderr)
            return 1
        ok, msg = verify_tone(sys.argv[2])
        print(msg)
        return 0 if ok else 1

    out_path = sys.argv[1]
    generate_tone(out_path)
    ok, msg = verify_tone(out_path)
    if not ok:
        print(f"ERROR verifying generated tone: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
