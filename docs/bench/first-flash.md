# First-Flash Procedure

This document defines the single manual flash procedure to rebuild the nextoomio reference test bench from bare metal ([ROADMAP.md](../../ROADMAP.md) section 2.1).

## 1. Overview and Scope

In accordance with [ROADMAP.md](../../ROADMAP.md) section 2.1 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 4.1, the microSD card is written by hand exactly once. The developer's SSH public key is copied to the card's first FAT partition during this initial flash. Every subsequent system image is deployed over the network through the update engine, without requiring physical card removal or manual flashing.

Physical access to the microSD card is required only for:
1. Bare-metal initial provisioning of the reference bench device (this procedure).
2. Hardware recovery in the event of storage failure or an unreadable partition table / selector.

## 2. Prerequisites and Equipment

### Hardware

- **Reference Board:** Raspberry Pi 4 Model B ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1).
- **Reference Sound Card:** HiFiBerry Amp+ ([SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, identified as `hifiberry-amp`).
- **Power Supply:** 12V–18V DC regulated supply connected to the HiFiBerry Amp+ barrel jack. The Amp+ powers the Raspberry Pi back through the 40-pin GPIO header.
- **Passive Speaker:** 4–8 $\Omega$ test speaker connected to the HiFiBerry Amp+ screw terminal block.
- **Network:** Wired Gigabit Ethernet cable connected to `eth0` with local DHCP service.

> [!CAUTION]
> Do NOT connect a USB-C power supply to the Raspberry Pi 4 while the 12V–18V power supply is connected to the HiFiBerry Amp+. The Amp+ powers the Raspberry Pi directly via the 40-pin GPIO header; connecting a second power supply via USB-C can damage the board.

### Storage Media (Minimum Card Size)

- **Card Specification:** MicroSD card, Class 10 / UHS-I / A1.
- **Minimum Card Size:** At least **16 GB** (16 GiB).
  - *Reason:* [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6 defines a fixed dual A/B layout:
    - Partition 1 (`select`): 64 MiB (FAT)
    - Partition 2 (`boot_a`): 256 MiB (FAT)
    - Partition 3 (`boot_b`): 256 MiB (FAT)
    - Partition 5 (`root_a`): 3072 MiB / 3 GiB (ext4)
    - Partition 6 (`root_b`): 3072 MiB / 3 GiB (ext4)
    - Partition 7 (`data`): 1536 MiB initial (ext4; planned: grown on first boot per [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6; not implemented in the current image)
    - Total fixed initial layout consumes 8256 MiB, which is about 8.06 GiB (8.66 GB). Standard nominal "8 GB" cards provide only ~7.45 GiB usable capacity and cannot fit the 8256 MiB layout, so a card of at least 16 GB is required for the initial layout alone.

### Workstation Prerequisites

- MicroSD card reader/writer.
- Workstation flashing utility: `bmaptool` (recommended for block-mapped writing) or `dd`.
- Compressed Wic image artifact (`*.wic.bz2`) and matching block map (`*.wic.bmap`) obtained from the CI build workflow (see Section 3.1).
- A developer SSH public key file (e.g., `~/.ssh/id_ed25519.pub`).
- GitHub CLI (`gh`) or a web browser to download CI build artifacts.

## 3. Flashing the Image

### 3.1 Obtaining the CI Image Artifact

In accordance with [ROADMAP.md](../../ROADMAP.md) section 2.1, the image is built by CI. A person following this procedure can retrieve the required artifacts directly from GitHub Actions:

1. **Workflow source:**
   - Full BitBake Wic image builds are executed by the workflow `.github/workflows/yocto-build.yml` ("Yocto Image Build").
   - Full images are built and uploaded on every `push` to `main` modifying build paths (`kas/**`, `meta-nextoomio/**`, `.github/workflows/yocto-build.yml`), or via manual `workflow_dispatch` with input `mode: full-image`.
   - Build artifacts are retained for 14 days. Pick the latest successful run on `main` (or a specific tagged release/commit).

2. **Download the artifact:**

   - **Using GitHub CLI (`gh`):**
     ```bash
     # List recent successful Yocto image runs on main
     gh run list --workflow=yocto-build.yml --status=success --branch=main

     # Download the artifact from the selected run ID
     gh run download <RUN_ID> -n "nextoomio-rpi4-64-*"

     # Change into the extracted artifact directory
     cd nextoomio-rpi4-64-*
     ```
     `gh run download` extracts the artifact contents directly into a subdirectory named after the artifact (e.g., `nextoomio-rpi4-64-<sha>/`). No separate `unzip` command is needed when using `gh`.

   - **Using the GitHub Web UI:**
     1. Navigate to the repository's **Actions** tab.
     2. In the left sidebar, select the **Yocto Image Build** workflow.
     3. Select the latest successful run on the `main` branch.
     4. Scroll to the **Artifacts** section at the bottom of the summary page.
     5. Download the artifact named `nextoomio-rpi4-64-<sha>` (downloaded through the browser as a `.zip` archive).
     6. Unpack the zip archive on your workstation:
        ```bash
        unzip nextoomio-rpi4-64-*.zip
        ```

3. **Identify the Wic image and block map:**

   The downloaded artifact directory contains the compressed Wic image (`*.wic.bz2`), block map (`*.wic.bmap`), rootfs manifest (`*.rootfs.manifest`), SBOM archive, and license manifest.

   > [!IMPORTANT]
   > Keep both the `*.wic.bz2` image and its matching `*.wic.bmap` block map in the same directory. When invoked, `bmaptool copy` looks for the matching `.wic.bmap` file in the same directory as the image.

   In BitBake/OpenEmbedded builds, image files are named with a build timestamp (for example, `nextoomio-image-raspberrypi4-64-20261116120000.rootfs.wic.bz2` and matching `nextoomio-image-raspberrypi4-64-20261116120000.rootfs.wic.bmap`, or symlink `nextoomio-image-raspberrypi4-64.rootfs.wic.bz2`).

   List the directory contents and identify your exact filename:
   ```bash
   ls -la nextoomio-image-raspberrypi4-64*
   ```
   In the flashing commands in Section 3.3, substitute your actual filename (e.g. `nextoomio-image-raspberrypi4-64-20261116120000.rootfs.wic.bz2`) for `<IMAGE_FILE>`.

### 3.2 Identifying the Target MicroSD Card Node

1. Insert the blank microSD card into the workstation card reader.
2. Identify the target block device node:
   - On Linux: `lsblk` or `dmesg | tail` (e.g., `/dev/sdX` or `/dev/mmcblk0`).
   - On macOS: `diskutil list` (e.g., `/dev/rdiskN`).

   > [!IMPORTANT]
   > Ensure the target device node represents the microSD card and NOT a workstation system disk.

### 3.3 Writing the Image to the Card

Write the image artifact to the card using either `bmaptool` or `dd`:

- **Using `bmaptool` (recommended, fast and verified):**
  ```bash
  sudo bmaptool copy nextoomio-image-raspberrypi4-64-*.rootfs.wic.bz2 /dev/sdX
  ```
  *(Substitute your actual `.wic.bz2` filename, e.g. `nextoomio-image-raspberrypi4-64-20261116120000.rootfs.wic.bz2`, and replace `/dev/sdX` with your microSD device node. As long as both the `.wic.bz2` and `.wic.bmap` files reside in the same directory, `bmaptool` automatically discovers and uses the block map. If they are in different locations, pass `--bmap <path-to-bmap>` explicitly.)*

- **Using `bzcat` and `dd`:**
  ```bash
  bzcat nextoomio-image-raspberrypi4-64-*.rootfs.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
  ```
  *(Substitute your actual compressed `.wic.bz2` filename and replace `/dev/sdX` with your card device node.)*

Flush buffers to disk:
```bash
sync
```

## 4. Verifying the Written Card

Before inserting the card into the Raspberry Pi, verify the partition table and filesystem structure on the workstation.

### Partition Table Layout Check

Inspect the written card with `lsblk` or `fdisk -l /dev/sdX`:

1. Partition table type must be MBR (`msdos`) with fixed disk identifier `0x4e58544d` ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6).
2. The card must present seven partition entries matching the specification:
   - **Partition 1 (`select`):** 64 MiB, primary FAT (`vfat`), bootable / active flag set.
   - **Partition 2 (`boot_a`):** 256 MiB, primary FAT (`vfat`), labeled `boot_a`.
   - **Partition 3 (`boot_b`):** 256 MiB, primary FAT (`vfat`), labeled `boot_b` (empty formatted filesystem).
   - **Partition 4 (Extended container):** Extended partition container (type `Extended` / `W95 Ext'd (LBA)`) enclosing the logical partitions 5–7.
   - **Partition 5 (`root_a`):** 3072 MiB (3 GiB), logical ext4, labeled `root_a`.
   - **Partition 6 (`root_b`):** 3072 MiB (3 GiB), logical ext4, labeled `root_b` (empty formatted filesystem).
   - **Partition 7 (`data`):** 1536 MiB initial, logical ext4, labeled `data`.

### Partition 1 (`select`) Content Check

Mount partition 1 to verify its contents:

```bash
sudo mkdir -p /mnt/select
sudo mount /dev/sdX1 /mnt/select
```

1. Confirm that `autoboot.txt` exists and contains the initial boot configuration:
   ```ini
   [all]
   tryboot_a_b=1
   boot_partition=2

   [tryboot]
   boot_partition=2
   ```
2. Confirm that partition 1 contains only the selector file and no slot artifacts (no kernels, device-tree blobs, or overlays; those reside exclusively on `boot_a` partition 2 per D-005).

## 5. Copying the Public Key to Partition 1

In accordance with decision AD-23 and [ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.7, SSH access is enabled exclusively by placing a public key file on partition 1 (`select`). Without this file, the SSH daemon remains stopped and the firewall port closed.

### Key File Specifications and Constraints

- **File Name:** Must be named `authorized_keys` or `authorized_keys.txt` in the root directory of partition 1 (`select`).
- **Accepted Key Types:**
  - `ssh-ed25519` (recommended)
  - `ecdsa-sha2-nistp256`
  - `ecdsa-sha2-nistp384`
  - `ecdsa-sha2-nistp521`
  - `ssh-rsa` with a minimum key length of **3072 bits**
- **File Constraints:**
  - Maximum file size: **16 KiB** (16,384 bytes).
  - Maximum key count: **16 keys**.
  - Exactly one public key per line.
  - **No key options:** Key options (e.g., `no-port-forwarding`, `from="..."`) are rejected and will disable SSH access entirely.
  - Line endings: Both UNIX LF (`\n`) and Windows CRLF (`\r\n`) are tolerated.

### Copy Procedure

With partition 1 mounted at `/mnt/select`:

```bash
# Copy developer public key to partition 1
cat ~/.ssh/id_ed25519.pub | sudo tee /mnt/select/authorized_keys > /dev/null

# Set correct file permissions (read/write for owner)
sudo chmod 0644 /mnt/select/authorized_keys

# Confirm file presence and format
ls -l /mnt/select/authorized_keys
cat /mnt/select/authorized_keys
```

Unmount the partition and flush writes:

```bash
sudo umount /mnt/select
sync
```

Eject and safely remove the microSD card from the workstation.

## 6. First Boot and SSH Verification

1. Insert the written microSD card into the Raspberry Pi 4 microSD slot.
2. Connect the wired Ethernet cable from the local network switch/router to the Raspberry Pi `eth0` port.
3. Connect the passive test speaker to the HiFiBerry Amp+ output terminals.
4. Connect the 12V–18V DC power adapter to the HiFiBerry Amp+ DC jack.
5. Apply power. The system initializes:
   - Raspberry Pi bootloader reads `autoboot.txt` on partition 1 (`select`).
   - Slot A boot files (`boot_a`, partition 2, populated by OpenEmbedded `bootimg-partition` in `meta-nextoomio/files/wic/nextoomio-ab.wks.in`) are loaded: 64-bit kernel (`Image`), device tree blob (`bcm2711-rpi-4-b.dtb`), and sound card overlay include `nextoomio-hardware.txt` (deployed by `meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/nextoomio-audio-profile_0.0.1.bb` and included in `config.txt` via `meta-nextoomio/recipes-bsp/bootfiles/rpi-config_%.bbappend`, loading `overlays/hifiberry-amp.dtbo`).
   - Kernel boots with root filesystem on slot A (`root_a`, partition 5, `PARTUUID=4e58544d-05`).
   - On first boot, the `/data` partition (partition 7) is mounted, and `/boot/select` is mounted read-only (`ro`).
   - `nextoomio-ssh-keys.service` runs: validates `/boot/select/authorized_keys`, creates volatile authorized keys in `/run/ssh/authorized_keys`, opens firewall port 22 in `nftables`, and starts `sshd.service`.
6. Identify the assigned IP address via DHCP server leases, network scanner, or mDNS:
   ```bash
   ping -c 3 nextoomio.local
   ```
7. Connect to the device using the matching private key:
   ```bash
   ssh -i ~/.ssh/id_ed25519 root@nextoomio.local
   ```
8. Confirm that authentication succeeds without a password. Password authentication, keyboard-interactive login, and forwarding features are disabled by design.
9. Confirm the read-only selector mount:
   ```bash
   mount | grep '/boot/select'
   # Expected output contains "(ro,"
   ```

## 7. Recording Hardware Revisions

In accordance with acceptance criterion AC3, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, and [ROADMAP.md](../../ROADMAP.md) section 2.1, the exact hardware revisions must be read from the running device upon initial flash and recorded in the story evidence file [`docs/evidence/p0/P0-F5.2.md`](../evidence/p0/P0-F5.2.md) under the **Hardware model and revision** section and the AC3 row of **Observed result**.

Execute the following commands over the SSH session:

```bash
# 1. Read Raspberry Pi model name
cat /sys/firmware/devicetree/base/model
# Example output: Raspberry Pi 4 Model B Rev 1.4

# 2. Read CPU model, serial number, and hardware revision code
grep -E 'Model|Revision|Serial' /proc/cpuinfo
# Example output:
# Model     : Raspberry Pi 4 Model B Rev 1.4
# Revision  : c03114
# Serial    : 10000000xxxxxxxx

# 3. Read physical RAM capacity
free -h
# Example output: MemTotal: 3.8Gi (for 4 GB board)

# 4. Verify sound card detection in ALSA
cat /proc/asound/cards
aplay -l
# Expected output confirms card 0: sndrpihifiberry [snd_rpi_hifiberry_amp]

# 5. Read HiFiBerry Amp+ PCB silkscreen revision
# Visually inspect the silkscreen printed on the top surface of the Amp+ PCB (e.g., "PCB Rev 1.0").
```

### Reference Bench Hardware Inventory Table

Record the readings in [`docs/evidence/p0/P0-F5.2.md`](../evidence/p0/P0-F5.2.md). The table below lists the query command, the expected specification or reference format, and the placeholder to be recorded during first flash on physical hardware:

| Component | Query Command / Inspection Method | Expected Specification / Reference Format | Recorded Reading |
| --- | --- | --- | --- |
| **Board Model** | `cat /sys/firmware/devicetree/base/model` | Raspberry Pi 4 Model B (e.g., `Rev 1.4`) | |
| **Board Revision** | `grep 'Revision' /proc/cpuinfo` | Hardware revision code (e.g., `c03114`) | |
| **RAM Capacity** | `free -h` or `/proc/meminfo` | Physical RAM (e.g., `3.8Gi` for 4 GB board) | |
| **Board Serial** | `grep 'Serial' /proc/cpuinfo` | Unique 16-hex-digit SoC serial number | |
| **Sound Card** | `aplay -l` or `cat /proc/asound/cards` | `snd_rpi_hifiberry_amp` (`hifiberry-amp`) | |
| **Amp+ PCB Revision** | Visual PCB silkscreen inspection | Silkscreen batch/revision (e.g., `PCB Rev 1.0`) | |
| **Storage Card** | MicroSD card label inspection | Card brand and capacity (minimum 16 GB) | |
| **Network Interface** | `ip link show eth0` | Wired Gigabit Ethernet interface `eth0` | |

## 8. Initial Audio Smoke Verification

During the first flash, verify that the audio pipeline is operational by listening to a test tone:

- In `nextoomio-image.bb`, the image installs `alsa-utils`, PipeWire (`pipewire`, `pipewire-alsa`, `pipewire-pulse`, `wireplumber`), and `nextoomio-audio-profile`.
- At boot, `nextoomio-audio-profile.service` runs `nextoomio-audio-profile-setup` (`meta-nextoomio/recipes-nextoomio/nextoomio-audio-profile/files/nextoomio-audio-profile-setup.sh`) to initialize the hardware mixer control (`Master`) to the fixed 30% volume level defined in `hifiberry-amp.json` (D-012).
- To confirm audible output per [ROADMAP.md](../../ROADMAP.md) section 2.1, play a synthesized test tone through ALSA without relying on external WAV audio files:

```bash
# Generate a 440 Hz test tone on the default ALSA output (routed through PipeWire):
speaker-test -D default -c 2 -t sine -f 440 -l 1

# Or target the HiFiBerry hardware PCM device directly:
speaker-test -D plughw:CARD=sndrpihifiberry,DEV=0 -c 2 -t sine -f 440 -l 1
```

Confirm that the test sound is audible from the connected passive test speaker. As noted in [ROADMAP.md](../../ROADMAP.md) section 2.1, audible output is confirmed by listening once when someone is physically at the bench device during the initial flash.

## 9. Subsequent Deployments

Once the first flash is complete and the bench device is verified reachable over SSH, all subsequent software iterations and A/B trial deployments are delivered over the network using the update engine:

- The card remains in the device.
- Updates are uploaded over SSH and installed into the inactive slot (`boot_b`/`root_b` or `boot_a`/`root_a`).
- Trial boot, local health check, and remote-confirmed commit proceed automatically without physical card access.
