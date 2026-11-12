#!/usr/bin/env python3
"""Build only a private small-root graphics variant; keep the completed System intact."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--firmware-work', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    work, out = args.work, args.output
    work.mkdir(parents=True, exist_ok=args.resume)
    out.mkdir(parents=True, exist_ok=args.resume)
    if (out / 'build-manifest.json').exists():
        raise FileExistsError('Completed variant already exists')
    build = args.firmware_work / 'build'
    userland = build / 'userland'
    shutil.copytree(userland / 'root', work / 'root', symlinks=True, dirs_exist_ok=args.resume)
    sources = work / 'source'
    shutil.copytree(ROOT / 'userland/src', sources, dirs_exist_ok=args.resume)
    record = json.loads((args.firmware_work / 'userland-compiled.json').read_text())
    command = next(c['command'] for c in record['commands'] if c['name'] == 'libredwood-sb26.dylib')
    library = work / 'root/lib/libredwood-sb26.dylib'
    command = [str(sources / Path(s).name) if s.startswith('src/') else s for s in command]
    command[command.index('-o') + 1] = str(library)
    # This library needs no entitlements. Have ld64 emit its ad-hoc signature,
    # then validate every code-directory page hash independently below.
    command.append('-Wl,-adhoc_codesign')
    with (out / 'compile.log').open('wb') as log:
        subprocess.run(command, cwd=userland, stdout=log, stderr=log, check=True)
    inspect(library, require_arm64e=True)
    conf = work / 'root/etc/glass.conf'
    conf.write_text('# Dada: preserve source artwork; keep rounded clipping.\n'
                    'on = 1\nicon_color = 1.0\nicon_white = 1.0\nicon_sheen = 0.0\n'
                    'icon_layer = 1.0\ndock = 1.0\nsearch = 1.0\nwidget = 1.0\n')
    # Verify the private root differs from the completed root in exactly two files.
    changed = []
    for source in sorted((userland / 'root').rglob('*')):
        relative = source.relative_to(userland / 'root')
        dest = work / 'root' / relative
        if source.is_symlink():
            assert dest.is_symlink() and os.readlink(dest) == os.readlink(source)
        elif source.is_file() and source.read_bytes() != dest.read_bytes():
            changed.append(relative.as_posix())
    assert sorted(changed) == ['etc/glass.conf', 'lib/libredwood-sb26.dylib'], changed
    root = out / 'root26.apfs'
    env = dict(os.environ, PYTHONPATH=str(args.firmware_work / 'host-python'))
    with (out / 'apfs-build.log').open('wb') as log:
        subprocess.run(['python3', str(build / 'tools/apfs/mkapfs.py'), '-z', '--plain', 'fw',
                        '-o', str(root), 'redwood=' + str(work / 'root')],
                       cwd=userland, env=env, stdout=log, stderr=log, check=True)
        subprocess.run(['bash', str(build / 'tools/apfs/check-image.sh'), str(root),
                        'redwood=' + str(work / 'root')], cwd=build / 'tools/apfs',
                       env=env, stdout=log, stderr=log, check=True)
    config = json.loads((args.base / 'boot-config.json').read_text())
    command_line = ' '.join(x for x in config['cmdline'].split() if not x.startswith('epoch='))
    command_line += f' epoch={int(time.time())}'
    loader = args.base / 'loader-dada-ram-usb.efi'
    kernel = ROOT / 'out/ios26-r1/kernel-ath12k.bin'
    payload = out / 'ios-payload.bin'
    subprocess.run(['python3', str(ROOT / 'xnuloader/mkpayload.py'), '-o', str(payload),
                    'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(root),
                    '--identity', 'v57', '--usb', '--memsize', '240000000', '--cmdline', command_line,
                    '--forbid-cmd', 'loader_return_after'], check=True)
    shutil.copy2(library, out / library.name)
    shutil.copy2(conf, out / conf.name)
    shutil.copy2(args.base / 'warsaw-dada-ram-usb-r2.img', out / 'warsaw-dada-graphics-r3.img')
    report = {'variant': 'dada graphics r3', 'changed_small_root_files': changed,
              'system_reference': str(ROOT / 'out/ios26-r1/system26.apfs'),
              'gpu_driver_changed': False, 'widget_and_clock_rendering_fixed': False,
              'touch_kernel_port_included': False, 'hardware_tested': False,
              'cmdline': command_line, 'compile': command, 'signature': 'ld64 -adhoc_codesign',
              'previous_root_sha256': sha(ROOT / 'out/ios26-r1/root26.apfs'),
              'apfs_checks_passed': True,
              'files': {f.name: {'bytes': f.stat().st_size, 'sha256': sha(f)}
                        for f in (root, payload, out / 'warsaw-dada-graphics-r3.img', out / library.name)}}
    (out / 'build-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / 'cmdline.txt').write_text(command_line + '\n')
    print('Graphics r3 small root built and checked. Not yet hardware tested.', flush=True)


if __name__ == '__main__':
    main()
