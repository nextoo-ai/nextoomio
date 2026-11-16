#!/usr/bin/env python3
"""Automated unit and fixture test suite for nextoomio-tone-check (P0-F6.2, AC1-AC4, D-012).

Executes nextoomio-tone-check.sh against mock procfs environments and mock player
commands to test pass, failure, and edge cases with real execution and assertions (CQ-R2, CQ-R4).
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import signal
import struct
import subprocess
import sys
import tempfile
import time
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
TONE_CHECK_SH = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-tone-check/files/nextoomio-tone-check.sh"
GENERATE_TONE_PY = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-tone-check/files/generate-tone.py"


def create_mock_proc_tree(base_dir: pathlib.Path, card_id: str = "sndrpihifiberry", card_idx: int = 0) -> pathlib.Path:
    """Create a mock procfs tree with /asound/cards and /asound/card<idx>/pcm0p/sub0/."""
    proc_root = base_dir / "proc"
    asound = proc_root / "asound"
    pcm_sub0 = asound / f"card{card_idx}/pcm0p/sub0"
    pcm_sub0.mkdir(parents=True, exist_ok=True)

    cards_file = asound / "cards"
    cards_file.write_text(f" {card_idx} [{card_id}      ]: snd_rpi_hifiberry - snd_rpi_hifiberry_amp\n", encoding="utf-8")

    status_file = pcm_sub0 / "status"
    status_file.write_text("closed\n", encoding="utf-8")
    return proc_root


def create_verified_wav(path: pathlib.Path, gain: float = 0.027) -> None:
    """Create a 2.0s 48kHz stereo WAV file with specified peak linear gain."""
    sample_rate = 48000
    total_frames = 96000
    amplitude = int(round(gain * 32767))
    frames = bytearray()
    half = total_frames // 2
    for i in range(total_frames):
        t = i / sample_rate
        s = int(round(amplitude * math.sin(2.0 * math.pi * 440.0 * t)))
        left = s if i < half else 0
        right = 0 if i < half else s
        frames.extend(struct.pack("<hh", left, right))

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)


def test_safe_tone_generation_and_verification() -> None:
    """AC2 / D-012: Test tone generator and amplitude check."""
    print("Testing safe tone generator and volume bounds (AC2, D-012)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        safe_wav = tmp / "safe.wav"
        loud_wav = tmp / "loud.wav"

        # 1. Generate safe tone via generate-tone.py
        res = subprocess.run([sys.executable, str(GENERATE_TONE_PY), str(safe_wav)], capture_output=True, text=True)
        assert res.returncode == 0, f"generate-tone.py failed: {res.stderr}"
        assert safe_wav.is_file(), "safe.wav not created"

        # 2. Verify safe tone passes check
        res_check = subprocess.run([sys.executable, str(GENERATE_TONE_PY), "--check", str(safe_wav)], capture_output=True, text=True)
        assert res_check.returncode == 0, f"check safe tone failed: {res_check.stdout} {res_check.stderr}"
        assert "OK: peak amplitude" in res_check.stdout, f"Unexpected check output: {res_check.stdout}"

        # 3. Create loud tone (exceeding 30% volume / gain 0.027) and assert check fails
        create_verified_wav(loud_wav, gain=0.50)  # 50% linear gain >> 0.027
        res_loud = subprocess.run([sys.executable, str(GENERATE_TONE_PY), "--check", str(loud_wav)], capture_output=True, text=True)
        assert res_loud.returncode == 1, f"Expected check failure for loud WAV, got {res_loud.returncode}"
        assert "exceeds safe 30% volume limit" in res_loud.stdout, f"Unexpected loud output: {res_loud.stdout}"

    print("  test_safe_tone_generation_and_verification: PASSED")


def test_absent_sound_card_exit_code_2() -> None:
    """AC3: Given a device without the sound card, fails with distinct exit code 2."""
    print("Testing absent sound card handling (AC3)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = tmp / "proc"
        proc_root.mkdir(parents=True, exist_ok=True)
        # Empty cards file (no card present)
        (proc_root / "asound").mkdir(parents=True, exist_ok=True)
        (proc_root / "asound/cards").write_text("", encoding="utf-8")

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        # Standard text output
        cmd = ["/bin/sh", str(TONE_CHECK_SH), "--proc-root", str(proc_root), "--card", "sndrpihifiberry"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 2, f"Expected exit code 2, got {res.returncode}. Output: {res.stdout}\n{res.stderr}"
        assert "sound_card_absent" in res.stderr, f"Missing sound_card_absent in stderr: {res.stderr}"

        # JSON output
        cmd_json = cmd + ["--json"]
        res_j = subprocess.run(cmd_json, capture_output=True, text=True, env=env)
        assert res_j.returncode == 2, f"Expected exit code 2 for JSON, got {res_j.returncode}"
        data = json.loads(res_j.stdout)
        assert data["status"] == "failed"
        assert data["reason"] == "sound_card_absent"
        assert data["card_id"] == "sndrpihifiberry"

    print("  test_absent_sound_card_exit_code_2: PASSED")


def test_busy_device_initial_status_exit_code_3() -> None:
    """AC3: Given a device with playback device busy before playback, fails with code 3."""
    print("Testing initially busy playback device handling (AC3)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)
        status_file = proc_root / "asound/card0/pcm0p/sub0/status"
        # Simulate an already RUNNING stream (e.g. concurrent playback by another daemon)
        status_file.write_text("state: RUNNING\nowner_pid: 12345\nhw_ptr: 5000\n", encoding="utf-8")

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = ["/bin/sh", str(TONE_CHECK_SH), "--proc-root", str(proc_root), "--card", "sndrpihifiberry"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 3, f"Expected exit code 3, got {res.returncode}. Output: {res.stdout}\n{res.stderr}"
        assert "playback_device_busy" in res.stderr, f"Missing playback_device_busy in stderr: {res.stderr}"

        # JSON output
        cmd_json = cmd + ["--json"]
        res_j = subprocess.run(cmd_json, capture_output=True, text=True, env=env)
        assert res_j.returncode == 3, f"Expected exit code 3 for JSON, got {res_j.returncode}"
        data = json.loads(res_j.stdout)
        assert data["status"] == "failed"
        assert data["reason"] == "playback_device_busy"

    print("  test_busy_device_initial_status_exit_code_3: PASSED")


def test_busy_device_player_error_exit_code_3() -> None:
    """AC3: Given a player error reporting busy, fails with distinct exit code 3."""
    print("Testing player busy error handling (AC3)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)

        # Mock player that fails with EBUSY error
        mock_player = tmp / "mock_aplay_busy.sh"
        mock_player.write_text(
            '#!/bin/sh\n'
            'echo "aplay: main:831: audio open error: Device or resource busy" >&2\n'
            'exit 1\n',
            encoding="utf-8"
        )
        mock_player.chmod(0o755)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 3, f"Expected exit code 3, got {res.returncode}. Output: {res.stdout}\n{res.stderr}"
        assert "playback_device_busy" in res.stderr, f"Expected playback_device_busy: {res.stderr}"

        # JSON output
        res_j = subprocess.run(cmd + ["--json"], capture_output=True, text=True, env=env)
        assert res_j.returncode == 3
        data = json.loads(res_j.stdout)
        assert data["status"] == "failed"
        assert data["reason"] == "playback_device_busy"

    print("  test_busy_device_player_error_exit_code_3: PASSED")


def test_full_length_fast_polled_run_passing() -> None:
    """AC1: Full-length fast-polled run passing (Reviewer Finding 3).

    Verifies that a player simulating complete playback over 0.35s with hw_ptr advancing
    from 0 to 96,000 frames is sampled by the 0.1s poll loop, observes RUNNING,
    and passes with exit code 0.
    """
    print("Testing full-length fast-polled playback pass (AC1, Reviewer Finding 3)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)
        status_file = proc_root / "asound/card0/pcm0p/sub0/status"

        # Mock player simulating hardware frame advancement
        mock_player = tmp / "mock_player_pass.py"
        mock_player.write_text(f"""#!/usr/bin/env python3
