#!/usr/bin/env python3
"""Build a private small-root icon orientation fix over the tested NativeMetal r4.

The GPU binaries, kernel, System and original fallback package stay intact.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
OLD_WORK = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('stage', choices=['compile', 'package'])
    ap.add_argument('--work', type=Path, default=Path('/home/LZY/warsaw-dada-native-metal-ios26-r5-20261110'))
    ap.add_argument('--output', type=Path, default=ROOT / 'out/dada-native-metal-ios26-r5-orientation')
    args = ap.parse_args()
    work, out = args.work, args.output
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    base = ROOT / 'out/dada-native-metal-ios26-r4'
    fw = Path('/home/LZY/warsaw-ios26-20261110')

    def run(name, command, **kw):
        command = [str(x) for x in command]
        write_json(out / (name + '.command.json'), command)
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, **kw)
        print('PASS', name, flush=True)

    if args.stage == 'compile':
        source = work / 'source'
        source.mkdir(exist_ok=True)
        names = ['inject_sb26.c', 'inject_sb26_layers.c', 'inject_sb26_rate.c', 'inject_posterboard.c']
        for include in (ROOT / 'userland/src').glob('*.inc'):
            shutil.copy2(include, source / include.name)
        for name in names:
            shutil.copy2(ROOT / 'userland/src' / name, source / name)
        old = Path('/home/LZY/warsaw-dada-graphics-r3-20261110/source/inject_sb26.c').read_text()
        new = (source / 'inject_sb26.c').read_text()
        assert 'CGContextTranslateCTM(ctx, 0, (CGFloat)h);' in old
        assert 'CGContextTranslateCTM(ctx, 0, (CGFloat)h);' not in new
        (out / 'icon-orientation.patch').write_text(''.join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True), fromfile='a/userland/src/inject_sb26.c',
            tofile='b/userland/src/inject_sb26.c')))
        prior = json.loads((ROOT / 'out/dada-graphics-r3/build-manifest.json').read_text())
        command = prior['compile']
        command = [str(source / Path(x).name) if Path(x).name in names else x for x in command]
        command[command.index('-o') + 1] = str(out / 'libredwood-sb26.dylib')
        run('sb26-compile', command, cwd=fw / 'build/userland')
        clang = OLD_WORK / 'clang'
        sdk = Path('/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk')
        flags = ['-target', 'arm64e-apple-ios26.0', '-isysroot', sdk, '-O2', '-Wl,-adhoc_codesign']
        run('session-helper', [clang, *flags, ROOT / 'tools/dada/session_icon_library.c', '-o', out / 'session-icon-library'])
        run('orientation-probe', [clang, *flags, '-fobjc-arc', ROOT / 'tools/dada/native_metal_orientation.m',
            '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOSurface', '-o', out / 'native-metal-orientation'])
        shutil.copy2(OLD_WORK / 'generated/runtime.air', out / 'runtime.air')
        write_json(out / 'macho-report.json', {n: inspect(out / n) for n in
                   ['libredwood-sb26.dylib', 'session-icon-library', 'native-metal-orientation']})
        write_json(out / 'source-manifest.json', {'base': str(base), 'sources':
            {p.name: sha(p) for p in sorted(source.iterdir()) if p.is_file()},
            'removed': ['CGContextTranslateCTM', 'CGContextScaleCTM'],
            'gpu_unchanged': True, 'system_unchanged': True, 'kernel_unchanged': True})
        return

    if (out / 'build-manifest.json').exists() or (work / 'root').exists():
        raise FileExistsError('Refusing to overwrite an existing private package')
    root = work / 'root'
    shutil.copytree(OLD_WORK / 'root', root, symlinks=True)
    inspect(out / 'libredwood-sb26.dylib')
    shutil.copy2(out / 'libredwood-sb26.dylib', root / 'lib/libredwood-sb26.dylib')
    shutil.copy2(out / 'native-metal-orientation', root / 'bin/native-metal-orientation')
    shutil.copy2(out / 'runtime.air', root / 'etc/native-metal-orientation.air')
    shutil.copy2(out / 'source-manifest.json', root / 'etc/icon-orientation-build.json')
    for rel in ['lib/RedwoodMetal.bundle/RedwoodMetal', 'lib/RedwoodMetal.bundle/libvulkan_freedreno.dylib']:
        assert sha(root / rel) == sha(OLD_WORK / 'root' / rel)
    changed=[]
    for p in sorted(root.rglob('*')):
        old = OLD_WORK / 'root' / p.relative_to(root)
        if p.is_symlink():
            assert old.is_symlink() and os.readlink(p) == os.readlink(old)
        elif p.is_file() and (not old.exists() or sha(p)!=sha(old)):
            changed.append(str(p.relative_to(root)))
    assert changed == ['bin/native-metal-orientation', 'etc/icon-orientation-build.json',
                       'etc/native-metal-orientation.air', 'lib/libredwood-sb26.dylib'], changed
    env = dict(os.environ, PYTHONPATH=str(fw / 'host-python'))
    build = fw / 'build'
    disk = out / 'root26.apfs'
    run('apfs-build', ['python3', build / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
        '-o', disk, 'redwood=' + str(root)], cwd=build / 'userland', env=env)
    run('apfs-check', ['bash', build / 'tools/apfs/check-image.sh', disk, 'redwood=' + str(root)],
        cwd=build / 'tools/apfs', env=env)
    config = json.loads((base / 'boot-config.json').read_text())
    cmdline = ' '.join(x for x in config['cmdline'].split() if not x.startswith('epoch=')) + f' epoch={int(time.time())}'
    payload = out / 'ios-payload.bin'
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    run('payload', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload, 'loader.efi=' + str(loader),
        'kernel=' + str(ROOT / 'out/ios26-r1/kernel-ath12k.bin'), 'ramdisk=' + str(disk),
        '--identity', 'v57', '--usb', '--memsize', '240000000', '--cmdline', cmdline,
        '--forbid-cmd', 'loader_return_after'])
    image = out / 'warsaw-dada-native-metal-r5.img'
    shutil.copy2(base / config['uefi'], image)
    shutil.copytree(base / 'licenses', out / 'licenses', dirs_exist_ok=True)
    report = {'variant': 'NativeMetal r5 icon bitmap orientation', 'base': str(base),
        'changed_small_root_files': changed, 'gpu_unchanged_from_r4': True, 'uefi_unchanged': True,
        'kernel_unchanged': True, 'system_reference': config['system_reference'],
        'apfs_checks_passed': True, 'hardware_tested_package': False,
        'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in
                  [disk, payload, image, out / 'libredwood-sb26.dylib', out / 'native-metal-orientation']}}
    write_json(out / 'build-manifest.json', report)
    config.update(cmdline=cmdline, payload=payload.name, uefi=image.name, files=report['files'], hardware_tested=False)
    write_json(out / 'boot-config.json', config)
    print('Small root/payload complete; System and GPU binaries reused.', flush=True)


if __name__ == '__main__':
    main()
