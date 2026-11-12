#!/usr/bin/env python3
"""Preserve native RenderBox XR icon atlases over the working native Glass/touch build."""
import difflib
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from package_touch_experimental import kernel_layout, payload_check

ROOT = Path(__file__).resolve().parents[2]
BASE_WORK = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110')
BASE = ROOT / 'out/dada-native-glass-r1-20261111'
WORK = Path('/home/LZY/warsaw-dada-native-xr-r2-20261111')
OUT = ROOT / 'out/dada-native-glass-xr-r2-20261111'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--resume-build', action='store_true')
    resume = ap.parse_args().resume_build
    if resume:
        assert WORK.is_dir() and OUT.is_dir() and not (OUT / 'ios-payload.bin').exists()
    else:
        WORK.mkdir(exist_ok=False)
        OUT.mkdir(exist_ok=False)

    def run(name, command, **kwargs):
        command = [str(s) for s in command]
        save(OUT / (name + '.command.json'), command)
        with (OUT / (name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, **kwargs)
        print('PASS', name, flush=True)

    if not resume:
        shutil.copytree(BASE_WORK / 'xnu12377', WORK / 'xnu12377', symlinks=True)
        shutil.copytree(BASE_WORK / 'deps', WORK / 'deps', symlinks=True)
        for name in ['touch-port.json', 'experimental-build.json', 'support-build.json']:
            shutil.copy2(BASE_WORK / name, WORK / name)
    kernel_relative = Path('iokit/Kernel/MiniIOSurface.cpp')
    old = (BASE_WORK / 'xnu12377' / kernel_relative).read_text()
    new = (ROOT / 'xnu/files' / kernel_relative).read_text()
    assert "if (format == 'b3a8' || format == '&BGA')" in old
    assert 'gSurfaceNativeXR' not in old and 'surf_native_xr' in new
    patch = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile='a/' + str(kernel_relative), tofile='b/' + str(kernel_relative)))
    (OUT / 'native-xr.patch').write_text(patch)
    (WORK / 'xnu12377' / kernel_relative).write_text(new)
    # Cached generated Makefiles and dependency records contain absolute paths.
    # Relocate them so a cached rule cannot compile the original source tree.
    relocated = []
    for path in (WORK / 'xnu12377/BUILD').rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        if 'Makefile' not in path.name and path.suffix not in ('.d', '.mk', '.json'):
            continue
        before = path.read_bytes()
        after = before.replace(str(BASE_WORK).encode(), str(WORK).encode())
        if after != before:
            path.write_bytes(after)
            relocated.append(str(path.relative_to(WORK)))
    save(OUT / 'relocated-build-records.json', relocated)
    for path in (WORK / 'xnu12377/BUILD/obj').rglob('MiniIOSurface.cpo'):
        assert path.resolve().is_relative_to(WORK.resolve())
        path.unlink()
    print('Prepared isolated XR kernel tree; original r3 build preserved.', flush=True)
    run('kernel-build', ['python3', ROOT / 'tools/dada/check_touch_kernel_linux.py',
        '--work', WORK, '--cache', '/home/LZY/hakios-arm64e-dada-kernel-20261031',
        '--attempt-kernel', '--verbose'])
    result = json.loads((WORK / 'linux-touch-compile.json').read_text())
    assert result['full_kernel_build_returncode'] == 0 and result['kernel_linked'], result
    variant = WORK / 'xnu12377/BUILD/obj/DEVELOPMENT_ARM64_BCM2837'
    kernel = variant / 'mach.development.bcm2837'
    assert sha(kernel) != sha(ROOT / 'out/dada-touch-experimental-r3-20261110/kernel-touch.bin')
    assert b'native XR atlas' in kernel.read_bytes()
    undefined = subprocess.check_output(['llvm-nm-21', '--undefined-only', '--just-symbol-name',
        str(variant / 'mach.development.bcm2837.unstripped')], text=True)
    assert not undefined.strip(), undefined
    touch = json.loads((WORK / 'touch-port.json').read_text())
    for entry in touch['imported_files']:
        assert sha(WORK / 'xnu12377' / entry['path']) == entry['port_sha256']
    shutil.copy2(kernel, OUT / 'kernel-native-xr.bin')
    save(OUT / 'kernel-layout.json', kernel_layout(kernel))
    shutil.copy2(WORK / 'full-kernel-build.log', OUT / 'full-kernel-build.log')
    shutil.copy2(WORK / 'linux-touch-compile.json', OUT / 'linux-touch-compile.json')

    root = WORK / 'root'
    original_root = Path('/home/LZY/warsaw-native-glass-r1-20261111/root')
    shutil.copytree(original_root, root, symlinks=True)
    meta = json.loads((root / 'etc/dada-touch-build.json').read_text())
    meta.update(kernel_sha256=sha(kernel), native_xr_atlas=True, hardware_tested=False)
    save(root / 'etc/dada-touch-build.json', meta)
    gpu_files = ['lib/RedwoodMetal.bundle/RedwoodMetal',
                 'lib/RedwoodMetal.bundle/libvulkan_freedreno.dylib',
                 'lib/libredwood-sb26.dylib', 'bin/input26-service']
    for relative in gpu_files:
        assert sha(root / relative) == sha(original_root / relative), relative
    fw = Path('/home/LZY/warsaw-ios26-20261110')
    env = dict(os.environ, PYTHONPATH=str(fw / 'host-python'))
    disk = OUT / 'root26.apfs'
    run('apfs-build', ['python3', fw / 'build/tools/apfs/mkapfs.py', '-z', '--plain',
        'fw', '-o', disk, 'redwood=' + str(root)], cwd=fw / 'build/userland', env=env)
    run('apfs-check', ['bash', fw / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(root)], cwd=fw / 'build/tools/apfs', env=env)
    config = json.loads((BASE / 'boot-config.json').read_text())
    cmdline = ' '.join(s for s in config['cmdline'].split()
        if s.split('=')[0] not in ('epoch', 'surf_native_xr'))
    cmdline += f' surf_native_xr=1 epoch={int(time.time())}'
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    payload = OUT / 'ios-payload.bin'
    run('payload-build', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(OUT / 'kernel-native-xr.bin'),
        'ramdisk=' + str(disk), '--identity', 'v57', '--usb', '--memsize', '240000000',
        '--cmdline', cmdline, '--require-cmd', 'dadatouch=1',
        '--require-cmd', 'surf_native_xr=1', '--forbid-cmd', 'loader_return_after'])
    save(OUT / 'payload-entries.json', payload_check(payload,
        {'loader.efi': loader, 'kernel': OUT / 'kernel-native-xr.bin', 'ramdisk': disk}))
    image = OUT / 'warsaw-dada-native-glass-xr-r2.img'
    shutil.copy2(BASE / config['uefi'], image)
    shutil.copytree(BASE / 'licenses', OUT / 'licenses')
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in [image, payload, disk, OUT / 'kernel-native-xr.bin']}
    config.update(cmdline=cmdline, uefi=image.name, files=files, hardware_tested=False,
        native_xr_atlas=True)
    save(OUT / 'boot-config.json', config)
    save(OUT / 'STATUS.json', {'built': True, 'hardware_tested': False,
        'native_glass_profile_preserved': True, 'native_xr_atlas_preserved': True,
        'touch_driver_source_preserved': True, 'gpu_userland_preserved': True,
        'system_reference': str(ROOT / 'out/ios26-r1/system26.apfs'),
        'icons_correct_on_phone': None, 'no_partition_flash': True,
        'rollback_payload': str(BASE / 'ios-payload.bin'), 'files': files})
    save(OUT / 'build-manifest.json', {'private_work': str(WORK),
        'base_native_glass': str(BASE), 'changed_kernel_source': str(kernel_relative),
        'source_sha256': sha(WORK / 'xnu12377/iokit/Kernel/MiniIOSurface.cpp'),
        'touch_source': touch, 'gpu_files_unchanged': {s: sha(root / s) for s in gpu_files},
        'kernel_build_returncode': 0, 'kernel_undefined_symbols': [],
        'format_evidence': str(BASE / 'atlas-pixels-4/format-comparison.png')})
    shutil.copy2(ROOT / 'docs/DADA-NATIVE-XR-ATLAS-20261111.md', OUT / 'README.md')
    print('PASS native XR atlas payload packaged; phone validation pending.', flush=True)


if __name__ == '__main__':
    main()
