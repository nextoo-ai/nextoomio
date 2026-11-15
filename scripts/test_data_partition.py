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
        """Verify all recipe shell scripts have valid syntax via sh -n."""
        for script_name in (
            "nextoomio-boot-mount.sh",
            "nextoomio-data-grow.sh",
            "nextoomio-data-check.sh",
            "nextoomio-data-init.sh",
        ):
            script_path = DATA_RECIPE_FILES / script_name
            self.assertTrue(script_path.is_file(), f"Missing {script_name}")
            res = subprocess.run(["sh", "-n", str(script_path)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Syntax error in {script_name}: {res.stderr}")

    def test_boot_mount_active_slot_resolution(self) -> None:
        """Verify AC1: nextoomio-boot-mount.sh dynamically resolves active slot A or B."""
        script_path = DATA_RECIPE_FILES / "nextoomio-boot-mount.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            boot_dir = tmp_path / "boot"
            cmdline_file = tmp_path / "cmdline"

            # Case 1: Slot A active via cmdline
            cmdline_file.write_text("console=serial0,115200 root=/dev/mmcblk0p5 rootfstype=ext4 root_a ro", encoding="utf-8")
            env_a = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_BOOT_DIR": str(boot_dir),
                "NEXTOOMIO_CMDLINE_FILE": str(cmdline_file),
                "NEXTOOMIO_MOCK_MOUNT": "1",
            }
            res_a = subprocess.run(["sh", str(script_path)], env=env_a, capture_output=True, text=True)
            self.assertEqual(res_a.returncode, 0, f"boot-mount failed for slot A: {res_a.stderr}")
            info_a = (boot_dir / ".mounted_info").read_text(encoding="utf-8")
            self.assertIn("slot=a", info_a)
            self.assertIn("boot_a", info_a)

            # Case 2: Slot B active via cmdline
            cmdline_file.write_text("console=serial0,115200 root=/dev/mmcblk0p6 rootfstype=ext4 root_b ro", encoding="utf-8")
            env_b = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_BOOT_DIR": str(boot_dir),
                "NEXTOOMIO_CMDLINE_FILE": str(cmdline_file),
                "NEXTOOMIO_MOCK_MOUNT": "1",
            }
            res_b = subprocess.run(["sh", str(script_path)], env=env_b, capture_output=True, text=True)
            self.assertEqual(res_b.returncode, 0, f"boot-mount failed for slot B: {res_b.stderr}")
            info_b = (boot_dir / ".mounted_info").read_text(encoding="utf-8")
            self.assertIn("slot=b", info_b)
            self.assertIn("boot_b", info_b)

    def test_grow_script_idempotency(self) -> None:
        """Verify AC2: nextoomio-data-grow.sh detects existing resize marker and exits without re-running."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-grow.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            (fake_data / ".resized").touch()

            env = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Grow script failed: {res.stderr}")
            self.assertIn("resize marker found", res.stdout)

    def test_check_script_e2fsck_exit_codes(self) -> None:
        """Verify AC3 and AC5: nextoomio-data-check.sh handles clean, repaired, and corrupt file systems."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-check.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_run = tmp_path / "run" / "nextoomio"
            fake_dev = tmp_path / "mock_dev"
            fake_dev.touch()

            # Helper to create mock e2fsck script
            mock_bin_dir = tmp_path / "bin"
            mock_bin_dir.mkdir(parents=True)
            mock_e2fsck = mock_bin_dir / "mock_e2fsck.sh"

            # Sub-test 1: e2fsck returns 0 (clean) -> data_available
            mock_e2fsck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            mock_e2fsck.chmod(0o755)

            env_clean = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_DATA_DEV": str(fake_dev),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_E2FSCK_CMD": str(mock_e2fsck),
            }
            res_clean = subprocess.run(["sh", str(script_path)], env=env_clean, capture_output=True, text=True)
            self.assertEqual(res_clean.returncode, 0)
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_available")
            self.assertFalse((fake_run / "data_unavailable").exists())

            # Sub-test 2: e2fsck returns 1 (repaired) -> data_available
            mock_e2fsck.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            res_repaired = subprocess.run(["sh", str(script_path)], env=env_clean, capture_output=True, text=True)
            self.assertEqual(res_repaired.returncode, 0)
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_available")
            self.assertFalse((fake_run / "data_unavailable").exists())

            # Sub-test 3: e2fsck returns 4 (uncorrectable errors) -> data_unavailable, exit 0 (prevent emergency mode)
            mock_e2fsck.write_text("#!/bin/sh\nexit 4\n", encoding="utf-8")
            res_corrupt = subprocess.run(["sh", str(script_path)], env=env_clean, capture_output=True, text=True)
            self.assertEqual(res_corrupt.returncode, 0, "Script must exit 0 to avoid systemd emergency mode")
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_unavailable")
            self.assertTrue((fake_run / "data_unavailable").exists())

    def test_init_script_directory_structure_and_modes(self) -> None:
        """Verify AC4: nextoomio-data-init.sh creates directories with correct modes and secrets is not world-readable."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            env = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
                "NEXTOOMIO_TEST_MOUNT": "1",
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
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

    def test_init_script_recovery_mode(self) -> None:
        """Verify AC5: nextoomio-data-init.sh in recovery mode provides volatile dirs and preserves flag."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            # Simulate recovery mode flag from pre-mount check
            (fake_run / "data_unavailable").touch()

            env = {
                "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
                "NEXTOOMIO_TEST_MOUNT": "1",
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Init script in recovery mode failed: {res.stderr}")
            self.assertTrue((fake_run / "data_unavailable").exists())
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_unavailable")
            self.assertTrue((fake_data / "config").is_dir())
            self.assertTrue((fake_data / "secrets").is_dir())

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
