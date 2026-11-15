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


def make_stub(bin_dir: pathlib.Path, name: str, script_body: str) -> pathlib.Path:
    p = bin_dir / name
    p.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


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
        """Verify AC1: nextoomio-boot-mount.sh dynamically resolves active slot A or B and mounts ro."""
        script_path = DATA_RECIPE_FILES / "nextoomio-boot-mount.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            mount_log = tmp_path / "mount.log"

            # Create stubs for mount and mountpoint on PATH (no test hooks in shipped scripts - CQ-R14)
            make_stub(
                bin_dir,
                "mount",
                f'echo "$@" >> "{mount_log}"\nexit 0',
            )
            make_stub(
                bin_dir,
                "mountpoint",
                "exit 1",  # Not yet mounted
            )

            boot_dir = tmp_path / "boot"
            cmdline_file = tmp_path / "cmdline"
            boot_dev_a = tmp_path / "boot_a_dev"
            boot_dev_b = tmp_path / "boot_b_dev"
            boot_dev_a.touch()
            boot_dev_b.touch()

            # Case 1: Slot A active via cmdline
            cmdline_file.write_text("console=serial0,115200 root=/dev/mmcblk0p5 rootfstype=ext4 root_a ro", encoding="utf-8")
            env_a = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_BOOT_DIR": str(boot_dir),
                "NEXTOOMIO_CMDLINE_FILE": str(cmdline_file),
                "NEXTOOMIO_BOOT_DEV_A": str(boot_dev_a),
                "NEXTOOMIO_BOOT_DEV_B": str(boot_dev_b),
            }
            res_a = subprocess.run(["sh", str(script_path)], env=env_a, capture_output=True, text=True)
            self.assertEqual(res_a.returncode, 0, f"boot-mount failed for slot A: {res_a.stderr}")
            log_a = mount_log.read_text(encoding="utf-8")
            self.assertIn(str(boot_dev_a), log_a)
            self.assertIn("ro,defaults,nofail", log_a)
            self.assertIn("-t vfat", log_a)

            # Case 2: Slot B active via cmdline
            mount_log.unlink()
            cmdline_file.write_text("console=serial0,115200 root=/dev/mmcblk0p6 rootfstype=ext4 root_b ro", encoding="utf-8")
            env_b = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_BOOT_DIR": str(boot_dir),
                "NEXTOOMIO_CMDLINE_FILE": str(cmdline_file),
                "NEXTOOMIO_BOOT_DEV_A": str(boot_dev_a),
                "NEXTOOMIO_BOOT_DEV_B": str(boot_dev_b),
            }
            res_b = subprocess.run(["sh", str(script_path)], env=env_b, capture_output=True, text=True)
            self.assertEqual(res_b.returncode, 0, f"boot-mount failed for slot B: {res_b.stderr}")
            log_b = mount_log.read_text(encoding="utf-8")
            self.assertIn(str(boot_dev_b), log_b)
            self.assertIn("ro,defaults,nofail", log_b)

    def test_grow_script_first_boot_expansion(self) -> None:
        """Verify AC2: first boot expands extended and data partitions to end of disk via parted and resize2fs."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-grow.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            parted_log = tmp_path / "parted.log"
            resize2fs_log = tmp_path / "resize2fs.log"

            make_stub(bin_dir, "parted", f'echo "$@" >> "{parted_log}"\nexit 0')
            make_stub(bin_dir, "resize2fs", f'echo "$@" >> "{resize2fs_log}"\nexit 0')
            make_stub(bin_dir, "partx", "exit 0")

            fake_run = tmp_path / "run" / "nextoomio"
            fake_sys = tmp_path / "sys" / "class" / "block"

            # Setup sysfs geometry: disk has 31250000 sectors (16 GB), partition ends at 11145728 (free > 8192)
            disk_dir = fake_sys / "mmcblk0"
            part_dir = fake_sys / "mmcblk0p7"
            ext_dir = fake_sys / "mmcblk0p4"
            disk_dir.mkdir(parents=True)
            part_dir.mkdir(parents=True)
            ext_dir.mkdir(parents=True)

            (disk_dir / "size").write_text("31250000\n", encoding="utf-8")
            (part_dir / "start").write_text("8000000\n", encoding="utf-8")
            (part_dir / "size").write_text("3145728\n", encoding="utf-8")  # 1.5 GiB

            fake_dev = tmp_path / "dev" / "mmcblk0p7"
            fake_disk = tmp_path / "dev" / "mmcblk0"
            fake_dev.parent.mkdir(parents=True)
            fake_dev.touch()
            fake_disk.touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DEV": str(fake_dev),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_SYS_BLOCK_DIR": str(fake_sys),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Grow script failed: {res.stderr}")
            self.assertIn("expansion completed successfully", res.stdout)

            # Verify parted was called to resize extended partition 4 and logical partition 7 to 100%
            parted_calls = parted_log.read_text(encoding="utf-8")
            self.assertIn("resizepart 4 100%", parted_calls)
            self.assertIn("resizepart 7 100%", parted_calls)

            # Verify resize2fs was called on data device
            resize_calls = resize2fs_log.read_text(encoding="utf-8")
            self.assertIn(str(fake_dev), resize_calls)
            self.assertTrue((fake_run / "data-resized").exists())

    def test_grow_script_second_boot_idempotency(self) -> None:
        """Verify AC2: second boot detects partition already extends to end of disk and skips resize."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-grow.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            parted_log = tmp_path / "parted.log"
            resize2fs_log = tmp_path / "resize2fs.log"

            make_stub(bin_dir, "parted", f'echo "$@" >> "{parted_log}"\nexit 0')
            make_stub(bin_dir, "resize2fs", f'echo "$@" >> "{resize2fs_log}"\nexit 0')
            make_stub(bin_dir, "partx", "exit 0")
            # Filesystem block count matches partition capacity (23250000 sectors * 512 / 4096 = 2906250 blocks)
            make_stub(
                bin_dir,
                "dumpe2fs",
                'echo "Block count: 2906250"; echo "Block size: 4096"',
            )

            fake_run = tmp_path / "run" / "nextoomio"
            fake_sys = tmp_path / "sys" / "class" / "block"

            # Setup sysfs geometry: partition already fills disk (free sectors = 0 <= 8192)
            disk_dir = fake_sys / "mmcblk0"
            part_dir = fake_sys / "mmcblk0p7"
            disk_dir.mkdir(parents=True)
            part_dir.mkdir(parents=True)

            (disk_dir / "size").write_text("31250000\n", encoding="utf-8")
            (part_dir / "start").write_text("8000000\n", encoding="utf-8")
            (part_dir / "size").write_text("23250000\n", encoding="utf-8")  # start + size = 31250000

            fake_dev = tmp_path / "dev" / "mmcblk0p7"
            fake_disk = tmp_path / "dev" / "mmcblk0"
            fake_dev.parent.mkdir(parents=True)
            fake_dev.touch()
            fake_disk.touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DEV": str(fake_dev),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_SYS_BLOCK_DIR": str(fake_sys),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Grow script failed: {res.stderr}")
            self.assertIn("already extend to end of disk", res.stdout)
            self.assertIn("skipping expansion", res.stdout)

            # Neither parted nor resize2fs should be called
            self.assertFalse(parted_log.exists(), "parted must not be called when partition already extends to disk end")
            self.assertFalse(resize2fs_log.exists(), "resize2fs must not be called when filesystem already fills partition")
            self.assertTrue((fake_run / "data-resized").exists())

    def test_grow_script_partition_already_grown_filesystem_not(self) -> None:
        """Verify AC2 & CQ-R1: nextoomio-data-grow.sh resizes ext4 when partition is already grown but fs is not."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-grow.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            parted_log = tmp_path / "parted.log"
            resize2fs_log = tmp_path / "resize2fs.log"

            make_stub(bin_dir, "parted", f'echo "$@" >> "{parted_log}"\nexit 0')
            make_stub(bin_dir, "resize2fs", f'echo "$@" >> "{resize2fs_log}"\nexit 0')
            make_stub(bin_dir, "partx", "exit 0")
            # Filesystem size reports initial wic size (1.5 GiB = 393216 blocks of 4096)
            make_stub(
                bin_dir,
                "dumpe2fs",
                'echo "Block count: 393216"; echo "Block size: 4096"',
            )

            fake_run = tmp_path / "run" / "nextoomio"
            fake_sys = tmp_path / "sys" / "class" / "block"

            # Setup sysfs geometry: partition already fills disk (free sectors = 0 <= 8192)
            disk_dir = fake_sys / "mmcblk0"
            part_dir = fake_sys / "mmcblk0p7"
            disk_dir.mkdir(parents=True)
            part_dir.mkdir(parents=True)

            (disk_dir / "size").write_text("31250000\n", encoding="utf-8")
            (part_dir / "start").write_text("8000000\n", encoding="utf-8")
            (part_dir / "size").write_text("23250000\n", encoding="utf-8")  # 11.9 GiB partition

            fake_dev = tmp_path / "dev" / "mmcblk0p7"
            fake_disk = tmp_path / "dev" / "mmcblk0"
            fake_dev.parent.mkdir(parents=True)
            fake_dev.touch()
            fake_disk.touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DEV": str(fake_dev),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_SYS_BLOCK_DIR": str(fake_sys),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Grow script failed: {res.stderr}")
            self.assertIn("does not fill partition", res.stdout)
            self.assertIn("Resizing filesystem", res.stdout)

            # parted must NOT be called since partition geometry already spans to end
            self.assertFalse(parted_log.exists(), "parted must not be called when partition is already expanded")

            # resize2fs MUST be called because filesystem is smaller than partition
            self.assertTrue(resize2fs_log.exists(), "resize2fs must be called when filesystem does not fill partition")
            resize_calls = resize2fs_log.read_text(encoding="utf-8")
            self.assertIn(str(fake_dev), resize_calls)
            self.assertTrue((fake_run / "data-resized").exists())

    def test_grow_script_surfaces_failures(self) -> None:
        """Verify CQ-R2 / CQ-R4: nextoomio-data-grow.sh surfaces parted and resize2fs failures visibly."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-grow.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()

            # Stub parted to fail with code 1
            make_stub(bin_dir, "parted", 'echo "parted simulated error" >&2\nexit 1')
            make_stub(bin_dir, "resize2fs", "exit 0")
            make_stub(bin_dir, "partx", "exit 0")

            fake_sys = tmp_path / "sys" / "class" / "block"
            disk_dir = fake_sys / "mmcblk0"
            part_dir = fake_sys / "mmcblk0p7"
            disk_dir.mkdir(parents=True)
            part_dir.mkdir(parents=True)
            (disk_dir / "size").write_text("31250000\n", encoding="utf-8")
            (part_dir / "start").write_text("8000000\n", encoding="utf-8")
            (part_dir / "size").write_text("3145728\n", encoding="utf-8")

            fake_dev = tmp_path / "dev" / "mmcblk0p7"
            fake_disk = tmp_path / "dev" / "mmcblk0"
            fake_dev.parent.mkdir(parents=True)
            fake_dev.touch()
            fake_disk.touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DEV": str(fake_dev),
                "NEXTOOMIO_RUN_DIR": str(tmp_path / "run"),
                "NEXTOOMIO_SYS_BLOCK_DIR": str(fake_sys),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, "Grow script must exit non-zero when parted fails")
            self.assertIn("ERROR", res.stderr)

            # Sub-test 2: resize2fs fails with code 1
            make_stub(bin_dir, "parted", "exit 0")
            make_stub(bin_dir, "resize2fs", 'echo "resize2fs simulated error" >&2\nexit 1')
            res2 = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertNotEqual(res2.returncode, 0, "Grow script must exit non-zero when resize2fs fails")
            self.assertIn("ERROR", res2.stderr)

    def test_check_script_e2fsck_exit_codes(self) -> None:
        """Verify AC3 and AC5: nextoomio-data-check.sh handles clean, repaired, and corrupt file systems."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-check.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            fake_run = tmp_path / "run" / "nextoomio"
            fake_dev = tmp_path / "mock_dev"
            fake_dev.touch()

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

    def test_data_mount_condition_configuration(self) -> None:
        """Verify AC5: systemd drop-in prevents mounting corrupted partition when data_unavailable is set."""
        dropin_path = DATA_RECIPE_FILES / "data.mount.d/10-condition.conf"
        self.assertTrue(dropin_path.is_file(), f"Missing {dropin_path}")
        content = dropin_path.read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=!/run/nextoomio/data_unavailable", content)

    def test_init_script_directory_structure_and_modes(self) -> None:
        """Verify AC4: nextoomio-data-init.sh creates directories with correct modes and preallocates real blocks."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            mount_log = tmp_path / "mount.log"

            # Stub mount and mountpoint: report DATA_DIR is mounted, journal is not yet mounted
            make_stub(bin_dir, "mount", f'echo "$@" >> "{mount_log}"\nexit 0')
            make_stub(bin_dir, "mountpoint", 'case "$*" in *journal*) exit 1;; *) exit 0;; esac')

            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Init script failed: {res.stderr}")

            # Verify directory modes
            config_mode = oct(stat.S_IMODE((fake_data / "config").stat().st_mode))
            self.assertEqual(config_mode, "0o755")

            secrets_mode = oct(stat.S_IMODE((fake_data / "secrets").stat().st_mode))
            self.assertEqual(secrets_mode, "0o700", "Secrets directory must be mode 0700")

            secrets_st = (fake_data / "secrets").stat().st_mode
            self.assertEqual(secrets_st & stat.S_IROTH, 0, "Secrets must not be world-readable")
            self.assertEqual(secrets_st & stat.S_IWOTH, 0, "Secrets must not be world-writable")
            self.assertEqual(secrets_st & stat.S_IXOTH, 0, "Secrets must not be world-executable")

            music_mode = oct(stat.S_IMODE((fake_data / "music").stat().st_mode))
            self.assertEqual(music_mode, "0o755")

            update_mode = oct(stat.S_IMODE((fake_data / "update").stat().st_mode))
            self.assertEqual(update_mode, "0o755")

            # Verify preallocated files have expected size AND real disk blocks allocated (not sparse - CQ-R2 / CQ-R4)
            staging_file = fake_data / "update/staging.swu"
            self.assertTrue(staging_file.is_file(), "staging.swu must be preallocated")
            self.assertEqual(staging_file.stat().st_size, 1024 * 1024 * 1024, "staging.swu must be 1 GiB")
            self.assertGreater(staging_file.stat().st_blocks, 0, "staging.swu must have real blocks allocated (not sparse)")

            j0 = fake_data / "update/journal.0"
            j1 = fake_data / "update/journal.1"
            self.assertTrue(j0.is_file() and j1.is_file(), "journal records must be preallocated")
            self.assertEqual(j0.stat().st_size, 64 * 1024)
            self.assertGreater(j0.stat().st_blocks, 0, "journal.0 must have real blocks allocated")
            self.assertEqual(j1.stat().st_size, 64 * 1024)
            self.assertGreater(j1.stat().st_blocks, 0, "journal.1 must have real blocks allocated")

            # Verify bind-mount was executed for persistent log ring
            mount_calls = mount_log.read_text(encoding="utf-8")
            self.assertIn("--bind", mount_calls)
            self.assertIn(str(fake_data / "log/journal"), mount_calls)
            self.assertIn(str(fake_var / "journal"), mount_calls)

            # Verify markers and status
            self.assertTrue((fake_data / ".initialized").is_file())
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_available")
            self.assertFalse((fake_run / "data_unavailable").exists())

    def test_init_script_preallocation_heals_mismatched_file(self) -> None:
        """Verify CQ-R2 / CQ-R1: nextoomio-data-init.sh detects corrupted/incomplete staging file and reallocates."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            make_stub(bin_dir, "mount", "exit 0")
            make_stub(bin_dir, "mountpoint", "exit 0")

            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            # Create an incomplete 512-byte staging file
            update_dir = fake_data / "update"
            update_dir.mkdir(parents=True)
            bad_staging = update_dir / "staging.swu"
            bad_staging.write_bytes(b"x" * 512)
            self.assertEqual(bad_staging.stat().st_size, 512)

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Init script failed: {res.stderr}")
            self.assertIn("size mismatch", res.stdout)
            self.assertEqual(bad_staging.stat().st_size, 1024 * 1024 * 1024, "staging.swu must be reallocated to 1 GiB")
            self.assertGreater(bad_staging.stat().st_blocks, 0)

    def test_init_script_recovery_mode(self) -> None:
        """Verify AC5: nextoomio-data-init.sh in recovery mode provides volatile tmpfs and preserves flag."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            mount_log = tmp_path / "mount.log"

            make_stub(bin_dir, "mount", f'echo "$@" >> "{mount_log}"\nexit 0')
            make_stub(bin_dir, "mountpoint", "exit 1")  # Partition not mounted

            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            # Simulate recovery mode flag from pre-mount check
            (fake_run / "data_unavailable").touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Init script in recovery mode failed: {res.stderr}")
            self.assertTrue((fake_run / "data_unavailable").exists())
            self.assertEqual((fake_run / "data_status").read_text().strip(), "data_unavailable")
            self.assertTrue((fake_data / "config").is_dir())
            self.assertTrue((fake_data / "secrets").is_dir())

            # Verify tmpfs mount was requested
            mount_calls = mount_log.read_text(encoding="utf-8")
            self.assertIn("-t tmpfs", mount_calls)

    def test_init_script_surfaces_recovery_tmpfs_failure(self) -> None:
        """Verify CQ-R2 / CQ-R4: nextoomio-data-init.sh fails visibly if recovery tmpfs mount fails."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()

            # Stub mount to fail
            make_stub(bin_dir, "mount", 'echo "mount failed" >&2\nexit 1')
            make_stub(bin_dir, "mountpoint", "exit 1")

            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            (fake_run / "data_unavailable").touch()

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(tmp_path / "var" / "log"),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, "Init script must fail when tmpfs mount fails")
            self.assertIn("ERROR", res.stderr)

    def test_init_script_surfaces_bind_mount_failure(self) -> None:
        """Verify CQ-R2 / CQ-R4: nextoomio-data-init.sh fails visibly if journal bind-mount fails."""
        script_path = DATA_RECIPE_FILES / "nextoomio-data-init.sh"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()

            # Stub mount: fail when called with --bind
            make_stub(
                bin_dir,
                "mount",
                'case "$*" in *--bind*) echo "bind mount failed" >&2; exit 1;; *) exit 0;; esac',
            )
            # Stub mountpoint: report DATA_DIR is mounted, but /var/log/journal is not
            make_stub(
                bin_dir,
                "mountpoint",
                'case "$*" in */var/log/journal*) exit 1;; *) exit 0;; esac',
            )

            fake_data = tmp_path / "data"
            fake_run = tmp_path / "run" / "nextoomio"
            fake_var = tmp_path / "var" / "log"
            fake_data.mkdir(parents=True)
            fake_run.mkdir(parents=True)
            fake_var.mkdir(parents=True)

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/bin:/usr/bin')}",
                "NEXTOOMIO_DATA_DIR": str(fake_data),
                "NEXTOOMIO_RUN_DIR": str(fake_run),
                "NEXTOOMIO_VAR_LOG_DIR": str(fake_var),
            }
            res = subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, "Init script must fail when journal bind mount fails")
            self.assertIn("ERROR", res.stderr)

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
