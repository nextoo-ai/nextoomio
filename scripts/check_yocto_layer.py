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
        ROOT / "meta-nextoomio/recipes-connectivity/nftables/nftables_%.bbappend",
        ROOT / "meta-nextoomio/recipes-connectivity/nftables/files/nftables.conf",
        ROOT / "meta-nextoomio/recipes-connectivity/nftables/files/00-default.nft",
        ROOT / "meta-nextoomio/recipes-connectivity/networkmanager/networkmanager_%.bbappend",
        ROOT / "meta-nextoomio/recipes-connectivity/networkmanager/files/10-default-wired.nmconnection",
        ROOT / "meta-nextoomio/recipes-connectivity/networkmanager/files/99-nextoomio.preset",
        ROOT / "meta-nextoomio/recipes-connectivity/avahi/avahi_%.bbappend",
        ROOT / "meta-nextoomio/recipes-connectivity/avahi/files/avahi-daemon.conf",
        ROOT / "meta-nextoomio/recipes-core/base-files/base-files_%.bbappend",
        ROOT / "meta-nextoomio/recipes-core/systemd/systemd_%.bbappend",
        ROOT / "meta-nextoomio/recipes-core/systemd/files/99-timesync.preset",
        ROOT / "meta-nextoomio/recipes-connectivity/openssh/openssh_%.bbappend",
        ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio",
        ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset",
        ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf",
        ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/nextoomio-ssh-keys_0.0.1.bb",
        ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys.service",
        ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh",
        ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-stop.sh",
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

    nft_conf = ROOT / "meta-nextoomio/recipes-connectivity/nftables/files/nftables.conf"
    if nft_conf.is_file():
        nft_text = nft_conf.read_text(encoding="utf-8")
        if not re.search(r"type\s+filter\s+hook\s+input\s+priority\s+filter\s*;\s*policy\s+drop\s*;", nft_text):
            errors.append("nftables.conf must configure input chain with 'policy drop;' for default-deny inbound firewall (AC3)")
        if "udp dport 5353 accept" not in nft_text:
            errors.append("nftables.conf must allow mDNS (udp dport 5353 accept) for name resolution (AC2)")
        if 'include "/etc/nftables.d/*.nft"' not in nft_text:
            errors.append("nftables.conf must include '/etc/nftables.d/*.nft' for modular recipe extensions (P0-F2.4)")

    nm_conn = ROOT / "meta-nextoomio/recipes-connectivity/networkmanager/files/10-default-wired.nmconnection"
    if nm_conn.is_file():
        conn_text = nm_conn.read_text(encoding="utf-8")
        if "type=ethernet" not in conn_text:
            errors.append("10-default-wired.nmconnection must configure type=ethernet")
        if "method=auto" not in conn_text:
            errors.append("10-default-wired.nmconnection must configure method=auto for DHCP (AC1)")

    nm_preset = ROOT / "meta-nextoomio/recipes-connectivity/networkmanager/files/99-nextoomio.preset"
    if nm_preset.is_file():
        preset_text = nm_preset.read_text(encoding="utf-8")
        if "disable NetworkManager-wait-online.service" not in preset_text:
            errors.append("NetworkManager preset must disable NetworkManager-wait-online.service (AD-21, AC4)")

    ts_preset = ROOT / "meta-nextoomio/recipes-core/systemd/files/99-timesync.preset"
    if ts_preset.is_file():
        ts_text = ts_preset.read_text(encoding="utf-8")
        if "disable systemd-time-wait-sync.service" not in ts_text:
            errors.append("systemd preset must disable systemd-time-wait-sync.service (AD-21, AC5)")

    # D-007: Image SSH server configuration and single image architecture (AD-23, AC1)
    if image_bb.is_file():
        image_text = image_bb.read_text(encoding="utf-8")
        m_remove = re.search(r'IMAGE_FEATURES:remove\s*=\s*["\']([^"\']+)["\']', image_text)
        if m_remove and "ssh-server-openssh" in m_remove.group(1):
            errors.append(
                "nextoomio-image.bb: IMAGE_FEATURES:remove must not contain 'ssh-server-openssh'; "
                "SSH server is installed stopped in the base image and activated via key file (D-007, AD-23)"
            )
        for pkg in ("openssh-sshd", "openssh-keygen", "nextoomio-ssh-keys"):
            if pkg not in image_text:
                errors.append(
                    f"nextoomio-image.bb: IMAGE_INSTALL must include '{pkg}' for key-activated SSH access (AD-23, P0-F3.1)"
                )

    # P0-F3.1: OpenSSH hardened server configuration (AC2, AC5, AD-23)
    sshd_conf = ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/sshd_config.nextoomio"
    if sshd_conf.is_file():
        conf_text = sshd_conf.read_text(encoding="utf-8")
        if re.search(r"^\s*Protocol\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio: Protocol directive is obsolete/ignored and must be omitted (CQ-R14)")
        if re.search(r"^\s*Subsystem\s+sftp\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio: Subsystem sftp must be omitted because sftp-server is not installed (CQ-R14)")
        if not re.search(r"^\s*PasswordAuthentication\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'PasswordAuthentication no' (AC2)")
        if not re.search(r"^\s*KbdInteractiveAuthentication\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'KbdInteractiveAuthentication no' (AC2)")
        if not re.search(r"^\s*AuthorizedKeysFile\s+/run/ssh/authorized_keys\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AuthorizedKeysFile /run/ssh/authorized_keys' for volatile storage (AD-23, AC3)")
        if not re.search(r"^\s*AllowAgentForwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AllowAgentForwarding no' (AC2)")
        if not re.search(r"^\s*AllowTcpForwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'AllowTcpForwarding no' (AC2)")
        if not re.search(r"^\s*X11Forwarding\s+no\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure 'X11Forwarding no' (AC2)")
        if not re.search(r"^\s*HostKey\s+/data/ssh/ssh_host_ed25519_key\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure persistent HostKey on /data partition (AC5)")
        if not re.search(r"^\s*HostKey\s+/data/ssh/ssh_host_rsa_key\b", conf_text, re.MULTILINE):
            errors.append("sshd_config.nextoomio must configure persistent RSA HostKey on /data partition (AC5)")

    # P0-F3.1: SSH service presets and drop-ins (AC1, AC5, AD-23)
    ssh_preset = ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/99-nextoomio-ssh.preset"
    if ssh_preset.is_file():
        preset_text = ssh_preset.read_text(encoding="utf-8")
        if "disable sshd.service" not in preset_text or "disable sshd.socket" not in preset_text:
            errors.append("99-nextoomio-ssh.preset must disable sshd.service and sshd.socket by default (AD-23)")

    ssh_dropin = ROOT / "meta-nextoomio/recipes-connectivity/openssh/files/10-authorized-keys.conf"
    if ssh_dropin.is_file():
        dropin_text = ssh_dropin.read_text(encoding="utf-8")
        if "ConditionPathExists=/run/ssh/authorized_keys" not in dropin_text:
            errors.append("10-authorized-keys.conf drop-in must specify 'ConditionPathExists=/run/ssh/authorized_keys' (AC5)")

    # P0-F3.1: Validator systemd service ordering and dependencies (AC1, CQ-R1)
    ssh_service = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys.service"
    if ssh_service.is_file():
        service_text = ssh_service.read_text(encoding="utf-8")
        if not re.search(r"^After=.*\bnftables\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify After=... nftables.service to ensure firewall ruleset is loaded before SSH rule (CQ-R1, AC1)")
        if not re.search(r"^Wants=.*\bnftables\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify Wants=... nftables.service (CQ-R1, AC1)")
        if not re.search(r"^Before=.*\bsshd\.service\b", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify Before=sshd.service (AC1)")
        if not re.search(r"^RequiresMountsFor=.*(/boot/select|/data)", service_text, re.MULTILINE):
            errors.append("nextoomio-ssh-keys.service must specify RequiresMountsFor=/boot/select /data")

    # P0-F3.1: Key validator setup script (AC4, AD-23)
    setup_script = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-setup.sh"
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
            errors.append("nextoomio-ssh-keys-setup.sh must record diagnostics to /run/nextoomio/diagnostics/ssh.json (AC4)")

    # P0-F3.1: Stop script cleanup check (AC3, CQ-R1, CQ-R2)
    stop_script = ROOT / "meta-nextoomio/recipes-nextoomio/nextoomio-ssh-keys/files/nextoomio-ssh-keys-stop.sh"
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

    # P0-F3.1: Component register check in ARCHITECTURE.md section 10
    arch_doc = ROOT / "ARCHITECTURE.md"
    if arch_doc.is_file():
        arch_text = arch_doc.read_text(encoding="utf-8")
        if not re.search(r"\|\s*OpenSSH\s*\|\s*v0\.0\.1\s*\|", arch_text):
            errors.append("ARCHITECTURE.md section 10 register must record OpenSSH row (DoD item 4)")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: meta-nextoomio layer, A/B Wic layout, reference hardware profile, kas files, and D-004/D-005 rules validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
