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
        ROOT / "meta-nextoomio/recipes-bsp/bootfiles/rpi-autoboot.bb",
        ROOT / "meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend",
        ROOT / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd_0.0.1.bb",
        ROOT / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd-crates.inc",
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

    rpi4_kas = ROOT / "kas/rpi4-64.yml"
    if rpi4_kas.is_file():
        rpi4_text = rpi4_kas.read_text(encoding="utf-8")
        if "tryboot_a_b" in rpi4_text:
            errors.append(
                "kas/rpi4-64.yml: tryboot_a_b must not be set in RPI_EXTRA_CONFIG/config.txt; "
                "tryboot_a_b is a property of autoboot.txt (CQ-R2)"
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

    # D-004: Validate kernel command line root partition specification
    # Fails if fixed device nodes (e.g. /dev/mmcblk0p2) are used, if unusable partition/fs labels
    # (PARTLABEL=/LABEL=) are used on MBR layout, or if not pointing by PARTUUID to slot A (partition 5).
    distro_conf = ROOT / "meta-nextoomio/conf/distro/nextoomio.conf"
    cmdline_bbappend = ROOT / "meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend"

    if distro_conf.is_file():
        conf_text = distro_conf.read_text(encoding="utf-8")
        if re.search(r"^\s*CMDLINE_ROOT_PARTITION\b", conf_text, re.MULTILINE):
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf: CMDLINE_ROOT_PARTITION must not be defined "
                "in distro conf; define it unconditionally in recipes-bsp/bootfiles/rpi-cmdline.bbappend "
                "to avoid dead duplication (CQ-R14)"
            )

    has_cmdline_root = False
    expected_partuuid = None
    if cmdline_bbappend.is_file():
        bbappend_text = cmdline_bbappend.read_text(encoding="utf-8")
        for line in bbappend_text.splitlines():
            line_clean = line.strip()
            if line_clean.startswith("#") or not line_clean:
                continue
            if re.match(r"^\s*CMDLINE_ROOT_PARTITION\s*\?=", line_clean):
                errors.append(
                    "rpi-cmdline.bbappend: CMDLINE_ROOT_PARTITION must use '=' instead of '?=' "
                    "because bbappend is parsed after recipe defaults (CQ-R14)"
                )
            m = re.match(r"^\s*CMDLINE_ROOT_PARTITION\s*=\s*[\"']?([^\"'\s]+)[\"']?", line_clean)
            if m:
                has_cmdline_root = True
                val = m.group(1)
                if re.search(r"/dev/mmcblk|/dev/sd", val):
                    errors.append(
                        f"rpi-cmdline.bbappend: CMDLINE_ROOT_PARTITION '{val}' uses a fixed root device "
                        f"node; must name root partition by PARTUUID (never /dev/mmcblk0p2, D-004)"
                    )
                if re.match(r"^(?:PARTLABEL|LABEL)=", val):
                    errors.append(
                        f"rpi-cmdline.bbappend: CMDLINE_ROOT_PARTITION '{val}' uses PARTLABEL or LABEL; "
                        f"MBR partition tables have no partition names, and kernel cannot resolve LABEL without initramfs. "
                        f"Must use PARTUUID=<disk-signature>-05 (D-004, CQ-R1)"
                    )
                m_partuuid = re.match(r"^PARTUUID=([0-9a-fA-F]{8})-05$", val)
                if not m_partuuid:
                    errors.append(
                        f"rpi-cmdline.bbappend: CMDLINE_ROOT_PARTITION '{val}' must be in format "
                        f"PARTUUID=<disk-signature>-05 pointing to slot A root partition 5 on MBR layout"
                    )
                else:
                    expected_partuuid = m_partuuid.group(1).lower()

    if not has_cmdline_root:
        errors.append(
            "CMDLINE_ROOT_PARTITION must be configured in meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend "
            "to prevent upstream meta-raspberrypi defaulting to /dev/mmcblk0p2 (D-004)"
        )

    # D-005: Validate selector partition isolation and autoboot.txt deployment
    if distro_conf.is_file():
        conf_text = distro_conf.read_text(encoding="utf-8")
        m_select = re.search(r'IMAGE_BOOT_FILES_label-select\s*=\s*["\']([^"\']+)["\']', conf_text)
        if not m_select:
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf must define IMAGE_BOOT_FILES_label-select "
                "to isolate selector partition content (D-005)"
            )
        else:
            select_files = m_select.group(1).split()
            if "autoboot.txt" not in select_files:
                errors.append(
                    "IMAGE_BOOT_FILES_label-select must contain 'autoboot.txt' for A/B boot selection"
                )
            for forbidden in ("Image", "zImage", "uImage", "bcm2835-bootfiles", "overlays", "config.txt"):
                if forbidden in select_files or any(forbidden in f for f in select_files):
                    errors.append(
                        f"IMAGE_BOOT_FILES_label-select contains forbidden slot content '{forbidden}'; "
                        f"selector partition must hold only the boot-selection file (D-005)"
                    )

        m_wicvars = re.search(r'WICVARS:append\s*=\s*["\']([^"\']+)["\']', conf_text)
        if not m_wicvars or "IMAGE_BOOT_FILES_label-select" not in m_wicvars.group(1):
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf: WICVARS must append IMAGE_BOOT_FILES_label-select "
                "for Wic bootimg-partition plugin to read partition-specific boot files"
            )

    autoboot_bb = ROOT / "meta-nextoomio/recipes-bsp/bootfiles/rpi-autoboot.bb"
    if autoboot_bb.is_file():
        autoboot_text = autoboot_bb.read_text(encoding="utf-8")
        if "inherit deploy" not in autoboot_text:
            errors.append("rpi-autoboot.bb must inherit deploy to deploy autoboot.txt to DEPLOYDIR")
        if "autoboot.txt" not in autoboot_text or "[all]" not in autoboot_text or "boot_partition" not in autoboot_text:
            errors.append("rpi-autoboot.bb must generate autoboot.txt with [all] and boot_partition sections")

    image_bb = ROOT / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
    if image_bb.is_file():
        image_text = image_bb.read_text(encoding="utf-8")
        if "rpi-autoboot:do_deploy" not in image_text:
            errors.append(
                "meta-nextoomio/recipes-core/images/nextoomio-image.bb: do_image_wic[depends] "
                "must depend on 'rpi-autoboot:do_deploy' to ensure autoboot.txt is staged prior to Wic execution"
            )

    # Wic layout verification (ARCHITECTURE.md section 3.6, AC2, AC4, AC5)
    wks_path = ROOT / "meta-nextoomio/files/wic/nextoomio-ab.wks.in"
    if wks_path.is_file():
        wks_text = wks_path.read_text(encoding="utf-8")
        for label in ("select", "boot_a", "boot_b", "root_a", "root_b", "data"):
            if f"--label {label}" not in wks_text:
                errors.append(f"Wic layout missing partition label '{label}'")

        if "--ptable msdos" not in wks_text:
            errors.append("Wic layout must use MBR partition table (--ptable msdos) per ARCHITECTURE.md section 3.6")

        m_diskid = re.search(r"--diskid\s+(?:0x)?([0-9a-fA-F]{8})", wks_text)
        if not m_diskid:
            errors.append("Wic layout bootloader must specify fixed MBR disk signature via '--diskid 0x<id>'")
        elif expected_partuuid and m_diskid.group(1).lower() != expected_partuuid:
            errors.append(
                f"Wic bootloader diskid '0x{m_diskid.group(1)}' does not match CMDLINE_ROOT_PARTITION "
                f"disk signature '{expected_partuuid}' in rpi-cmdline.bbappend"
            )

        # Verify sizes and slot B emptiness on fresh card (AC5)
        for line in wks_text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if "--label select" in line_str:
                if "--fixed-size 64M" not in line_str:
                    errors.append("Wic selector partition must be fixed size 64M")
                if "--active" not in line_str:
                    errors.append("Wic selector partition must be active/bootable")
            elif "--label boot_a" in line_str:
                if "--fixed-size 256M" not in line_str:
                    errors.append("Wic boot_a partition must be fixed size 256M")
                if "--source bootimg-partition" not in line_str:
                    errors.append("Wic boot_a partition must use --source bootimg-partition")
            elif "--label boot_b" in line_str:
                if "--fixed-size 256M" not in line_str:
                    errors.append("Wic boot_b partition must be fixed size 256M")
                if "--source" in line_str:
                    errors.append("Wic boot_b partition must have no --source on initial image (empty slot B, AC5)")
            elif "--label root_a" in line_str:
                if "--fixed-size 3072M" not in line_str:
                    errors.append("Wic root_a partition must be fixed size 3072M")
                if "--source rootfs" not in line_str:
                    errors.append("Wic root_a partition must use --source rootfs")
            elif "--label root_b" in line_str:
                if "--fixed-size 3072M" not in line_str:
                    errors.append("Wic root_b partition must be fixed size 3072M")
                if "--source" in line_str:
                    errors.append("Wic root_b partition must have no --source on initial image (empty slot B, AC5)")
            elif "--label data" in line_str:
                if "--fixed-size 1536M" not in line_str:
                    errors.append("Wic data partition must be fixed size 1536M initial size")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: meta-nextoomio layer, A/B Wic layout, reference hardware profile, kas files, and D-004/D-005 rules validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
