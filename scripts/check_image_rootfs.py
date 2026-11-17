#!/usr/bin/env python3
"""Inspect a compressed ext4 root filesystem without mounting it."""
import argparse
import gzip
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / 'meta-nextoomio/conf/expected-enabled-units.txt'
CHECKED = {'basic.target.wants', 'local-fs.target.wants', 'sysinit.target.wants',
           'multi-user.target.wants', 'sockets.target.wants', 'network-online.target.wants'}


def parse_listing(output):
    """Parse debugfs ls -p records into (name, mode) pairs."""
    result = []
    for line in output.splitlines():
        match = re.fullmatch(r'/\d+/([0-7]+)/\d+/\d+/([^/]+)/[^/]*/?', line.strip())
        if match and match.group(2) not in ('.', '..'):
            result.append((match.group(2), match.group(1)))
    return result


def compare_units(actual, expected):
    errors = []
    for item in sorted(expected - actual):
        errors.append(f'missing enabled unit: {item}')
    for item in sorted(actual - expected):
        if item.split('/', 1)[0] in CHECKED:
            errors.append(f'unexpected enabled unit: {item}')
    if any(item.rsplit('/', 1)[-1] == 'sshd.service' for item in actual):
        errors.append('sshd.service is enabled')
    return errors


def check_fstab(content):
    entries = [line.split('#', 1)[0].split() for line in content.splitlines()]
    entries = [entry for entry in entries if entry]
    errors = []
    roots = [e for e in entries if len(e) >= 4 and e[1] == '/']
    if len(roots) != 1 or 'ro' not in roots[0][3].split(',') or 'rw' in roots[0][3].split(','):
        errors.append('fstab: root must be mounted read-only')
    for mount in ('/boot/select', '/data'):
        if not any(len(e) >= 4 and e[1] == mount for e in entries):
            errors.append(f'fstab: missing {mount}')
    if any(len(e) >= 2 and e[1] == '/boot' for e in entries):
        errors.append('fstab: /boot must not be mounted')
    return errors


def check_release(content, commit=None):
    values = {}
    for line in content.splitlines():
        match = re.fullmatch(r'([A-Z_]+)=(?:"([^"]*)"|([^\s]*))', line.strip())
        if match:
            values[match[1]] = match[2] if match[2] is not None else match[3]
    errors = [f'release: missing {key}' for key in ('IMAGE_VERSION', 'BUILD_ID', 'SOURCE_COMMIT', 'HARDWARE_COMPATIBILITY') if not values.get(key)]
    if values.get('HARDWARE_COMPATIBILITY') != 'raspberrypi4-64':
        errors.append('release: wrong HARDWARE_COMPATIBILITY')
    if commit is not None and values.get('SOURCE_COMMIT') != commit:
        errors.append('release: SOURCE_COMMIT does not match expected commit')
    return errors


def read_debugfs(image, command):
    result = subprocess.run(['debugfs', '-R', command, str(image)], capture_output=True, text=True)
    # debugfs can return status zero even when its command failed.
    stderr = '\n'.join(line for line in result.stderr.splitlines() if not line.startswith('debugfs '))
    if result.returncode or stderr.strip():
        raise RuntimeError(f'debugfs {command}: {stderr.strip()}')
    return result.stdout


def inspect(image, commit, reader=read_debugfs):
    expected = {line.strip() for line in BASELINE.read_text().splitlines() if line.strip() and not line.startswith('#')}
    if {item.split('/', 1)[0] for item in expected} != CHECKED - {'network-online.target.wants'}:
        raise RuntimeError('expected enabled units baseline has incorrect directories')
    dirs = parse_listing(reader(image, 'ls -p /etc/systemd/system'))
    actual = set()
    for name, mode in dirs:
        if name.endswith('.wants') and mode.startswith('04'):
            for unit, _ in parse_listing(reader(image, f'ls -p /etc/systemd/system/{name}')):
                actual.add(f'{name}/{unit}')
            if name not in CHECKED:
                print(f'Uncompared {name}: ' + ', '.join(sorted(x.split('/', 1)[1] for x in actual if x.startswith(name + '/'))))
    errors = compare_units(actual, expected)
    for path, check in (('/etc/fstab', check_fstab), ('/etc/nextoomio-release', lambda x: check_release(x, commit))):
        try:
            errors.extend(check(reader(image, f'cat {path}')))
        except RuntimeError as exc:
            errors.append(str(exc))
    for path in ('/usr/bin/nx-sysd', '/etc/nftables.conf'):
        try:
            reader(image, f'stat {path}')
        except RuntimeError as exc:
            errors.append(f'missing {path}: {exc}')
    return errors


