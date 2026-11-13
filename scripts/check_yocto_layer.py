#!/usr/bin/env python3
"""Validate meta-nextoomio Yocto layer, Wic partition layout, hardware profiles, and kas configs."""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    errors: list[str] = []

    required_files = [
        ROOT / "meta-nextoomio/COPYING.MIT",
        ROOT / "meta-nextoomio/conf/layer.conf",
        ROOT / "meta-nextoomio/conf/distro/nextoomio.conf",
        ROOT / "meta-nextoomio/wic/nextoomio-ab.wks.in",
        ROOT / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json",
        ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image.bb",
        ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image-dev.bb",
        ROOT / "kas/base.yml",
        ROOT / "kas/rpi4-64.yml",
        ROOT / "kas/ci-hosted.yml",
    ]

    for path in required_files:
        if not path.is_file():
            errors.append(f"Missing required Yocto/kas file: {path.relative_to(ROOT)}")

    profile_path = ROOT / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json"
    if profile_path.is_file():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("profile_id") != "hifiberry-amp":
            errors.append("Reference profile_id must be 'hifiberry-amp'")
        if profile.get("display_name") != "HiFiBerry Amp":
            errors.append("Reference display_name must be 'HiFiBerry Amp' (Volumio-aligned)")
        if profile.get("default_logical_volume_percent") != 30:
            errors.append("Default logical volume must be 30% per SPECIFICATION.md section 5.1")

    wks_path = ROOT / "meta-nextoomio/wic/nextoomio-ab.wks.in"
    if wks_path.is_file():
        wks_text = wks_path.read_text(encoding="utf-8")
        for label in ("select", "boot_a", "boot_b", "root_a", "root_b", "data"):
            if f"--label {label}" not in wks_text:
                errors.append(f"Wic layout missing partition label '{label}'")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: meta-nextoomio layer, A/B Wic layout, reference hardware profile, and kas files validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
