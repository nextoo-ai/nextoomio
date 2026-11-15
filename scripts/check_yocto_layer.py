#!/usr/bin/env python3
"""Validate meta-nextoomio Yocto layer, Wic partition layout, hardware profiles, and kas configs."""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    errors: list[str] = []

    required_files = [
        ROOT / "meta-nextoomio/COPYING.MIT",
        ROOT / "meta-nextoomio/conf/layer.conf",
        ROOT / "meta-nextoomio/conf/distro/nextoomio.conf",
        ROOT / "meta-nextoomio/files/wic/nextoomio-ab.wks.in",
        ROOT / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json",
        ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image.bb",
        ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image-dev.bb",
        ROOT / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd_0.0.1.bb",
        ROOT / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd-crates.inc",
        ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-data/nextoomio-data_0.0.1.bb",
        ROOT / "meta-nextoomio/recipes-core/base-files/base-files_%.bbappend",
        ROOT / "meta-nextoomio/recipes-core/base-files/files/fstab",
        ROOT / "meta-nextoomio/recipes-core/systemd/systemd-conf_%.bbappend",
        ROOT / "meta-nextoomio/recipes-core/systemd/files/10-nextoomio-journald.conf",
        ROOT / "kas/base.yml",
        ROOT / "kas/rpi4-64.yml",
        ROOT / "kas/ci-hosted.yml",
    ]

    for path in required_files:
        if not path.is_file():
            errors.append(f"Missing required Yocto/kas file: {path.relative_to(ROOT)}")

    kas_dir = ROOT / "kas"
    if kas_dir.is_dir():
        for kas_file in sorted(kas_dir.glob("*.yml")):
            for line_idx, line in enumerate(kas_file.read_text(encoding="utf-8").splitlines(), start=1):
                m = re.match(r"^\s*branch:\s*([0-9]+(?:\.[0-9]+)*)\s*(?:#.*)?$", line)
                if m:
                    errors.append(
                        f"{kas_file.relative_to(ROOT)} line {line_idx}: branch value '{m.group(1)}' "
                        f"must be quoted as a string to avoid YAML float parsing"
                    )

    profile_path = ROOT / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json"
    if profile_path.is_file():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("profile_id") != "hifiberry-amp":
            errors.append("Reference profile_id must be 'hifiberry-amp'")
        if profile.get("display_name") != "HiFiBerry Amp":
            errors.append("Reference display_name must be 'HiFiBerry Amp' (Volumio-aligned)")
        if profile.get("default_logical_volume_percent") != 30:
            errors.append("Default logical volume must be 30% per SPECIFICATION.md section 5.1")

    if (ROOT / "meta-nextoomio/wic").exists():
        errors.append(
            "Legacy directory meta-nextoomio/wic must be removed; "
            "wic/wks files must reside in meta-nextoomio/files/wic per OE-core sanity requirements"
        )

    wks_path = ROOT / "meta-nextoomio/files/wic/nextoomio-ab.wks.in"
    if wks_path.is_file():
        wks_text = wks_path.read_text(encoding="utf-8")
        for label in ("select", "boot_a", "boot_b", "root_a", "root_b", "data"):
            if f"--label {label}" not in wks_text:
                errors.append(f"Wic layout missing partition label '{label}'")
        if re.search(r"part\s+/data\s+.*--fixed-size", wks_text):
            errors.append("Wic layout /data partition must not use --fixed-size (D-006: grown on first boot)")

    image_path = ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
    if image_path.is_file():
        image_text = image_path.read_text(encoding="utf-8")
        if "nextoomio-data" not in image_text:
            errors.append("nextoomio-image.bb must install nextoomio-data package")

    data_recipe_path = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-data/nextoomio-data_0.0.1.bb"
    if data_recipe_path.is_file():
        data_recipe_text = data_recipe_path.read_text(encoding="utf-8")
        for unit in ("nextoomio-boot-mount.service", "nextoomio-data-grow.service", "nextoomio-data-check.service", "nextoomio-data-init.service"):
            if unit not in data_recipe_text:
                errors.append(f"nextoomio-data_0.0.1.bb missing systemd unit registration: {unit}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: meta-nextoomio layer, A/B Wic layout, reference hardware profile, and kas files validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
