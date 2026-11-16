#!/usr/bin/env python3
"""Validate meta-nextoomio Yocto layer, Wic partition layout, hardware profiles, and kas configs.

Supports --test to execute automated self-tests verifying that the CI check fails
on deliberately wrong configurations (AC5).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def derive_hardware_boot_include_from_recipe(
    recipe_path: pathlib.Path,
    profile_path: pathlib.Path,
    profile_id: str,
) -> tuple[str, str | None]:
    """Execute recipe do_compile derivation logic against profile_path and return (generated_include, error)."""
    recipe_text = recipe_path.read_text(encoding="utf-8")
    m = re.search(r"do_compile\s*\(\)\s*\{(.*?)\n\}", recipe_text, re.DOTALL)
    if not m:
        return "", "do_compile function not found in recipe"

    compile_body = m.group(1)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_p = pathlib.Path(tmp_dir)
        script = f"""
        S="{profile_path.parent}"
        B="{tmp_p}"
        NEXTOOMIO_AUDIO_PROFILE="{profile_id}"
        {compile_body}
        """
        proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        if proc.returncode != 0:
            return "", f"do_compile execution failed (exit code {proc.returncode}): {proc.stderr.strip()}"

        out_file = tmp_p / "nextoomio-hardware.txt"
        if not out_file.is_file():
            return "", "do_compile did not generate nextoomio-hardware.txt"

        return out_file.read_text(encoding="utf-8"), None


def validate_layer(root: pathlib.Path = ROOT) -> list[str]:
    """Run all static validation checks against the given repository root directory."""
    errors: list[str] = []

    required_files = [
        root / "meta-nextoomio/COPYING.MIT",
        root / "meta-nextoomio/conf/layer.conf",
        root / "meta-nextoomio/conf/distro/nextoomio.conf",
        root / "meta-nextoomio/files/wic/nextoomio-ab.wks.in",
        root / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json",
        root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb",
        root / "meta-nextoomio/recipes-core/images/nextoomio-image-dev.bb",
        root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-autoboot.bb",
        root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend",
        root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend",
        root / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd_0.0.1.bb",
        root / "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd-crates.inc",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/nextoomio-audio-profile_0.0.1.bb",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile.service",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile-setup.sh",
        root / "kas/base.yml",
        root / "kas/rpi4-64.yml",
        root / "kas/ci-hosted.yml",
        root / "meta-nextoomio/recipes-connectivity/nftables/nftables_%.bbappend",
        root / "meta-nextoomio/recipes-connectivity/nftables/files/nftables.conf",
        root / "meta-nextoomio/recipes-connectivity/nftables/files/00-default.nft",
        root / "meta-nextoomio/recipes-connectivity/networkmanager/networkmanager_%.bbappend",
        root / "meta-nextoomio/recipes-connectivity/networkmanager/files/10-default-wired.nmconnection",
        root / "meta-nextoomio/recipes-connectivity/networkmanager/files/99-nextoomio.preset",
        root / "meta-nextoomio/recipes-connectivity/avahi/avahi_%.bbappend",
        root / "meta-nextoomio/recipes-connectivity/avahi/files/avahi-daemon.conf",
        root / "meta-nextoomio/recipes-core/base-files/base-files_%.bbappend",
        root / "meta-nextoomio/recipes-core/systemd/systemd_%.bbappend",
        root / "meta-nextoomio/recipes-core/systemd/files/99-timesync.preset",
        root / "meta-nextoomio/recipes-connectivity/openssh/openssh_%.bbappend",
        root / "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio",
        root / "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset",
        root / "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/nextoomio-ssh-keys_0.0.1.bb",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys.service",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh",
        root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-stop.sh",
        root / "meta-nextoomio/classes/image_types_nextoomio.bbclass",
        root / "meta-nextoomio/recipes-support/swupdate/swupdate_%.bbappend",
        root / "meta-nextoomio/recipes-support/swupdate/nextoomio-update-image.bb",
        root / "meta-nextoomio/recipes-support/swupdate/files/sw-description",
        root / "meta-nextoomio/recipes-support/swupdate/files/swupdate.cfg",
        root / "meta-nextoomio/recipes-support/swupdate/files/hwrevision",
    ]

    for path in required_files:
        if not path.is_file():
            errors.append(f"Missing required Yocto/kas file: {path.relative_to(root)}")

    kas_dir = root / "kas"
    if kas_dir.is_dir():
        for kas_file in sorted(kas_dir.glob("*.yml")):
            for line_idx, line in enumerate(kas_file.read_text(encoding="utf-8").splitlines(), start=1):
                m = re.match(r"^\s*branch:\s*([0-9]+(?:\.[0-9]+)*)\s*(?:#.*)?$", line)
                if m:
                    errors.append(
                        f"{kas_file.relative_to(root)} line {line_idx}: branch value '{m.group(1)}' "
                        f"must be quoted as a string to avoid YAML float parsing"
                    )

    rpi4_kas = root / "kas/rpi4-64.yml"
    if rpi4_kas.is_file():
        rpi4_text = rpi4_kas.read_text(encoding="utf-8")
        if "tryboot_a_b" in rpi4_text:
            errors.append(
                "kas/rpi4-64.yml: tryboot_a_b must not be set in RPI_EXTRA_CONFIG/config.txt; "
                "tryboot_a_b is a property of autoboot.txt (CQ-R2)"
            )
        if re.search(r"dtoverlay\s*=", rpi4_text) or "hifiberry-amp" in rpi4_text:
            errors.append(
                "kas/rpi4-64.yml: sound card overlay must not be hardcoded in build configuration; "
                "overlay must be derived from the hardware profile (D-011)"
            )

    profile_path = root / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json"
    if profile_path.is_file():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("profile_id") != "hifiberry-amp":
            errors.append("Reference profile_id must be 'hifiberry-amp'")
        if profile.get("display_name") != "HiFiBerry Amp":
            errors.append("Reference display_name must be 'HiFiBerry Amp' (Volumio-aligned)")
        if profile.get("default_logical_volume_percent") != 30:
            errors.append("Default logical volume must be 30% per SPECIFICATION.md section 5.1")
        if profile.get("fixed_hardware_mixer_percent") != 30:
            errors.append(
                "Reference fixed_hardware_mixer_percent must be 30% (safe level for direct speaker drive, D-012, ARCHITECTURE.md section 5.3)"
            )
        if profile.get("fixed_hardware_mixer_percent") == 100:
            errors.append(
                "Reference fixed_hardware_mixer_percent must NOT be 100% (unattenuated output risk on bench, D-012)"
            )
        if profile.get("alsa_mixer_control") != "Master":
            errors.append("Reference alsa_mixer_control must be 'Master' per ARCHITECTURE.md section 9.2")
        if profile.get("dtoverlay") != "hifiberry-amp":
            errors.append("Reference dtoverlay must be 'hifiberry-amp' per ARCHITECTURE.md section 9.2")
        if profile.get("alsa_card_id") != "sndrpihifiberry":
            errors.append("Reference alsa_card_id must be 'sndrpihifiberry' to identify sound card in ALSA (AC1, AC5)")

    if (root / "meta-nextoomio/wic").exists():
        errors.append(
            "Legacy directory meta-nextoomio/wic must be removed; "
            "wic/wks files must reside in meta-nextoomio/files/wic per OE-core sanity requirements"
        )

    # D-004: Validate kernel command line root partition specification
    distro_conf = root / "meta-nextoomio/conf/distro/nextoomio.conf"
    cmdline_bbappend = root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend"

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

        m_boot_a = re.search(r'IMAGE_BOOT_FILES_label-boot_a\s*=\s*["\']([^"\']+)["\']', conf_text)
        if not m_boot_a or "nextoomio-hardware.txt" not in m_boot_a.group(1):
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf: IMAGE_BOOT_FILES_label-boot_a must include "
                "'nextoomio-hardware.txt' to install derived hardware overlay into slot A boot partition (D-011)"
            )
        if m_select and "nextoomio-hardware.txt" in m_select.group(1):
            errors.append(
                "IMAGE_BOOT_FILES_label-select must NOT contain 'nextoomio-hardware.txt'; "
                "selector partition must hold only the boot-selection file (D-005)"
            )

    autoboot_bb = root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-autoboot.bb"
    if autoboot_bb.is_file():
        autoboot_text = autoboot_bb.read_text(encoding="utf-8")
        if "inherit deploy" not in autoboot_text:
            errors.append("rpi-autoboot.bb must inherit deploy to deploy autoboot.txt to DEPLOYDIR")
        if "autoboot.txt" not in autoboot_text or "[all]" not in autoboot_text or "boot_partition" not in autoboot_text:
            errors.append("rpi-autoboot.bb must generate autoboot.txt with [all] and boot_partition sections")

    image_bb = root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
    if image_bb.is_file():
        image_text = image_bb.read_text(encoding="utf-8")
        if "rpi-autoboot:do_deploy" not in image_text:
            errors.append(
                "meta-nextoomio/recipes-core/images/nextoomio-image.bb: do_image_wic[depends] "
                "must depend on 'rpi-autoboot:do_deploy' to ensure autoboot.txt is staged prior to Wic execution"
            )
        if "nextoomio-audio-profile:do_deploy" not in image_text:
            errors.append(
                "meta-nextoomio/recipes-core/images/nextoomio-image.bb: do_image_wic[depends] "
                "must depend on 'nextoomio-audio-profile:do_deploy' to stage hardware boot include (D-011)"
            )
        if "nextoomio-audio-profile" not in image_text:
            errors.append(
                "meta-nextoomio/recipes-core/images/nextoomio-image.bb: IMAGE_INSTALL "
                "must include 'nextoomio-audio-profile' (D-011, D-012, P0-F6.1)"
            )

    # Wic layout verification (ARCHITECTURE.md section 3.6, AC2, AC4, AC5)
    wks_path = root / "meta-nextoomio/files/wic/nextoomio-ab.wks.in"
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

        # Verify sizes, read-only select partition (AC7), and slot B emptiness on fresh card (AC5)
        for line in wks_text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if "--label select" in line_str:
                if "--fixed-size 64M" not in line_str:
                    errors.append("Wic selector partition must be fixed size 64M")
                if "--active" not in line_str:
                    errors.append("Wic selector partition must be active/bootable")
                m_fsopt = re.search(r'--fsoptions\s+["\']([^"\']+)["\']', line_str)
                if not m_fsopt or "ro" not in [opt.strip() for opt in m_fsopt.group(1).split(",")]:
                    errors.append("Wic selector partition must be mounted read-only (--fsoptions 'ro', AC7)")
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

    # P0-F2.4: Validate network baseline, firewall rules, and hostname configuration
    if distro_conf.is_file():
        conf_text = distro_conf.read_text(encoding="utf-8")
        if not re.search(r'^\s*hostname\s*=\s*["\']nextoomio["\']', conf_text, re.MULTILINE):
            errors.append("meta-nextoomio/conf/distro/nextoomio.conf must set hostname = 'nextoomio' (AC2)")
        if not re.search(r'^\s*VIRTUAL-RUNTIME_net_manager\s*=\s*["\']networkmanager["\']', conf_text, re.MULTILINE):
            errors.append("meta-nextoomio/conf/distro/nextoomio.conf must set VIRTUAL-RUNTIME_net_manager = 'networkmanager'")
        if "timesyncd" not in conf_text:
            errors.append("meta-nextoomio/conf/distro/nextoomio.conf must enable systemd timesyncd (AD-21, AC5)")

    nft_conf = root / "meta-nextoomio/recipes-connectivity/nftables/files/nftables.conf"
    if nft_conf.is_file():
        nft_text = nft_conf.read_text(encoding="utf-8")
        if not re.search(r"type\s+filter\s+hook\s+input\s+priority\s+filter\s*;\s*policy\s+drop\s*;", nft_text):
            errors.append("nftables.conf must configure input chain with 'policy drop;' for default-deny inbound firewall (AC3)")
        if "udp dport 5353 accept" not in nft_text:
            errors.append("nftables.conf must allow mDNS (udp dport 5353 accept) for name resolution (AC2)")
        if 'include "/etc/nftables.d/*.nft"' not in nft_text:
            errors.append("nftables.conf must include '/etc/nftables.d/*.nft' for modular recipe extensions (P0-F2.4)")

    nm_conn = root / "meta-nextoomio/recipes-connectivity/networkmanager/files/10-default-wired.nmconnection"
    if nm_conn.is_file():
        conn_text = nm_conn.read_text(encoding="utf-8")
        if "type=ethernet" not in conn_text:
            errors.append("10-default-wired.nmconnection must configure type=ethernet")
        if "method=auto" not in conn_text:
            errors.append("10-default-wired.nmconnection must configure method=auto for DHCP (AC1)")

    nm_preset = root / "meta-nextoomio/recipes-connectivity/networkmanager/files/99-nextoomio.preset"
    if nm_preset.is_file():
        preset_text = nm_preset.read_text(encoding="utf-8")
        if "disable NetworkManager-wait-online.service" not in preset_text:
            errors.append("NetworkManager preset must disable NetworkManager-wait-online.service (AD-21, AC4)")

    ts_preset = root / "meta-nextoomio/recipes-core/systemd/files/99-timesync.preset"
    if ts_preset.is_file():
        ts_text = ts_preset.read_text(encoding="utf-8")
        if "disable systemd-time-wait-sync.service" not in ts_text:
            errors.append("systemd preset must disable systemd-time-wait-sync.service (AD-21, AC5)")

    # D-007 / P0-F3.2 AC4: Image SSH server configuration, single image baseline, and removal of package manager/debug tooling
    if image_bb.is_file():
        image_text = image_bb.read_text(encoding="utf-8")
        m_remove = re.search(r'IMAGE_FEATURES:remove\s*=\s*["\']([^"\']+)["\']', image_text)
        if m_remove and "ssh-server-openssh" in m_remove.group(1):
            errors.append(
                "nextoomio-image.bb: IMAGE_FEATURES:remove must not contain 'ssh-server-openssh'; "
                "SSH server is installed stopped in the base image and activated via key file (D-007, AD-23)"
            )
        if not m_remove or "package-management" not in m_remove.group(1):
            errors.append(
                "nextoomio-image.bb: IMAGE_FEATURES:remove must contain 'package-management' (P0-F3.2 AC4)"
            )
        if not m_remove or "debug-tweaks" not in m_remove.group(1):
            errors.append(
                "nextoomio-image.bb: IMAGE_FEATURES:remove must contain 'debug-tweaks' (P0-F3.2 AC4)"
            )
        for pkg in ("openssh-sshd", "openssh-keygen", "nextoomio-ssh-keys"):
            if pkg not in image_text:
                errors.append(
                    f"nextoomio-image.bb: IMAGE_INSTALL must include '{pkg}' for key-activated SSH access (AD-23, P0-F3.1)"
                )

    # P0-F3.1 / P0-F3.2: OpenSSH hardened server configuration (AC2, AC5, AD-23)
    sshd_conf = root / "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio"
    if sshd_conf.is_file():
        conf_text = sshd_conf.read_text(encoding="utf-8")
        if re.search(r"^\s*Protocol\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio: Protocol directive is obsolete/ignored and must be omitted (CQ-R14)")
        if re.search(r"^\s*Subsystem\s+sftp\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio: Subsystem sftp must be omitted because sftp-server is not installed (CQ-R14)")
        if not re.search(r"^\s*PasswordAuthentication\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'PasswordAuthentication no' (AC2, AC5)")
        if re.search(r"^\s*PasswordAuthentication\s+yes\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must NOT enable 'PasswordAuthentication yes' (AC5)")
        if not re.search(r"^\s*KbdInteractiveAuthentication\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'KbdInteractiveAuthentication no' (AC2, AC5)")
        if re.search(r"^\s*KbdInteractiveAuthentication\s+yes\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must NOT enable 'KbdInteractiveAuthentication yes' (AC5)")
        if re.search(r"^\s*PermitRootLogin\s+yes\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must NOT configure 'PermitRootLogin yes' (AC5)")
        if not re.search(r"^\s*AuthorizedKeysFile\s+/run/ssh/authorized_keys\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AuthorizedKeysFile /run/ssh/authorized_keys' for volatile storage (AD-23, AC3)")
        if not re.search(r"^\s*AllowAgentForwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AllowAgentForwarding no' (AC2)")
        if not re.search(r"^\s*AllowTcpForwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AllowTcpForwarding no' (AC2)")
        if not re.search(r"^\s*X11Forwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'X11Forwarding no' (AC2)")
        if not re.search(r"^\s*HostKey\s+/data/ssh/", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure HostKey paths on /data/ssh/ persistent partition (AC5)")

    # P0-F3.1 / P0-F3.2: OpenSSH systemd preset and bbappend (AC1, AC5)
    ssh_preset = root / "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset"
    if ssh_preset.is_file():
        preset_text = ssh_preset.read_text(encoding="utf-8")
        if not re.search(r"^disable\s+sshd\.service\b", preset_text, re.MULTILINE):
            errors.append("99-nextoomio-ssh.preset must contain 'disable sshd.service' (AD-23, AC1, AC5)")
        if re.search(r"^enable\s+sshd\.service\b", preset_text, re.MULTILINE):
            errors.append("99-nextoomio-ssh.preset must NOT contain 'enable sshd.service' (AC5)")
        if not re.search(r"^disable\s+sshd\.socket\b", preset_text, re.MULTILINE):
            errors.append("99-nextoomio-ssh.preset must contain 'disable sshd.socket' (AC1, AC5)")
        if not re.search(r"^disable\s+sshd@\.service\b", preset_text, re.MULTILINE):
            errors.append("99-nextoomio-ssh.preset must contain 'disable sshd@.service' (AC1, AC5)")

    ssh_dropin = root / "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf"
    if ssh_dropin.is_file():
        dropin_text = ssh_dropin.read_text(encoding="utf-8")
        if not re.search(r"^ConditionPathExists=/run/ssh/authorized_keys\b", dropin_text, re.MULTILINE):
            errors.append(
                "10-authorized-keys.conf must configure 'ConditionPathExists=/run/ssh/authorized_keys' "
                "to ensure sshd never starts without validated keys (AD-23, AC1, AC5)"
            )

    ssh_bbappend = root / "meta-nextoomio/recipes-connectivity/openssh/openssh_%.bbappend"
    if ssh_bbappend.is_file():
        bbappend_text = ssh_bbappend.read_text(encoding="utf-8")
        if not re.search(r'SYSTEMD_AUTO_ENABLE:\$\{PN\}-sshd\s*=\s*["\']disable["\']', bbappend_text):
            errors.append(
                "openssh_%.bbappend must configure SYSTEMD_AUTO_ENABLE:${PN}-sshd = 'disable' (AD-23, AC1, AC5)"
            )
        if re.search(r'SYSTEMD_AUTO_ENABLE:\$\{PN\}-sshd\s*=\s*["\']enable["\']', bbappend_text):
            errors.append(
                "openssh_%.bbappend must NOT configure SYSTEMD_AUTO_ENABLE:${PN}-sshd = 'enable' (AC5)"
            )
        if "systemd-sshd-service-mode" not in bbappend_text:
            errors.append(
                "openssh_%.bbappend must append PACKAGECONFIG with 'systemd-sshd-service-mode' "
                "to ensure sshd.service unit exists in pinned OE-core release (AC1)"
            )

    # P0-F3.1 / P0-F3.2: nextoomio-ssh-keys service ordering (CQ-R1, AC1, AC5)
    ssh_keys_service = root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys.service"
    if ssh_keys_service.is_file():
        service_text = ssh_keys_service.read_text(encoding="utf-8")
        if not re.search(r"^After=.*\bnftables\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify After=... nftables.service (CQ-R1, AC1)")
        if not re.search(r"^Wants=.*\bnftables\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify Wants=... nftables.service (CQ-R1, AC1)")
        if not re.search(r"^Before=.*\bsshd\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify Before=sshd.service (AC1)")
        if not re.search(r"^RequiresMountsFor=.*(/boot/select|/data)", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify RequiresMountsFor=/boot/select /data")

    # P0-F3.1 / P0-F3.2: Key validator setup script (AC3, AC4, AD-23)
    setup_script = root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh"
    if setup_script.is_file():
        script_text = setup_script.read_text(encoding="utf-8")
        if re.search(r"nft\s+delete\s+rule\s+inet\s+filter\s+input\s+tcp\s+dport\s+22\s+accept", script_text):
            errors.append(
                "nextoomio-ssh-keys-setup.sh contains invalid 'nft delete rule ... accept'; "
                "nftables rule deletion requires rule handle lookup (CQ-R1, CQ-R2, AC3)"
            )
        if not re.search(r"nft\s+delete\s+rule\s+inet\s+filter\s+input\s+handle\b", script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must delete nftables rules using rule handles (CQ-R1, AC3)")
        if not re.search(r"missing_validator_utility", script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must fail closed with diagnostic when ssh-keygen is missing (CQ-R1, AC4)")
        if "sha256-uncomputed" in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must not record uncomputed fingerprints; validation must fail closed (CQ-R1, AC4)")
        if not re.search(r"missing_host_keys", script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must fail closed if persistent host keys are missing or unreadable (CQ-R1, AC5)")
        if not re.search(r'\[\s*"\$FILE_SIZE"\s+-gt\s+16384\s*\]', script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must enforce 16 KiB size limit (AC4)")
        if not re.search(r'\[\s*"\$KEY_COUNT"\s+-gt\s+16\s*\]', script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must enforce maximum 16 keys limit (AC4)")
        if not re.search(r'\[\s*"\$BITS"\s+-lt\s+3072\s*\]', script_text):
            errors.append("nextoomio-ssh-keys-setup.sh must enforce minimum 3072 bits for RSA keys (AC4)")
        if "authorized_keys" not in script_text or "authorized_keys.txt" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must check for authorized_keys and authorized_keys.txt (AC1)")
        if "/run/ssh/authorized_keys" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must install keys to volatile storage /run/ssh/authorized_keys (AD-23)")
        if "/data/ssh" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must initialize host keys on /data/ssh (AC5)")
        if "/run/nextoomio/diagnostics/ssh.json" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must record diagnostics to /run/nextoomio/diagnostics/ssh.json (AC3, AC4)")
        if "/run/nextoomio/diagnostics/ssh.txt" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must record diagnostics text to /run/nextoomio/diagnostics/ssh.txt (AC3)")
        if "systemctl start --no-block sshd.service" not in script_text:
            errors.append("nextoomio-ssh-keys-setup.sh must start sshd with --no-block to prevent systemd ordering deadlock (CQ-R1, AC1)")

    # P0-F3.1 / P0-F3.2: Stop script cleanup check (AC3, CQ-R1, CQ-R2)
    stop_script = root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-stop.sh"
    if stop_script.is_file():
        stop_text = stop_script.read_text(encoding="utf-8")
        if re.search(r"nft\s+delete\s+rule\s+inet\s+filter\s+input\s+tcp\s+dport\s+22\s+accept", stop_text):
            errors.append(
                "nextoomio-ssh-keys-stop.sh contains invalid 'nft delete rule ... accept'; "
                "nftables rule deletion requires rule handle lookup (CQ-R1, CQ-R2, AC3)"
            )
        if not re.search(r"nft\s+delete\s+rule\s+inet\s+filter\s+input\s+handle\b", stop_text):
            errors.append("nextoomio-ssh-keys-stop.sh must delete nftables rules using rule handles (CQ-R1, AC3)")
        if "/run/ssh/authorized_keys" not in stop_text:
            errors.append("nextoomio-ssh-keys-stop.sh must purge volatile keys at /run/ssh/authorized_keys (AC3)")
        if "/run/nextoomio/diagnostics/ssh.txt" not in stop_text:
            errors.append("nextoomio-ssh-keys-stop.sh must update diagnostics text at /run/nextoomio/diagnostics/ssh.txt (AC3)")

    # P0-F3.2 AC4: CI workflow artifact upload publishes package manifest
    yocto_wf = root / ".github/workflows/yocto-build.yml"
    if yocto_wf.is_file():
        wf_text = yocto_wf.read_text(encoding="utf-8")
        if "*.manifest" not in wf_text and "*.rootfs.manifest" not in wf_text:
            errors.append(
                ".github/workflows/yocto-build.yml: artifact upload must publish package manifest (*.manifest, AC4)"
            )

    # D-011: Validate rpi-config include configuration for hardware profile overlay
    rpi_config_bbappend = root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend"
    if rpi_config_bbappend.is_file():
        cfg_text = rpi_config_bbappend.read_text(encoding="utf-8")
        if "include nextoomio-hardware.txt" not in cfg_text:
            errors.append(
                "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend must configure 'include nextoomio-hardware.txt' "
                "in RPI_EXTRA_CONFIG to include dynamic hardware overlay (D-011)"
            )
        if "dtoverlay=hifiberry-amp" in cfg_text:
            errors.append(
                "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend must not hardcode sound-card overlay directly; "
                "overlay must be derived from hardware profile into nextoomio-hardware.txt (D-011)"
            )

    # P0-F6.1 / D-011 / D-012: Validate nextoomio-audio-profile recipe, service, and setup script
    ap_bb = root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/nextoomio-audio-profile_0.0.1.bb"
    if ap_bb.is_file():
        ap_text = ap_bb.read_text(encoding="utf-8")
        if "inherit" not in ap_text or "deploy" not in ap_text:
            errors.append("nextoomio-audio-profile_0.0.1.bb must inherit deploy (D-011)")
        if "inherit" not in ap_text or "systemd" not in ap_text:
            errors.append("nextoomio-audio-profile_0.0.1.bb must inherit systemd (D-012, AC2)")
        if "nextoomio-hardware.txt" not in ap_text:
            errors.append("nextoomio-audio-profile_0.0.1.bb must generate and deploy nextoomio-hardware.txt (D-011)")
        if "hardware-profile.json" not in ap_text:
            errors.append("nextoomio-audio-profile_0.0.1.bb must install hardware-profile.json to /etc/nextoomio/ (D-011)")
        if "SYSTEMD_SERVICE:${PN}" not in ap_text or "nextoomio-audio-profile.service" not in ap_text:
            errors.append("nextoomio-audio-profile_0.0.1.bb must declare SYSTEMD_SERVICE:${PN} = 'nextoomio-audio-profile.service'")
        if "grep -A" in ap_text:
            errors.append(
                "nextoomio-audio-profile_0.0.1.bb: must not use 'grep -A' to parse JSON profile; "
                "use python3 -c / json (CQ-R1, CQ-R2)"
            )
        if "python3" not in ap_text or "json" not in ap_text:
            errors.append(
                "nextoomio-audio-profile_0.0.1.bb: do_compile must use python3 and json to parse hardware profile (CQ-R1)"
            )

        # CQ-R1 / CQ-R2 / CQ-R4 / CQ-M7: Test actual derivation of nextoomio-hardware.txt from reference profile
        if profile_path.is_file():
            include_text, err = derive_hardware_boot_include_from_recipe(ap_bb, profile_path, "hifiberry-amp")
            if err:
                errors.append(f"nextoomio-audio-profile_0.0.1.bb: failed to derive boot include: {err}")
            else:
                overlay_lines = [line.strip() for line in include_text.splitlines() if line.strip().startswith("dtoverlay=")]
                if not overlay_lines:
                    errors.append(
                        "nextoomio-audio-profile_0.0.1.bb: derived nextoomio-hardware.txt missing 'dtoverlay=' entry"
                    )
                else:
                    for ol in overlay_lines:
                        if ol != "dtoverlay=hifiberry-amp":
                            errors.append(
                                f"nextoomio-audio-profile_0.0.1.bb: derived invalid overlay line '{ol}'; "
                                f"expected 'dtoverlay=hifiberry-amp' for hifiberry-amp profile (CQ-R1, CQ-R2)"
                            )

    ap_service = root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile.service"
    if ap_service.is_file():
        svc_text = ap_service.read_text(encoding="utf-8")
        if not re.search(r"^After=.*\bsound\.target\b", svc_text, re.MULTILINE):
            errors.append("nextoomio-audio-profile.service must specify After=... sound.target")
        if not re.search(r"^Before=.*\bpipewire\.service\b", svc_text, re.MULTILINE):
            errors.append("nextoomio-audio-profile.service must specify Before=pipewire.service (AC2)")
        if "/usr/sbin/nextoomio-audio-profile-setup" not in svc_text:
            errors.append("nextoomio-audio-profile.service must execute /usr/sbin/nextoomio-audio-profile-setup")

    ap_script = root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile-setup.sh"
    if ap_script.is_file():
        sh_text = ap_script.read_text(encoding="utf-8")
        if "/etc/nextoomio/hardware-profile.json" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must check /etc/nextoomio/hardware-profile.json (D-011)")
        if "amixer" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must use amixer to configure mixer level (AC2, D-012)")
        if "amixer -c" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must address sound card explicitly via 'amixer -c' (AC1, AC2)")
        if "alsa_card_id" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must read alsa_card_id from hardware profile (AC1, AC5)")
        if "/run/nextoomio/diagnostics/audio.json" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must record diagnostics to /run/nextoomio/diagnostics/audio.json")
        if "/run/nextoomio/diagnostics/audio.txt" not in sh_text:
            errors.append("nextoomio-audio-profile-setup.sh must record diagnostics text to /run/nextoomio/diagnostics/audio.txt")

    # P0-F4.2 / UPD-03 / UPD-04 / AD-13: Update file and SWUpdate configuration checks
    if distro_conf.is_file():
        conf_text = distro_conf.read_text(encoding="utf-8")
        if "image_types_nextoomio" not in conf_text:
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf: IMAGE_CLASSES must append "
                "'image_types_nextoomio' to enable .swu update file generation (P0-F4.2, D-008)"
            )
        if "SWUPDATE_IMAGES" not in conf_text:
            errors.append(
                "meta-nextoomio/conf/distro/nextoomio.conf: SWUPDATE_IMAGES must be defined"
            )

    if image_bb.is_file():
        img_text = image_bb.read_text(encoding="utf-8")
        m_fstypes = re.search(r'IMAGE_FSTYPES\s*=\s*["\']([^"\']+)["\']', img_text)
        if not m_fstypes or "swu" not in m_fstypes.group(1).split():
            errors.append(
                "meta-nextoomio/recipes-core/images/nextoomio-image.bb: IMAGE_FSTYPES must contain 'swu' "
                "to produce update package alongside flashable image (P0-F4.2, AC1, D-008)"
            )

    types_class = root / "meta-nextoomio/classes/image_types_nextoomio.bbclass"
    if types_class.is_file():
        class_text = types_class.read_text(encoding="utf-8")
        if "inherit image_types" not in class_text:
            errors.append("image_types_nextoomio.bbclass must inherit image_types")
        if 'IMAGE_TYPES += "swu"' not in class_text:
            errors.append("image_types_nextoomio.bbclass must declare 'IMAGE_TYPES += \"swu\"'")
        if 'IMAGE_TYPEDEP:swu = "ext4.gz"' not in class_text:
            errors.append("image_types_nextoomio.bbclass must declare 'IMAGE_TYPEDEP:swu = \"ext4.gz\"'")
        if "NEXTOOMIO_BOOT_SLOT_MAXSIZE" not in class_text or "268435456" not in class_text:
            errors.append(
                "image_types_nextoomio.bbclass must enforce NEXTOOMIO_BOOT_SLOT_MAXSIZE = 268435456 (256 MiB, AC3)"
            )
        if "NEXTOOMIO_ROOT_SLOT_MAXSIZE" not in class_text or "3221225472" not in class_text:
            errors.append(
                "image_types_nextoomio.bbclass must enforce NEXTOOMIO_ROOT_SLOT_MAXSIZE = 3221225472 (3072 MiB, AC3)"
            )
        if "NEXTOOMIO_STAGING_RESERVATION_MAXSIZE" not in class_text or "1073741824" not in class_text:
            errors.append(
                "image_types_nextoomio.bbclass must enforce NEXTOOMIO_STAGING_RESERVATION_MAXSIZE = 1073741824 (1 GiB, AC3)"
            )
        if "SWUPDATE_SIGNING" not in class_text or re.search(r'SWUPDATE_SIGNING\s*=\s*["\'](?!["\'])', class_text):
            errors.append(
                "image_types_nextoomio.bbclass must set SWUPDATE_SIGNING = \"\" (signing disabled per UPD-03, AC2)"
            )
        for limit_check in ("BOOT_EXPANDED_SIZE", "ROOT_EXPANDED_SIZE", "SWU_SIZE"):
            if limit_check not in class_text:
                errors.append(f"image_types_nextoomio.bbclass must check {limit_check} against slot/staging limit (AC3)")

    sw_desc = root / "meta-nextoomio/recipes-support/swupdate/files/sw-description"
    if sw_desc.is_file():
        desc_text = sw_desc.read_text(encoding="utf-8")
        if not re.search(r'version\s*=\s*["\']0\.0\.1-dev["\']', desc_text):
            errors.append("sw-description must declare version matching DISTRO_VERSION ('0.0.1-dev') (AC1, AC4)")
        if "hardware-compatibility" not in desc_text or "raspberrypi4-64" not in desc_text:
            errors.append("sw-description must declare hardware-compatibility containing 'raspberrypi4-64' (AC1)")
        if "config-schema-range" not in desc_text:
            errors.append("sw-description must declare readable config-schema-range (AC1)")
        if "sha256" not in desc_text:
            errors.append("sw-description must declare sha256 checksums for artifacts (AC1)")
        if "copy1" not in desc_text or "copy2" not in desc_text:
            errors.append("sw-description must configure dual-slot selections copy1 and copy2 (AC1)")
        if "/dev/mmcblk0p2" not in desc_text or "/dev/mmcblk0p5" not in desc_text:
            errors.append("sw-description copy1 must target slot A partitions (/dev/mmcblk0p2, /dev/mmcblk0p5)")
        if "/dev/mmcblk0p3" not in desc_text or "/dev/mmcblk0p6" not in desc_text:
            errors.append("sw-description copy2 must target slot B partitions (/dev/mmcblk0p3, /dev/mmcblk0p6)")
        if re.search(r'signature\s*=\s*true', desc_text, re.IGNORECASE):
            errors.append("sw-description must NOT require signature verification (UPD-03, AC2)")

    sw_bbappend = root / "meta-nextoomio/recipes-support/swupdate/swupdate_%.bbappend"
    if sw_bbappend.is_file():
        app_text = sw_bbappend.read_text(encoding="utf-8")
        if 'SWUPDATE_SIGNING = ""' not in app_text:
            errors.append("swupdate_%.bbappend must set SWUPDATE_SIGNING = \"\" (UPD-03, AC2)")
        if "swupdate.cfg" not in app_text or "hwrevision" not in app_text:
            errors.append("swupdate_%.bbappend must install swupdate.cfg and hwrevision")

    hwrev_file = root / "meta-nextoomio/recipes-support/swupdate/files/hwrevision"
    if hwrev_file.is_file():
        hwrev_text = hwrev_file.read_text(encoding="utf-8")
        if not re.search(r"^raspberrypi4-64\s+1\.0", hwrev_text.strip()):
            errors.append("hwrevision must specify 'raspberrypi4-64 1.0' matching hardware-compatibility declaration")

    update_img_bb = root / "meta-nextoomio/recipes-support/swupdate/nextoomio-update-image.bb"
    if update_img_bb.is_file():
        up_text = update_img_bb.read_text(encoding="utf-8")
        if "inherit swupdate" not in up_text:
            errors.append("nextoomio-update-image.bb must inherit swupdate")
        if "IMAGE_DEPENDS" not in up_text or "nextoomio-image" not in up_text:
            errors.append("nextoomio-update-image.bb must declare IMAGE_DEPENDS = 'nextoomio-image'")
        if "SWUPDATE_IMAGES" not in up_text or "nextoomio-image" not in up_text:
            errors.append("nextoomio-update-image.bb must declare SWUPDATE_IMAGES containing 'nextoomio-image'")
        if 'SWUPDATE_SIGNING = ""' not in up_text:
            errors.append("nextoomio-update-image.bb must set SWUPDATE_SIGNING = \"\" (UPD-03, AC2)")

    kas_base = root / "kas/base.yml"
    if kas_base.is_file():
        kas_text = kas_base.read_text(encoding="utf-8")
        if "nextoomio-update-image" not in kas_text:
            errors.append("kas/base.yml: target must include nextoomio-update-image")

    if yocto_wf.is_file():
        wf_text = yocto_wf.read_text(encoding="utf-8")
        if "*.swu" not in wf_text:
            errors.append(".github/workflows/yocto-build.yml: artifact upload must publish *.swu update file (AC5)")
        if "bitbake -p nextoomio-image nextoomio-update-image" not in wf_text:
            errors.append(".github/workflows/yocto-build.yml: parse-only check must parse nextoomio-image and nextoomio-update-image")

    # Component register check in ARCHITECTURE.md section 10
    arch_doc = root / "ARCHITECTURE.md"
    if arch_doc.is_file():
        arch_text = arch_doc.read_text(encoding="utf-8")
        if not re.search(r"\|\s*OpenSSH\s*\|\s*v0\.0\.1\s*\|", arch_text):
            errors.append("ARCHITECTURE.md section 10 register must record OpenSSH row (DoD item 4)")

    return errors


def run_self_tests() -> int:
    """Run AC5 automated self-tests verifying check failures on deliberately wrong configurations."""
    print("Running check_yocto_layer self-tests (AC5 automated CI verification)...")

    # Baseline: valid repo must pass
    baseline_errors = validate_layer(ROOT)
    assert not baseline_errors, f"Baseline layer validation failed: {baseline_errors}"
    print("  test_check_passes_on_valid_configuration: OK")

    def copy_repo_subset(src: pathlib.Path, dst: pathlib.Path, rel_paths: list[str]) -> None:
        for rp in rel_paths:
            src_f = src / rp
            dst_f = dst / rp
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            dst_f.write_text(src_f.read_text(encoding="utf-8"), encoding="utf-8")

    all_layer_files = [
        "meta-nextoomio/COPYING.MIT",
        "meta-nextoomio/conf/layer.conf",
        "meta-nextoomio/conf/distro/nextoomio.conf",
        "meta-nextoomio/files/wic/nextoomio-ab.wks.in",
        "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json",
        "meta-nextoomio/recipes-core/images/nextoomio-image.bb",
        "meta-nextoomio/recipes-core/images/nextoomio-image-dev.bb",
        "meta-nextoomio/recipes-bsp/bootfiles/rpi-autoboot.bb",
        "meta-nextoomio/recipes-bsp/bootfiles/rpi-cmdline.bbappend",
        "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend",
        "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd_0.0.1.bb",
        "meta-nextoomio/recipes-nextoomio/nx-sysd/nx-sysd-crates.inc",
        "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/nextoomio-audio-profile_0.0.1.bb",
        "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile.service",
        "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile-setup.sh",
        "kas/base.yml",
        "kas/rpi4-64.yml",
        "kas/ci-hosted.yml",
        "meta-nextoomio/recipes-connectivity/nftables/nftables_%.bbappend",
        "meta-nextoomio/recipes-connectivity/nftables/files/nftables.conf",
        "meta-nextoomio/recipes-connectivity/nftables/files/00-default.nft",
        "meta-nextoomio/recipes-connectivity/networkmanager/networkmanager_%.bbappend",
        "meta-nextoomio/recipes-connectivity/networkmanager/files/10-default-wired.nmconnection",
        "meta-nextoomio/recipes-connectivity/networkmanager/files/99-nextoomio.preset",
        "meta-nextoomio/recipes-connectivity/avahi/avahi_%.bbappend",
        "meta-nextoomio/recipes-connectivity/avahi/files/avahi-daemon.conf",
        "meta-nextoomio/recipes-core/base-files/base-files_%.bbappend",
        "meta-nextoomio/recipes-core/systemd/systemd_%.bbappend",
        "meta-nextoomio/recipes-core/systemd/files/99-timesync.preset",
        "meta-nextoomio/recipes-connectivity/openssh/openssh_%.bbappend",
        "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio",
        "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset",
        "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf",
        "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/nextoomio-ssh-keys_0.0.1.bb",
        "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys.service",
        "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh",
        "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-stop.sh",
        "meta-nextoomio/classes/image_types_nextoomio.bbclass",
        "meta-nextoomio/recipes-support/swupdate/swupdate_%.bbappend",
        "meta-nextoomio/recipes-support/swupdate/nextoomio-update-image.bb",
        "meta-nextoomio/recipes-support/swupdate/files/sw-description",
        "meta-nextoomio/recipes-support/swupdate/files/swupdate.cfg",
        "meta-nextoomio/recipes-support/swupdate/files/hwrevision",
        ".github/workflows/yocto-build.yml",
        "ARCHITECTURE.md",
    ]

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_root = pathlib.Path(tmp_str)
        copy_repo_subset(ROOT, tmp_root, all_layer_files)

        # 1. AC5: Fail when 10-authorized-keys.conf drop-in is missing ConditionPathExists
        dropin = tmp_root / "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf"
        orig_dropin = dropin.read_text(encoding="utf-8")
        dropin.write_text("[Unit]\n# missing condition\n", encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("ConditionPathExists" in e for e in errs), f"Expected ConditionPathExists failure: {errs}"
        dropin.write_text(orig_dropin, encoding="utf-8")
        print("  test_check_fails_when_sshd_condition_missing: OK")

        # 2. AC5: Fail when 99-nextoomio-ssh.preset enables sshd.service
        preset = tmp_root / "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset"
        orig_preset = preset.read_text(encoding="utf-8")
        preset.write_text("enable sshd.service\n", encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("enable sshd.service" in e for e in errs), f"Expected enable sshd.service failure: {errs}"
        preset.write_text(orig_preset, encoding="utf-8")
        print("  test_check_fails_when_sshd_preset_enables_service: OK")

        # 3. AC5: Fail when SYSTEMD_AUTO_ENABLE:${PN}-sshd is set to enable
        bbappend = tmp_root / "meta-nextoomio/recipes-connectivity/openssh/openssh_%.bbappend"
        orig_bbappend = bbappend.read_text(encoding="utf-8")
        bbappend.write_text(orig_bbappend.replace('"disable"', '"enable"'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("SYSTEMD_AUTO_ENABLE" in e for e in errs), f"Expected SYSTEMD_AUTO_ENABLE failure: {errs}"
        bbappend.write_text(orig_bbappend, encoding="utf-8")
        print("  test_check_fails_when_sshd_auto_enable_active: OK")

        # 4. AC5: Fail when PasswordAuthentication yes is configured
        sshd_conf = tmp_root / "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio"
        orig_sshd_conf = sshd_conf.read_text(encoding="utf-8")
        sshd_conf.write_text(orig_sshd_conf.replace("PasswordAuthentication no", "PasswordAuthentication yes"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("PasswordAuthentication" in e for e in errs), f"Expected PasswordAuthentication failure: {errs}"
        sshd_conf.write_text(orig_sshd_conf, encoding="utf-8")
        print("  test_check_fails_when_password_authentication_enabled: OK")

        # 5. AC5: Fail when KbdInteractiveAuthentication yes is configured
        sshd_conf.write_text(orig_sshd_conf.replace("KbdInteractiveAuthentication no", "KbdInteractiveAuthentication yes"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("KbdInteractiveAuthentication" in e for e in errs), f"Expected KbdInteractive failure: {errs}"
        sshd_conf.write_text(orig_sshd_conf, encoding="utf-8")
        print("  test_check_fails_when_kbd_interactive_enabled: OK")

        # 6. AC5: Fail when PermitRootLogin yes is configured
        sshd_conf.write_text(orig_sshd_conf.replace("PermitRootLogin prohibit-password", "PermitRootLogin yes"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("PermitRootLogin" in e for e in errs), f"Expected PermitRootLogin failure: {errs}"
        sshd_conf.write_text(orig_sshd_conf, encoding="utf-8")
        print("  test_check_fails_when_permit_root_login_yes: OK")

        # 7. AC4: Fail when package-management is not removed from nextoomio-image.bb
        img_bb = tmp_root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
        orig_img_bb = img_bb.read_text(encoding="utf-8")
        img_bb.write_text(orig_img_bb.replace("package-management", ""), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("package-management" in e for e in errs), f"Expected package-management failure: {errs}"
        img_bb.write_text(orig_img_bb, encoding="utf-8")
        print("  test_check_fails_when_package_management_not_removed: OK")

        # 8. AC4: Fail when package manifest artifact is missing from yocto-build.yml
        wf = tmp_root / ".github/workflows/yocto-build.yml"
        orig_wf = wf.read_text(encoding="utf-8")
        wf.write_text(orig_wf.replace(".manifest", ".nomatch"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("package manifest" in e for e in errs), f"Expected package manifest failure: {errs}"
        wf.write_text(orig_wf, encoding="utf-8")
        print("  test_check_fails_when_manifest_artifact_missing: OK")

        # 9. AC3: Fail when diagnostics text (/run/nextoomio/diagnostics/ssh.txt) is missing from setup script
        setup_sh = tmp_root / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh"
        orig_setup_sh = setup_sh.read_text(encoding="utf-8")
        setup_sh.write_text(orig_setup_sh.replace("/run/nextoomio/diagnostics/ssh.txt", "/tmp/ignored.txt"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("diagnostics text" in e for e in errs), f"Expected diagnostics text failure: {errs}"
        setup_sh.write_text(orig_setup_sh, encoding="utf-8")
        print("  test_check_fails_when_diagnostics_text_missing: OK")

        # 10. AC5 / D-011: Fail when kas/rpi4-64.yml hardcodes dtoverlay
        kas_rpi4 = tmp_root / "kas/rpi4-64.yml"
        orig_kas_rpi4 = kas_rpi4.read_text(encoding="utf-8")
        kas_rpi4.write_text(orig_kas_rpi4 + '\n    RPI_EXTRA_CONFIG:append = "\\ndtoverlay=hifiberry-amp\\n"\n', encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("kas/rpi4-64.yml: sound card overlay must not be hardcoded" in e for e in errs), f"Expected kas overlay failure: {errs}"
        kas_rpi4.write_text(orig_kas_rpi4, encoding="utf-8")
        print("  test_check_fails_when_kas_hardcodes_overlay: OK")

        # 11. AC5 / D-012: Fail when hifiberry-amp.json fixed_hardware_mixer_percent is 100
        prof_file = tmp_root / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json"
        orig_prof = prof_file.read_text(encoding="utf-8")
        prof_file.write_text(orig_prof.replace('"fixed_hardware_mixer_percent": 30', '"fixed_hardware_mixer_percent": 100'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("fixed_hardware_mixer_percent must NOT be 100%" in e for e in errs), f"Expected mixer percent 100 failure: {errs}"
        prof_file.write_text(orig_prof, encoding="utf-8")
        print("  test_check_fails_when_fixed_mixer_percent_is_100: OK")

        # 12. AC5 / D-011: Fail when rpi-config_%.bbappend is missing nextoomio-hardware.txt include
        cfg_bbappend = tmp_root / "meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend"
        orig_cfg_bbappend = cfg_bbappend.read_text(encoding="utf-8")
        cfg_bbappend.write_text("# missing include\n", encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("include nextoomio-hardware.txt" in e for e in errs), f"Expected rpi-config include failure: {errs}"
        cfg_bbappend.write_text(orig_cfg_bbappend, encoding="utf-8")
        print("  test_check_fails_when_rpi_config_missing_hardware_include: OK")

        # 13. AC5 / D-011: Fail when distro conf boot_a files missing nextoomio-hardware.txt
        dist_conf = tmp_root / "meta-nextoomio/conf/distro/nextoomio.conf"
        orig_dist_conf = dist_conf.read_text(encoding="utf-8")
        dist_conf.write_text(orig_dist_conf.replace(" nextoomio-hardware.txt", ""), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("IMAGE_BOOT_FILES_label-boot_a must include 'nextoomio-hardware.txt'" in e for e in errs), f"Expected boot_a hardware.txt failure: {errs}"
        dist_conf.write_text(orig_dist_conf, encoding="utf-8")
        print("  test_check_fails_when_boot_a_missing_hardware_txt: OK")

        # 14. AC5 / D-011: Fail when nextoomio-image.bb missing nextoomio-audio-profile in IMAGE_INSTALL
        img_bb_path = tmp_root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
        orig_img_bb = img_bb_path.read_text(encoding="utf-8")
        img_bb_path.write_text(orig_img_bb.replace("nextoomio-audio-profile", "ignored-pkg"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("IMAGE_INSTALL must include 'nextoomio-audio-profile'" in e for e in errs), f"Expected audio profile in IMAGE_INSTALL failure: {errs}"
        img_bb_path.write_text(orig_img_bb, encoding="utf-8")
        print("  test_check_fails_when_image_missing_audio_profile: OK")

        # 15. AC1 / AC5: Fail when hifiberry-amp.json is missing alsa_card_id
        prof_file = tmp_root / "meta-nextoomio/files/hardware-profiles/hifiberry-amp.json"
        orig_prof = prof_file.read_text(encoding="utf-8")
        prof_file.write_text(orig_prof.replace('"alsa_card_id": "sndrpihifiberry",\n', ''), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("Reference alsa_card_id must be 'sndrpihifiberry'" in e for e in errs), f"Expected alsa_card_id failure: {errs}"
        prof_file.write_text(orig_prof, encoding="utf-8")
        print("  test_check_fails_when_profile_missing_alsa_card_id: OK")

        # 16. AC1 / AC2: Fail when nextoomio-audio-profile-setup.sh does not use amixer -c
        ap_sh_path = tmp_root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile-setup.sh"
        orig_ap_sh = ap_sh_path.read_text(encoding="utf-8")
        ap_sh_path.write_text(orig_ap_sh.replace("amixer -c", "amixer"), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("must address sound card explicitly via 'amixer -c'" in e for e in errs), f"Expected amixer -c failure: {errs}"
        ap_sh_path.write_text(orig_ap_sh, encoding="utf-8")
        print("  test_check_fails_when_audio_setup_omits_card_flag: OK")

        # 17. CQ-R1 / CQ-R2 / CQ-R4 / CQ-M7: Fail when do_compile generates invalid dtoverlay line (e.g. grep -A bug)
        ap_bb_path = tmp_root / "meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/nextoomio-audio-profile_0.0.1.bb"
        orig_ap_bb = ap_bb_path.read_text(encoding="utf-8")
        bad_do_compile = '''do_compile() {
    if [ -n "${NEXTOOMIO_AUDIO_PROFILE}" ] && [ -f "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" ]; then
        OVERLAY=$(grep -o '"dtoverlay"[^,]*' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" | sed -e 's/"dtoverlay"[[:space:]]*:[[:space:]]*"//' -e 's/"//' | tr -d '[:space:]')
        PARAMS=$(grep -A 5 '"dtoverlay_params"' "${S}/${NEXTOOMIO_AUDIO_PROFILE}.json" | grep '"' | sed -e 's/.*"\\(.*\\)".*/\\1/' | tr '\\n' ',' | sed 's/,$//')
        if [ -n "$PARAMS" ]; then
            echo "dtoverlay=${OVERLAY},${PARAMS}" > "${B}/nextoomio-hardware.txt"
        else
            echo "dtoverlay=${OVERLAY}" > "${B}/nextoomio-hardware.txt"
        fi
    fi
}'''
        m_dc = re.search(r"do_compile\s*\(\)\s*\{.*?\n\}", orig_ap_bb, re.DOTALL)
        assert m_dc, "do_compile pattern not found in test"
        ap_bb_path.write_text(orig_ap_bb[:m_dc.start()] + bad_do_compile + orig_ap_bb[m_dc.end():], encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("derived invalid overlay line" in e or "must not use 'grep -A'" in e for e in errs), f"Expected derivation failure: {errs}"
        ap_bb_path.write_text(orig_ap_bb, encoding="utf-8")
        print("  test_check_fails_when_do_compile_derives_invalid_overlay: OK")

        # 18. AC1 / D-008: Fail when IMAGE_FSTYPES in nextoomio-image.bb is missing 'swu'
        img_bb_p = tmp_root / "meta-nextoomio/recipes-core/images/nextoomio-image.bb"
        orig_img_bb_p = img_bb_p.read_text(encoding="utf-8")
        img_bb_p.write_text(orig_img_bb_p.replace('swu', ''), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("IMAGE_FSTYPES must contain 'swu'" in e for e in errs), f"Expected swu in IMAGE_FSTYPES failure: {errs}"
        img_bb_p.write_text(orig_img_bb_p, encoding="utf-8")
        print("  test_check_fails_when_image_fstypes_missing_swu: OK")

        # 19. AC1: Fail when sw-description is missing hardware-compatibility
        sw_desc_p = tmp_root / "meta-nextoomio/recipes-support/swupdate/files/sw-description"
        orig_sw_desc = sw_desc_p.read_text(encoding="utf-8")
        sw_desc_p.write_text(orig_sw_desc.replace('hardware-compatibility', 'ignored_hw'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("hardware-compatibility" in e for e in errs), f"Expected hardware-compatibility failure: {errs}"
        sw_desc_p.write_text(orig_sw_desc, encoding="utf-8")
        print("  test_check_fails_when_sw_description_missing_hardware_compatibility: OK")

        # 20. AC1: Fail when sw-description is missing config-schema-range
        sw_desc_p.write_text(orig_sw_desc.replace('config-schema-range', 'ignored_schema'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("config-schema-range" in e for e in errs), f"Expected config-schema-range failure: {errs}"
        sw_desc_p.write_text(orig_sw_desc, encoding="utf-8")
        print("  test_check_fails_when_sw_description_missing_config_schema_range: OK")

        # 21. AC1: Fail when sw-description is missing sha256 checksums
        sw_desc_p.write_text(orig_sw_desc.replace('sha256', 'md5'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("sha256 checksums" in e for e in errs), f"Expected sha256 failure: {errs}"
        sw_desc_p.write_text(orig_sw_desc, encoding="utf-8")
        print("  test_check_fails_when_sw_description_missing_sha256: OK")

        # 22. AC3: Fail when slot limits in image_types_nextoomio.bbclass are altered
        class_p = tmp_root / "meta-nextoomio/classes/image_types_nextoomio.bbclass"
        orig_class = class_p.read_text(encoding="utf-8")
        class_p.write_text(orig_class.replace('268435456', '999999'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("NEXTOOMIO_BOOT_SLOT_MAXSIZE" in e for e in errs), f"Expected size limit failure: {errs}"
        class_p.write_text(orig_class, encoding="utf-8")
        print("  test_check_fails_when_size_limits_missing_or_altered: OK")

        # 23. AC2: Fail when SWUPDATE_SIGNING is enabled
        class_p.write_text(orig_class.replace('SWUPDATE_SIGNING = ""', 'SWUPDATE_SIGNING = "RSA"'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("SWUPDATE_SIGNING" in e for e in errs), f"Expected SWUPDATE_SIGNING failure: {errs}"
        class_p.write_text(orig_class, encoding="utf-8")
        print("  test_check_fails_when_swupdate_signing_enabled: OK")

        # 24. AC5: Fail when *.swu artifact upload is missing from yocto-build.yml
        wf_p = tmp_root / ".github/workflows/yocto-build.yml"
        orig_wf_p = wf_p.read_text(encoding="utf-8")
        wf_p.write_text(orig_wf_p.replace('*.swu', '*.ignored'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("publish *.swu" in e for e in errs), f"Expected *.swu upload failure: {errs}"
        wf_p.write_text(orig_wf_p, encoding="utf-8")
        print("  test_check_fails_when_swu_artifact_upload_missing: OK")

        # 25. AC1 / D-008: Fail when distro conf is missing image_types_nextoomio
        dist_p = tmp_root / "meta-nextoomio/conf/distro/nextoomio.conf"
        orig_dist_p = dist_p.read_text(encoding="utf-8")
        dist_p.write_text(orig_dist_p.replace('image_types_nextoomio', 'ignored_class'), encoding="utf-8")
        errs = validate_layer(tmp_root)
        assert any("image_types_nextoomio" in e for e in errs), f"Expected image_types_nextoomio failure: {errs}"
        dist_p.write_text(orig_dist_p, encoding="utf-8")
        print("  test_check_fails_when_distro_conf_missing_swupdate_class: OK")

    print("OK: all AC5 automated self-tests passed.")
    return 0


def main() -> int:
    if "--test" in sys.argv:
        return run_self_tests()

    errors = validate_layer(ROOT)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: meta-nextoomio layer, A/B Wic layout, reference hardware profile, kas files, SWUpdate .swu configuration, and D-004/D-005/P0-F3.2/P0-F4.2/P0-F6.1 rules validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
