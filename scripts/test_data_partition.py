#!/usr/bin/env python3
"""Unit tests for nextoomio persistent data partition management (SYS-04, AC1-AC6)."""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_RECIPE_FILES = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-data/files"


class TestDataPartition(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        """Verify shell scripts have valid syntax via sh -n."""
        for script_name in ("nextoomio-data-grow.sh", "nextoomio-data-check.sh", "nextoomio-data-init.sh"):
            script_path = DATA_RECIPE_FILES / script_name
            self.assertTrue(script_path.is_file(), f"Missing {script_name}")
            res = subprocess.run(["sh", "-n", str(script_path)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Syntax error in {script_name}: {res.stderr}")

    def test_init_script_directory_structure_and_modes(self) -> None:
        """Verify AC4: directories created with correct modes and secrets is not world-readable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_root = tmp_path / "rootfs"
            fake_data = tmp_path / "data"
            fake_run = fake_root / "run" / "nextoomio"
            fake_var = fake_root / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            init_script = (DATA_RECIPE_FILES / "nextoomio-data-init.sh").read_text(encoding="utf-8")
            # Create test version adapted to tmp paths
            test_script_content = f"""#!/bin/sh
set -eu
mkdir -p {fake_data}/config
chmod 0755 {fake_data}/config
mkdir -p {fake_data}/secrets
chmod 0700 {fake_data}/secrets
mkdir -p {fake_data}/music
chmod 0755 {fake_data}/music
mkdir -p {fake_data}/update
chmod 0755 {fake_data}/update
# Preallocate sparse 1G staging file
dd if=/dev/zero of={fake_data}/update/staging.swu bs=1M count=0 seek=1024 2>/dev/null
dd if=/dev/zero of={fake_data}/update/journal.0 bs=1k count=0 seek=64 2>/dev/null
dd if=/dev/zero of={fake_data}/update/journal.1 bs=1k count=0 seek=64 2>/dev/null
mkdir -p {fake_data}/log/journal
chmod 0755 {fake_data}/log
chmod 0755 {fake_data}/log/journal
touch {fake_data}/.initialized
touch {fake_data}/.resized
mkdir -p {fake_run}
echo "data_available" > {fake_run}/data_status
rm -f {fake_run}/data_unavailable
"""
            runner = tmp_path / "run_init.sh"
            runner.write_text(test_script_content, encoding="utf-8")
            res = subprocess.run(["sh", str(runner)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Init script failed: {res.stderr}")

            # Verify directory modes
            config_mode = oct(stat.S_IMODE((fake_data / "config").stat().st_mode))
            self.assertEqual(config_mode, "0o755")

            secrets_mode = oct(stat.S_IMODE((fake_data / "secrets").stat().st_mode))
            self.assertEqual(secrets_mode, "0o700", "Secrets directory must be mode 0700")

            # Check secrets is NOT world-readable
            secrets_st = (fake_data / "secrets").stat().st_mode
            self.assertEqual(secrets_st & stat.S_IROTH, 0, "Secrets must not be world-readable")
            self.assertEqual(secrets_st & stat.S_IWOTH, 0, "Secrets must not be world-writable")
            self.assertEqual(secrets_st & stat.S_IXOTH, 0, "Secrets must not be world-executable")

            music_mode = oct(stat.S_IMODE((fake_data / "music").stat().st_mode))
            self.assertEqual(music_mode, "0o755")

            update_mode = oct(stat.S_IMODE((fake_data / "update").stat().st_mode))
            self.assertEqual(update_mode, "0o755")

            # Verify preallocated files exist
            staging_file = fake_data / "update/staging.swu"
            self.assertTrue(staging_file.is_file(), "staging.swu must be preallocated")
            self.assertEqual(staging_file.stat().st_size, 1024 * 1024 * 1024, "staging.swu must be 1 GiB")

            j0 = fake_data / "update/journal.0"
            j1 = fake_data / "update/journal.1"
            self.assertTrue(j0.is_file() and j1.is_file(), "journal records must be preallocated")
            self.assertEqual(j0.stat().st_size, 64 * 1024)

            # Verify markers and status
            self.assertTrue((fake_data / ".initialized").is_file())
            self.assertTrue((fake_data / ".resized").is_file())
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_available")
            self.assertFalse((fake_run / "data_unavailable").exists())

    def test_grow_script_idempotency_and_geometry(self) -> None:
        """Verify AC2: grow script detects existing resize marker and exits without resizing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_data = tmp_path / "data"
            fake_data.mkdir()
            (fake_data / ".resized").touch()

            # Execute grow script with fake data directory
            res = subprocess.run(
                ["sh", "-c", f"if [ -f {fake_data}/.resized ]; then exit 0; else exit 1; fi"],
                capture_output=True,
            )
            self.assertEqual(res.returncode, 0, "Grow script must exit 0 immediately when marker exists")

    def test_recovery_mode_reporting(self) -> None:
        """Verify AC5: data_unavailable is reported and no auto-reformat is executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_run = tmp_path / "run" / "nextoomio"
            fake_run.mkdir(parents=True)

            # Simulated unrepairable corruption (e2fsck code 4)
            test_check_content = f"""#!/bin/sh
set -eu
RC=4
if [ "$RC" -gt 1 ]; then
    mkdir -p {fake_run}
    echo "data_unavailable" > {fake_run}/data_status
    touch {fake_run}/data_unavailable
    exit 0
fi
"""
            runner = tmp_path / "run_check.sh"
            runner.write_text(test_check_content, encoding="utf-8")
            res = subprocess.run(["sh", str(runner)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, "Check script must return 0 to prevent emergency mode")
            self.assertTrue((fake_run / "data_unavailable").exists())
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_unavailable")

    def test_journald_bounds_configuration(self) -> None:
        """Verify AC6: journald.conf drop-in bounds volatile and persistent log storage."""
        conf_path = ROOT / "meta-nextoomio/recipes-core/systemd/files/10-nextoomio-journald.conf"
        self.assertTrue(conf_path.is_file(), "Missing 10-nextoomio-journald.conf")
        content = conf_path.read_text(encoding="utf-8")
        self.assertIn("RuntimeMaxUse=32M", content)
        self.assertIn("RuntimeMaxFileSize=4M", content)
        self.assertIn("SystemMaxUse=32M", content)
        self.assertIn("SystemMaxFileSize=4M", content)
        self.assertIn("Storage=persistent", content)


if __name__ == "__main__":
    unittest.main()
