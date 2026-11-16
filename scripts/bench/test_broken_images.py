#!/usr/bin/env python3
"""Static validation and bench inspection script for deliberately broken images (P0-F4.7).

Inspects the three broken image configurations and build options defined in P0-F4.7 and ARCHITECTURE.md section 3.6:
- AC1: nextoomio-image-broken-hang.bb (kas/test-broken-hang.yml)
- AC2: nextoomio-image-broken-health.bb (kas/test-broken-health.yml)
- AC3: nextoomio-image-broken-network.bb (kas/test-broken-network.yml)
- AC6: Documented build options and isolation from release configuration.

Usage:
    python3 scripts/bench/test_broken_images.py --test
    python3 scripts/bench/test_broken_images.py [--host <ip/hostname>]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

TEST_IMAGE_CONFIGS = {
    "hang": {
        "recipe_rel": "meta-nextoomio/recipes-core/images/nextoomio-image-broken-hang.bb",
        "kas_rel": "kas/test-broken-hang.yml",
        "target": "nextoomio-image-broken-hang",
        "description": "Kernel/init hang before systemd starts (AC1)",
        "fault_package": "nextoomio-test-faults-hang",
    },
    "health": {
        "recipe_rel": "meta-nextoomio/recipes-core/images/nextoomio-image-broken-health.bb",
        "kas_rel": "kas/test-broken-health.yml",
        "target": "nextoomio-image-broken-health",
        "description": "Local health check prerequisite failure (AC2)",
        "fault_package": None,
    },
    "network": {
        "recipe_rel": "meta-nextoomio/recipes-core/images/nextoomio-image-broken-network.bb",
        "kas_rel": "kas/test-broken-network.yml",
        "target": "nextoomio-image-broken-network",
        "description": "Unreachable network / no SSH service (AC3)",
        "fault_package": None,
    },
}


def validate_test_images_statically(root: pathlib.Path = ROOT) -> list[str]:
    """Validate static invariants for broken test images and build options (AC6)."""
    errors: list[str] = []

    # 1. Base image must not contain any fault injection packages (Isolation invariant)
    base_image_bb = root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
    if base_image_bb.is_file():
        base_text = base_image_bb.read_text(encoding="utf-8")
        if "nextoomio-test-faults" in base_text:
            errors.append("nextoomio-image.bb must not include nextoomio-test-faults (broken images must be isolated)")
        if "broken" in base_text.lower():
            errors.append("nextoomio-image.bb must not reference broken image components")
    else:
        errors.append("Missing base image recipe: meta-nextoomio/recipes-core/images/nextoomio-image.bb")

    # 2. Base kas configuration must target nextoomio-image, not any broken variant
    base_kas = root / "kas/base.yml"
    if base_kas.is_file():
        base_kas_text = base_kas.read_text(encoding="utf-8")
        if not re.search(r"^\s*target:\s*nextoomio-image\s*$", base_kas_text, re.MULTILINE):
            errors.append("kas/base.yml must target 'nextoomio-image' as default release target")
        if "broken" in base_kas_text.lower():
            errors.append("kas/base.yml must not reference broken image variants")

    # 3. Fault injection recipe and helper files for early hang
    faults_bb = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/nextoomio-test-faults_0.0.1.bb"
    if not faults_bb.is_file():
        errors.append("Missing fault injection recipe: nextoomio-test-faults_0.0.1.bb")
    else:
        faults_text = faults_bb.read_text(encoding="utf-8")
        if "${PN}-hang" not in faults_text:
            errors.append("nextoomio-test-faults_0.0.1.bb missing package ${PN}-hang")

    hang_sh = root / "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/nextoomio-hang-early.sh"
    if not hang_sh.is_file():
        errors.append("Missing early hang script: nextoomio-hang-early.sh")
    else:
        hang_text = hang_sh.read_text(encoding="utf-8")
        if "DELIBERATE EARLY INIT HANG" not in hang_text:
            errors.append("nextoomio-hang-early.sh missing identification banner")
        if "sleep" not in hang_text or "while" not in hang_text:
            errors.append("nextoomio-hang-early.sh must loop/sleep to induce hang")

    # 4. Each broken image recipe and kas file must exist and match specification
    for name, info in TEST_IMAGE_CONFIGS.items():
        recipe_path = root / info["recipe_rel"]
        kas_path = root / info["kas_rel"]
        target = info["target"]
        fault_pkg = info["fault_package"]

        if not recipe_path.is_file():
            errors.append(f"Missing broken image recipe: {info['recipe_rel']}")
        else:
            rec_text = recipe_path.read_text(encoding="utf-8")
            if "require nextoomio-image.bb" not in rec_text:
                errors.append(f"{recipe_path.name} must require nextoomio-image.bb")
            if fault_pkg and fault_pkg not in rec_text:
                errors.append(f"{recipe_path.name} must install {fault_pkg}")

            if name == "hang":
                if "ROOTFS_POSTPROCESS_COMMAND" not in rec_text or "/sbin/init" not in rec_text:
                    errors.append(f"{recipe_path.name} must override /sbin/init via ROOTFS_POSTPROCESS_COMMAND")
            elif name == "health":
                if "ROOTFS_POSTPROCESS_COMMAND" not in rec_text or "pipewire.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask pipewire.service via ROOTFS_POSTPROCESS_COMMAND")
                if "nextoomio-audio-profile.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask nextoomio-audio-profile.service via ROOTFS_POSTPROCESS_COMMAND")
            elif name == "network":
                if "ROOTFS_POSTPROCESS_COMMAND" not in rec_text or "NetworkManager.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask NetworkManager.service via ROOTFS_POSTPROCESS_COMMAND")
                if "sshd.service" not in rec_text:
                    errors.append(f"{recipe_path.name} must mask sshd.service via ROOTFS_POSTPROCESS_COMMAND")

        if not kas_path.is_file():
            errors.append(f"Missing kas configuration file: {info['kas_rel']}")
        else:
            kas_text = kas_path.read_text(encoding="utf-8")
            if f"target: {target}" not in kas_text:
                errors.append(f"{kas_path.name} must set target to '{target}'")
            if "rpi4-64.yml" not in kas_text:
                errors.append(f"{kas_path.name} must include rpi4-64.yml")

    return errors


def run_self_tests() -> int:
    """Run automated self-tests verifying static recipe and kas configuration checks."""
    print("Running test_broken_images self-tests...")

    # 1. Static validation of repository files
    errs = validate_test_images_statically(ROOT)
    assert not errs, f"Static validation failed: {errs}"
    print("  test_static_validation_passes: OK")

    # 2. Negative test: verify that modifying nextoomio-image.bb to include test faults fails validation
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_p = pathlib.Path(tmp_str)
        img_dir = tmp_p / "meta-nextoomio/recipes-core/images"
        img_dir.mkdir(parents=True)
        (img_dir / "nextoomio-image.bb").write_text('IMAGE_INSTALL:append = " nextoomio-test-faults-hang"\n', encoding="utf-8")
        kas_dir = tmp_p / "kas"
        kas_dir.mkdir(parents=True)
        (kas_dir / "base.yml").write_text("target: nextoomio-image\n", encoding="utf-8")
        test_errs = validate_test_images_statically(tmp_p)
        assert any("nextoomio-image.bb must not include nextoomio-test-faults" in e for e in test_errs)
    print("  test_isolation_failure_detected: OK")

    # 3. Negative test: verify that missing require nextoomio-image.bb is detected
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_p = pathlib.Path(tmp_str)
        # copy all relevant files from ROOT
        for rel in [
            "meta-nextoomio/recipes-core/images/nextoomio-image.bb",
            "meta-nextoomio/recipes-core/images/nextoomio-image-broken-hang.bb",
            "meta-nextoomio/recipes-core/images/nextoomio-image-broken-health.bb",
            "meta-nextoomio/recipes-core/images/nextoomio-image-broken-network.bb",
            "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/nextoomio-test-faults_0.0.1.bb",
            "meta-nextoomio/recipes-nextoomio/nextoomio-test-faults/files/nextoomio-hang-early.sh",
            "kas/base.yml",
            "kas/test-broken-hang.yml",
            "kas/test-broken-health.yml",
            "kas/test-broken-network.yml",
        ]:
            src = ROOT / rel
            dst = tmp_p / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        # Mutate broken-hang to remove require
        (tmp_p / "meta-nextoomio/recipes-core/images/nextoomio-image-broken-hang.bb").write_text(
            "# missing require\n", encoding="utf-8"
        )
        test_errs = validate_test_images_statically(tmp_p)
        assert any("must require nextoomio-image.bb" in e for e in test_errs)
    print("  test_missing_require_detected: OK")

    print("OK: all test_broken_images self-tests passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Static inspector for deliberately broken images (P0-F4.7)")
    parser.add_argument("--test", action="store_true", help="Run automated static validation and negative self-tests")
    parser.add_argument("--host", default="nextoomio.local", help="Target bench device hostname or IP")
    args = parser.parse_args()

    if args.test:
        return run_self_tests()

    print(f"Target bench host: {args.host}")
    print("Checking static invariants...")
    errs = validate_test_images_statically(ROOT)
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("Static checks OK: 3 test image recipes and kas build options verified.")
    print("Note: Runtime rollback execution requires a physical bench session on hardware with SWUpdate.")
    print("No bench run was performed by this inspection tool.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