def self_test():
    listing = '/12/040755/0/0/./\n/2/040755/0/0/../\n/33/120777/0/0/nftables.service/0/\n'
    assert parse_listing(listing) == [('nftables.service', '120777')]
    expected = {'sysinit.target.wants/nftables.service'}
    assert not compare_units(expected, expected)
    assert compare_units(set(), expected)
    assert compare_units(expected | {'multi-user.target.wants/systemd-networkd.service'}, expected)
    assert compare_units(expected | {'sockets.target.wants/swupdate.socket'}, expected)
    assert compare_units(expected | {'other.target.wants/sshd.service'}, expected)
    assert not compare_units(expected | {'other.target.wants/example.service'}, expected)
    # D-071 issue #273: the actual listing, before the firewall and service fixes.
    recorded = """basic.target.wants: nextoomio-device-id.service nextoomio-trial-watchdog.service nextoomio-update-init.service
local-fs.target.wants: nextoomio-boot-mount.service nextoomio-data-check.service var-volatile-cache.service var-volatile-lib.service var-volatile-spool.service var-volatile-srv.service
sysinit.target.wants: run-postinsts.service systemd-network-generator.service systemd-resolved.service systemd-timesyncd.service
multi-user.target.wants: NetworkManager.service avahi-daemon.service busybox-klogd.service busybox-syslog.service dnsmasq.service nextoomio-audio-profile.service nextoomio-data-init.service nextoomio-ssh-keys.service remote-fs.target rpcbind.service swupdate.service systemd-networkd.service
sockets.target.wants: avahi-daemon.socket mpd.socket pipewire-manager.socket pipewire.socket rpcbind.socket swupdate.socket systemd-journald-audit.socket systemd-networkd-resolve-hook.socket systemd-networkd-varlink.socket systemd-networkd.socket systemd-resolved-monitor.socket systemd-resolved-varlink.socket systemd-userdbd.socket"""
    actual = {f'{directory}/{unit}' for line in recorded.splitlines()
              for directory, units in [line.split(': ', 1)] for unit in units.split()}
    baseline = {line.strip() for line in BASELINE.read_text().splitlines() if line.strip() and not line.startswith('#')}
    errors = compare_units(actual, baseline)
    for item in ('missing enabled unit: sysinit.target.wants/nftables.service',
                 'unexpected enabled unit: multi-user.target.wants/systemd-networkd.service',
                 'unexpected enabled unit: multi-user.target.wants/dnsmasq.service',
                 'unexpected enabled unit: multi-user.target.wants/rpcbind.service',
                 'unexpected enabled unit: multi-user.target.wants/swupdate.service'):
        assert item in errors, item
    assert compare_units(baseline | {'network-online.target.wants/NetworkManager-wait-online.service'}, baseline)
    for missing in ('/etc/nftables.conf', '/usr/bin/nx-sysd'):
        def reader(image, command):
            if command == f'stat {missing}':
                raise RuntimeError('not found')
            if command.startswith('ls -p'):
                return ''
            return ''
        assert any(f'missing {missing}:' in error for error in inspect(None, None, reader))
    fstab = '/dev/root / auto ro,noatime 0 1\nLABEL=select /boot/select vfat ro 0 0\nLABEL=data /data ext4 defaults 0 0\n'
    assert not check_fstab(fstab)
    assert check_fstab(fstab.replace('ro,noatime', 'rw') + 'x /boot vfat ro 0 0\n')
    assert check_fstab(fstab.replace('/data', '/missing'))
    release = 'IMAGE_VERSION="1"\nBUILD_ID="2"\nSOURCE_COMMIT="abc"\nHARDWARE_COMPATIBILITY="raspberrypi4-64"\n'
    assert not check_release(release, 'abc')
    assert check_release(release, 'wrong')
    assert check_release(release.replace('BUILD_ID="2"', 'BUILD_ID=""'))
    print('OK: rootfs parser and comparison self-tests')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', nargs='?')
    parser.add_argument('--commit')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    if args.test:
        self_test()
        return 0
    if not args.image:
        parser.error('image is required unless --test is used')
    try:
        with tempfile.TemporaryDirectory(prefix='.rootfs-check-', dir=ROOT) as directory:
            image = pathlib.Path(directory) / 'rootfs.ext4'
            with gzip.open(args.image, 'rb') as source, image.open('wb') as dest:
                shutil.copyfileobj(source, dest)
            errors = inspect(image, args.commit)
    except (OSError, EOFError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    for error in errors:
        print(f'ERROR: {error}', file=sys.stderr)
    if not errors:
        print('OK: rootfs checks passed (six baseline directories compared)')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