import time, pathlib

status_path = pathlib.Path("{status_file}")

# Transition to RUNNING state and advance hw_ptr progressively to 96,000 frames
frames = [0, 20000, 40000, 60000, 80000, 96000]
for f in frames:
    status_path.write_text(f"state: RUNNING\\nowner_pid: 100\\nhw_ptr: {{f}}\\n", encoding="utf-8")
    time.sleep(0.12)

# Hold final frames briefly as snd_pcm_drain completes before stream close
time.sleep(0.15)
status_path.write_text("closed\\n", encoding="utf-8")
""", encoding="utf-8")
        mock_player.chmod(0o755)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 0, f"Expected exit code 0, got {res.returncode}. Output:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        assert "Stream status: RUNNING" in res.stdout, f"Missing RUNNING in stdout: {res.stdout}"
        assert "Hardware position: advanced" in res.stdout, f"Missing position advanced in stdout: {res.stdout}"
        assert "Stream closed cleanly: yes" in res.stdout, f"Missing closed in stdout: {res.stdout}"
        assert "Result: PASS" in res.stdout, f"Missing Result: PASS in stdout: {res.stdout}"

        # JSON output
        res_j = subprocess.run(cmd + ["--json"], capture_output=True, text=True, env=env)
        assert res_j.returncode == 0, f"JSON run failed with code {res_j.returncode}: {res_j.stderr}"
        data = json.loads(res_j.stdout)
        assert data["status"] == "passed"
        assert data["stream_state"] == "RUNNING"
        assert data["stream_closed"] is True
        assert data["expected_frames"] == 96000
        assert data["observed_frames"] >= 86400
        assert data["logical_volume_limit_percent"] == 30

    print("  test_full_length_fast_polled_run_passing: PASSED")


def test_stalled_hw_ptr_fails() -> None:
    """AC1: Stalled hw_ptr fails with exit code 4 (Reviewer Finding 3).

    Verifies that if playback stream reaches RUNNING but hardware clock is stalled
    (hw_ptr does not advance expected frames), the check fails with code 4.
    """
    print("Testing stalled hw_ptr failure (AC1, Reviewer Finding 3)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)
        status_file = proc_root / "asound/card0/pcm0p/sub0/status"

        # Mock player where status stays at hw_ptr: 0 (stalled I2S clock / DMA)
        mock_player = tmp / "mock_player_stalled.py"
        mock_player.write_text(f"""#!/usr/bin/env python3
