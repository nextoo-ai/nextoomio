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
    - Partition 7 (`data`): 1536 MiB initial (ext4, grown on first boot to fill the remainder of the card)
    - Total fixed initial layout consumes ~8.2 GiB. A card of at least 16 GB ensures sufficient capacity for the initial layout and the grown `/data` storage partition.

### Workstation Prerequisites

- MicroSD card reader/writer.
- Workstation flashing utility: `bmaptool` (recommended for block-mapped writing) or `dd`.
- CI build artifacts for the image:
  - `nextoomio-image-raspberrypi4-64.rootfs.wic.bz2` (compressed Wic image)
  - `nextoomio-image-raspberrypi4-64.rootfs.wic.bmap` (block map file)
- A developer SSH public key file (e.g., `~/.ssh/id_ed25519.pub`).

## 3. Flashing the Image

1. Insert the blank microSD card into the workstation card reader.
2. Identify the target block device node:
   - On Linux: `lsblk` or `dmesg | tail` (e.g., `/dev/sdX` or `/dev/mmcblk0`).
   - On macOS: `diskutil list` (e.g., `/dev/rdiskN`).

   > [!IMPORTANT]
   > Ensure the target device node represents the microSD card and NOT a workstation system disk.

3. Write the image artifact to the card:

   - **Using `bmaptool` (recommended, fast and verified):**
     ```bash
     sudo bmaptool copy nextoomio-image-raspberrypi4-64.rootfs.wic.bz2 /dev/sdX
     ```

   - **Using `bunzip2` and `dd`:**
     ```bash
     bzcat nextoomio-image-raspberrypi4-64.rootfs.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
     ```

4. Flush buffers to disk:
   ```bash
   sync
   ```

## 4. Verifying the Written Card

Before inserting the card into the Raspberry Pi, verify the partition table and filesystem structure on the workstation.

### Partition Table Layout Check

Inspect the written card with `lsblk` or `fdisk -l /dev/sdX`:

1. Partition table type must be MBR (`msdos`) with fixed disk identifier `0x4e58544d` ([ARCHITECTURE.md](../../ARCHITECTURE.md) section 3.6).
2. The card must present seven partition entries matching the specification:
   - **Partition 1 (`select`):** 64 MiB, type FAT (`vfat`), bootable / active flag set.
   - **Partition 2 (`boot_a`):** 256 MiB, type FAT (`vfat`), labeled `boot_a`.
   - **Partition 3 (`boot_b`):** 256 MiB, type FAT (`vfat`), labeled `boot_b` (empty formatted filesystem).
   - **Partition 5 (`root_a`):** 3072 MiB (3 GiB), type ext4, labeled `root_a`.
   - **Partition 6 (`root_b`):** 3072 MiB (3 GiB), type ext4, labeled `root_b` (empty formatted filesystem).
   - **Partition 7 (`data`):** 1536 MiB initial, type ext4, labeled `data`.

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
   - Slot A boot files (`boot_a`, partition 2) are loaded: kernel (`Image`), device tree (`bcm2711-rpi-4-b.dtb`), and sound card overlay.
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

In accordance with acceptance criterion AC3, [SPECIFICATION.md](../../SPECIFICATION.md) section 2.1, and [ROADMAP.md](../../ROADMAP.md) section 2.1, the exact hardware revisions must be read from the running device and recorded in the test bench record upon initial flash.

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

Record the readings in the bench documentation:

| Component | Specification / Query Command | Recorded Reading |
| --- | --- | --- |
| **Board Model** | `/sys/firmware/devicetree/base/model` | Raspberry Pi 4 Model B |
| **Board Revision** | `grep 'Revision' /proc/cpuinfo` | Recorded upon first flash (e.g., `c03114`) |
| **RAM Capacity** | `free -h` or `/proc/meminfo` | Recorded upon first flash (e.g., `4 GB`) |
| **Board Serial** | `grep 'Serial' /proc/cpuinfo` | Recorded upon first flash |
| **Sound Card** | `aplay -l` / `cat /proc/asound/cards` | `snd_rpi_hifiberry_amp` (`hifiberry-amp`) |
| **Amp+ PCB Revision**| Physical silkscreen inspection | Recorded upon first flash (e.g., `PCB Rev 1.0`) |
| **Storage Card** | MicroSD card label inspection | Recorded upon first flash (e.g., SanDisk 32GB) |
| **Network Interface**| `ip link show eth0` | Wired Gigabit Ethernet `eth0` |

## 8. Initial Audio Smoke Verification

During the first flash, verify that the audio pipeline is operational by listening to a test tone:

```bash
# Play ALSA test tone on the HiFiBerry Amp+
speaker-test -D default -c 2 -t wav -l 1
```

Confirm that the test sound is audible from the connected test speaker. As noted in [ROADMAP.md](../../ROADMAP.md) section 2.1, audible output is confirmed by listening once when someone is physically at the bench device during the initial flash.

## 9. Subsequent Deployments

Once the first flash is complete and the bench device is verified reachable over SSH, all subsequent software iterations and A/B trial deployments are delivered over the network using the update engine:

- The card remains in the device.
- Updates are uploaded over SSH and installed into the inactive slot (`boot_b`/`root_b` or `boot_a`/`root_a`).
- Trial boot, local health check, and remote-confirmed commit proceed automatically without physical card access.
