#!/usr/bin/env python3
"""Package a newly linked TouchCom kernel over the r5 graphics baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import zipfile

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-native-metal-ios26-r5-orientation'
BASE_ROOT = Path('/home/LZY/warsaw-dada-native-metal-ios26-r5-20261110/root')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def kernel_layout(path):
    data = path.read_bytes()
    header = struct.unpack_from('<8I', data)
    assert header[:4] == (0xfeedfacf, 0x100000c, 0, 2), header
    count, size = header[4:6]
    cursor, entry, segments = 32, 0, []
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', data, cursor)
        assert length >= 8 and cursor + length <= 32 + size <= len(data)
        if cmd == 0x19:
            name, vm, vmsize, off, filesize, maxprot, prot = struct.unpack_from('<16s4Q2I', data, cursor + 8)
            assert filesize <= vmsize and off + filesize <= len(data)
            if vmsize:
                assert vm >= 0xfffffff007004000
                assert all(vm >= s['vm'] + s['vmsize'] or s['vm'] >= vm + vmsize for s in segments)
                segments.append({'name': name.rstrip(b'\0').decode(), 'vm': vm, 'vmsize': vmsize,
                                 'fileoff': off, 'filesize': filesize, 'prot': prot})
        elif cmd == 5:
            entry = struct.unpack_from('<Q', data, cursor + 16 + 32 * 8)[0]
        cursor += length
    assert cursor == 32 + size and segments
    assert any(s['prot'] & 4 and s['vm'] <= entry < s['vm'] + s['vmsize'] for s in segments)
    return {'entry': hex(entry), 'segments': segments, 'sha256': sha(path), 'bytes': len(data)}


def payload_check(path, expected):
    data = path.read_bytes()
    assert struct.unpack_from('<II', data) == (0x50554e58, len(data) - 8)
    cursor, entries = 8, {}
    while cursor < len(data):
        name, size, reserved = struct.unpack_from('<16sII', data, cursor)
        cursor += 24
        if name == b'\0' * 16:
            assert size == reserved == 0 and cursor == len(data)
            break
        name = name.split(b'\0')[0].decode()
        assert reserved == 0 and name not in entries and cursor + size <= len(data)
        entries[name] = data[cursor:cursor + size]
        cursor += (size + 15) & ~15
    else:
        raise AssertionError('Missing XNUP end entry')
    for name, source in expected.items():
        assert entries[name] == source.read_bytes(), name
    tokens = entries['cmdline'].rstrip(b'\0').decode().split()
    assert 'dadatouch=1' in tokens
    assert not any(t.split('=')[0] in ('touchscreen', 'autoswipe', 'dadatouch_step', 'loader_return_after') for t in tokens)
    return {name: {'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()} for name, blob in entries.items()}


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--work', type=Path, default=Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110'))
    ap.add_argument('--output', type=Path, default=ROOT / 'out/dada-touch-experimental-r3-20261110')
    args = ap.parse_args()
    work, out = args.work, args.output
    tree = work / 'xnu12377'
    variant = tree / 'BUILD/obj/DEVELOPMENT_ARM64_BCM2837'
    kernel = variant / 'mach.development.bcm2837'
    unstripped = variant / 'mach.development.bcm2837.unstripped'
    report = json.loads((work / 'experimental-build.json').read_text())
    assert report['compile_result']['kernel_file_exists']
    symbols = subprocess.check_output(['llvm-nm-21', str(unstripped)], text=True)
    for required in ('_hakios_dada_touch_init', '_hakios_dada_touch_stop'):
        assert any(line.endswith(' T ' + required) or line.endswith(' S ' + required)
                   for line in symbols.splitlines()), required
    undefined = subprocess.check_output(['llvm-nm-21', '--undefined-only', '--just-symbol-name', str(unstripped)], text=True)
    assert not undefined.strip(), undefined
    layout = kernel_layout(kernel)
    meta = report['touch_source']
    for item in meta['imported_files']:
        assert sha(tree / item['path']) == item['port_sha256']
    out.mkdir(parents=True, exist_ok=False)

    def run(name, command, **kwargs):
        command = [str(x) for x in command]
        write_json(out / (name + '.command.json'), command)
        with (out / (name + '.log')).open('wb') as log:
            subprocess.run(command, stdout=log, stderr=log, check=True, **kwargs)
        print('PASS', name, flush=True)

    run('userland-build', ['python3', ROOT / 'tools/dada/build_touch_package_userland.py', '--output', out / 'userland'])
    userland = out / 'userland/input26-service'
    inspect(userland)
    root = work / 'root'
    shutil.copytree(BASE_ROOT, root, symlinks=True)
    shutil.copy2(userland, root / 'bin/input26-service')
    # A copied executable inherits the producer's mode, not the old root's.
    (root / 'bin/input26-service').chmod(0o755)
    write_json(root / 'etc/dada-touch-build.json', {
        'primary_source': 'user repair package 20261110 + superboyyy TouchCom',
        'driver_sha256': meta['imported_files'][0]['port_sha256'],
        'kernel_sha256': sha(kernel), 'experimental_driverkit_fallback': True,
        'hardware_tested': False})
    changed = []
    for path in sorted(root.rglob('*')):
        original = BASE_ROOT / path.relative_to(root)
        if path.is_symlink():
            assert original.is_symlink() and os.readlink(path) == os.readlink(original)
        elif path.is_file() and (not original.exists() or sha(path) != sha(original)):
            changed.append(str(path.relative_to(root)))
    assert changed == ['bin/input26-service', 'etc/dada-touch-build.json'], changed
    fw = Path('/home/LZY/warsaw-ios26-20261110')
    build = fw / 'build'
    env = dict(os.environ, PYTHONPATH=str(fw / 'host-python'))
    disk = out / 'root26.apfs'
    run('apfs-build', ['python3', build / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
                      '-o', disk, 'redwood=' + str(root)], cwd=build / 'userland', env=env)
    run('apfs-check', ['bash', build / 'tools/apfs/check-image.sh', disk, 'redwood=' + str(root)],
        cwd=build / 'tools/apfs', env=env)
    shutil.copy2(kernel, out / 'kernel-touch.bin')
    config = json.loads((BASE / 'boot-config.json').read_text())
    removed = {'epoch', 'touchscreen', 'autoswipe', 'dadatouch_step', 'loader_return_after', 'dadatouch'}
    cmdline = ' '.join(t for t in config['cmdline'].split() if t.split('=')[0] not in removed)
    cmdline += f' dadatouch=1 epoch={int(time.time())}'
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    payload = out / 'ios-payload.bin'
    run('payload-build', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(out / 'kernel-touch.bin'),
        'ramdisk=' + str(disk), '--identity', 'v57', '--usb', '--memsize', '240000000',
        '--cmdline', cmdline, '--require-cmd', 'dadatouch=1', '--forbid-cmd', 'loader_return_after',
        '--forbid-cmd', 'autoswipe', '--forbid-cmd', 'touchscreen'])
    payload_report = payload_check(payload, {'loader.efi': loader, 'kernel': out / 'kernel-touch.bin', 'ramdisk': disk})
    image = out / 'warsaw-dada-usb-loader.img'
    shutil.copy2(BASE / config['uefi'], image)
    shutil.copytree(BASE / 'licenses', out / 'licenses')
    for name in ('linux-iig-compat.patch', 'experimental-build.json', 'touch-port.json',
                 'full-kernel-build.log', 'linux-touch-compile.json', 'support-build.json'):
        shutil.copy2(work / name, out / name)
    for name in ('libdispatch', 'libpthread'):
        for filename in ('LICENSE', 'LICENSE.txt', 'APPLE_LICENSE'):
            source_license = work / 'support-src' / name / filename
            if source_license.is_file():
                shutil.copy2(source_license, out / 'licenses' / (name + '-' + filename))
    (out / 'kernel-symbols.txt').write_text(symbols)
    write_json(out / 'kernel-layout.json', layout)
    write_json(out / 'payload-entries.json', payload_report)
    source = out / 'source-port'
    shutil.copytree(ROOT / 'xnu26/dada-touch-superboyyy', source / 'xnu26/dada-touch-superboyyy')
    for path in ['tools/dada/build_touch_experimental_linux.py', 'tools/dada/check_touch_kernel_linux.py',
                 'tools/dada/package_touch_experimental.py', 'tools/dada/build_touch_package_userland.py',
                 'tools/dada/build_touch_support_linux.py',
                 'tools/dada/macho_verify.py', 'userland/src/input26_service.c',
                 'userland/src/input26_service.entitlements', 'userland/src/redwood_input.h', 'LICENSE']:
        dest = source / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, dest)
    shutil.copy2(ROOT / 'docs/DADA-TOUCH-EXPERIMENTAL-R3-20261110.md', out / 'README.md')
    shutil.copy2(work / 'linux-iig-compat.patch', source / 'linux-iig-compat.patch')
    with zipfile.ZipFile(out / 'dada-touch-source-r3.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                archive.write(path, str(path.relative_to(source)))
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in
             [out / 'kernel-touch.bin', disk, payload, image, out / 'dada-touch-source-r3.zip']}
    config.update(cmdline=cmdline, payload=payload.name, uefi=image.name, files=files,
                  touch_kernel_port_included=True, hardware_tested=False,
                  experimental_driverkit_fallback=True, uefi_binary_changed=False)
    write_json(out / 'boot-config.json', config)
    write_json(out / 'STATUS.json', {
        'kernel_linked': True, 'kernel_undefined_symbols': [], 'payload_packaged': True,
        'apfs_content_verified': True, 'primary_source': 'user package + superboyyy',
        'gpu_small_root_files_changed': False, 'system_image_reused': config['system_reference'],
        'root_files_changed': changed, 'uefi_loader_reused': True,
        'touch_driver_is_in_kernel_payload': True, 'hardware_tested': False,
        'limitations': report['limitations'], 'files': files})
    print('Experimental touch kernel, root and payload ready:', out, flush=True)


if __name__ == '__main__':
    main()