import time, pathlib

status_path = pathlib.Path("{status_file}")
status_path.write_text("state: RUNNING\\nowner_pid: 200\\nhw_ptr: 0\\n", encoding="utf-8")
time.sleep(0.3)
status_path.write_text("closed\\n", encoding="utf-8")
""", encoding="utf-8")
        mock_player.chmod(0o755)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 4, f"Expected exit code 4 for stalled stream, got {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
        assert "stream_position_not_advancing" in res.stderr, f"Missing stream_position_not_advancing: {res.stderr}"

        # JSON output
        res_j = subprocess.run(cmd + ["--json"], capture_output=True, text=True, env=env)
        assert res_j.returncode == 4
        data = json.loads(res_j.stdout)
        assert data["status"] == "failed"
        assert data["reason"] == "stream_position_not_advancing"

    print("  test_stalled_hw_ptr_fails: PASSED")


def test_stream_never_reaches_running_fails() -> None:
    """AC1: Stream that never enters RUNNING state fails with exit code 4."""
    print("Testing stream never reaching RUNNING failure (AC1)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)
        status_file = proc_root / "asound/card0/pcm0p/sub0/status"

        mock_player = tmp / "mock_player_xrun.py"
        mock_player.write_text(f"""#!/usr/bin/env python3
import time, pathlib

status_path = pathlib.Path("{status_file}")
status_path.write_text("state: XRUN\\nowner_pid: 300\\nhw_ptr: 0\\n", encoding="utf-8")
time.sleep(0.25)
status_path.write_text("closed\\n", encoding="utf-8")
""", encoding="utf-8")
        mock_player.chmod(0o755)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 4, f"Expected exit code 4, got {res.returncode}"
        assert "stream_position_not_advancing" in res.stderr

    print("  test_stream_never_reaches_running_fails: PASSED")


def test_signal_cleanup_terminates_player_and_stream() -> None:
    """AC4: Signal/exit trap guarantees player is terminated and no stream is left open."""
    print("Testing signal trap termination and cleanup (AC4)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp, card_id="sndrpihifiberry", card_idx=0)
        status_file = proc_root / "asound/card0/pcm0p/sub0/status"
        pid_file = tmp / "player.pid"

        # Long-running mock player
        mock_player = tmp / "mock_player_sleep.sh"
        mock_player.write_text(
            f'#!/bin/sh\n'
            f'echo $$ > "{pid_file}"\n'
            f'echo "state: RUNNING\\nowner_pid: $$" > "{status_file}"\n'
            f'exec sleep 30\n',
            encoding="utf-8"
        )
        mock_player.chmod(0o755)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        cmd = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player),
        ]

        # Test SIGTERM
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Wait for player to record PID
        for _ in range(50):
            if pid_file.is_file() and pid_file.stat().st_size > 0:
                break
            time.sleep(0.05)
        assert pid_file.is_file(), "Player PID file was not created"
        player_pid = int(pid_file.read_text(encoding="utf-8").strip())

        # Verify player process is alive
        try:
            os.kill(player_pid, 0)
        except OSError:
            assert False, f"Player process {player_pid} was not running"

        # Deliver SIGTERM to nextoomio-tone-check
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)

        # Check tone check exit code reflects SIGTERM (128 + 15 = 143)
        assert proc.returncode == 143, f"Expected exit code 143, got {proc.returncode}"

        # Verify background player was killed cleanly by trap (AC4)
        time.sleep(0.1)
        player_alive = True
        try:
            os.kill(player_pid, 0)
        except OSError:
            player_alive = False
        assert not player_alive, f"Player process {player_pid} was left running after SIGTERM (AC4 violation)"

    print("  test_signal_cleanup_terminates_player_and_stream: PASSED")


def test_card_id_validation_and_json_safety() -> None:
    """Finding 5 (CQ-R7): Card ID validation and JSON injection safety."""
    print("Testing card ID validation and JSON escaping (Finding 5)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp)

        safe_wav = tmp / "safe.wav"
        create_verified_wav(safe_wav)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(safe_wav)

        # 1. Reject invalid card ID with special characters
        cmd_bad_card = ["/bin/sh", str(TONE_CHECK_SH), "--card", "bad;id$test"]
        res = subprocess.run(cmd_bad_card, capture_output=True, text=True, env=env)
        assert res.returncode == 1, f"Expected exit code 1 for invalid card, got {res.returncode}"
        assert "Invalid card ID" in res.stderr

        # 2. Mock player emitting quotes and special characters in stderr
        mock_player_quotes = tmp / "mock_quotes.sh"
        mock_player_quotes.write_text(
            '#!/bin/sh\n'
            'echo \'aplay: "critical error" with {json: true} and "quotes"\' >&2\n'
            'exit 1\n',
            encoding="utf-8"
        )
        mock_player_quotes.chmod(0o755)

        cmd_quotes = [
            "/bin/sh", str(TONE_CHECK_SH),
            "--proc-root", str(proc_root),
            "--card", "sndrpihifiberry",
            "--play-cmd", str(mock_player_quotes),
            "--json",
        ]
        res_q = subprocess.run(cmd_quotes, capture_output=True, text=True, env=env)
        assert res_q.returncode == 1, f"Expected exit 1, got {res_q.returncode}"
        # Assert stdout parses cleanly as valid JSON (not broken by quotes in stderr)
        data = json.loads(res_q.stdout)
        assert data["status"] == "failed"
        assert data["reason"] == "playback_failed"
        assert 'critical error' in data["message"]

    print("  test_card_id_validation_and_json_safety: PASSED")


def test_unverified_custom_tone_file_rejected() -> None:
    """Finding 4: Unverified or loud tone file is rejected before playback."""
    print("Testing rejection of unverified custom tone file (Finding 4, AC2)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        proc_root = create_mock_proc_tree(tmp)

        loud_wav = tmp / "loud.wav"
        create_verified_wav(loud_wav, gain=0.60)

        env = os.environ.copy()
        env["NEXTOOMIO_TONE_FILE"] = str(loud_wav)

        cmd = ["/bin/sh", str(TONE_CHECK_SH), "--proc-root", str(proc_root), "--card", "sndrpihifiberry"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert res.returncode == 1, f"Expected exit code 1 for loud tone, got {res.returncode}"
        assert "failed safe 30% volume check" in res.stderr

    print("  test_unverified_custom_tone_file_rejected: PASSED")


def main() -> int:
    print(f"=== Running nextoomio-tone-check executable fixture tests ===")
    test_safe_tone_generation_and_verification()
    test_absent_sound_card_exit_code_2()
    test_busy_device_initial_status_exit_code_3()
    test_busy_device_player_error_exit_code_3()
    test_full_length_fast_polled_run_passing()
    test_stalled_hw_ptr_fails()
    test_stream_never_reaches_running_fails()
    test_signal_cleanup_terminates_player_and_stream()
    test_card_id_validation_and_json_safety()
    test_unverified_custom_tone_file_rejected()
    print("=== All nextoomio-tone-check executable fixture tests passed successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
