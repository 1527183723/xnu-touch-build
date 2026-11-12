#!/usr/bin/env python3
"""Package bounded SDF diagnostics over the exact, tested XR r2 kernel/root."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from package_touch_experimental import payload_check

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-native-glass-xr-r2-20261111'
OUT = ROOT / 'out/dada-native-glass-sdf-diagnostic-r3-20261111'
WORK = Path('/home/LZY/warsaw-dada-sdf-diagnostic-r3-20261111')
FW = Path('/home/LZY/warsaw-ios26-20261110')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def main():
    OUT.mkdir(exist_ok=False)
    WORK.mkdir(exist_ok=False)
    root = WORK / 'root'
    original = Path('/home/LZY/warsaw-dada-native-xr-r2-20261111/root')
    shutil.copytree(original, root, symlinks=True)
    observer = BASE / 'sdf-observer-build-1/libredwood-sb26.dylib'
    probe = BASE / 'mirror-lifecycle-build-2/native-metal-xr-probe'
    shutil.copy2(observer, root / 'lib/libicon-sdf-observer.dylib')
    shutil.copy2(probe, root / 'bin/mirror-lifecycle')
    (root / 'bin/mirror-lifecycle').chmod(0o755)
    config = root / 'etc/inject.conf'
    text = config.read_text()
    assert text.count('/redwood/lib/libredwood-sb26.dylib') == 1
    config.write_text(text.replace('/redwood/lib/libredwood-sb26.dylib',
                                  '/redwood/lib/libicon-sdf-observer.dylib'))
    changed = []
    for p in root.rglob('*'):
        old = original / p.relative_to(root)
        if p.is_symlink():
            assert old.is_symlink() and os.readlink(old) == os.readlink(p)
        elif p.is_file() and (not old.is_file() or sha(p) != sha(old)):
            changed.append(str(p.relative_to(root)))
    assert sorted(changed) == ['bin/mirror-lifecycle', 'etc/inject.conf',
                                'lib/libicon-sdf-observer.dylib'], changed

    def run(name, command, **kw):
        command = [str(x) for x in command]
        save(OUT / (name + '.command.json'), command)
        with (OUT / (name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, **kw)
        print('PASS', name, flush=True)

    disk = OUT / 'root26.apfs'
    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    run('apfs-build', ['python3', FW / 'build/tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
        '-o', disk, 'redwood=' + str(root)], cwd=FW / 'build/userland', env=env)
    run('apfs-check', ['bash', FW / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(root)], cwd=FW / 'build/tools/apfs', env=env)
    cfg = json.loads((BASE / 'boot-config.json').read_text())
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = BASE / 'kernel-native-xr.bin'
    payload = OUT / 'ios-payload.bin'
    run('payload-build', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
        '--identity', cfg['identity'], '--usb', '--memsize', cfg['memsize_upper_bound_hex'],
        '--cmdline', cfg['cmdline'], '--require-cmd', 'dadatouch=1',
        '--require-cmd', 'surf_native_xr=1', '--forbid-cmd', 'loader_return_after'])
    save(OUT / 'payload-entries.json', payload_check(payload,
        {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk}))
    image = OUT / 'warsaw-dada-native-glass-sdf-diagnostic-r3.img'
    shutil.copy2(BASE / cfg['uefi'], image)
    cfg.update(uefi=image.name, hardware_tested=False, diagnostic_only=True,
               system_reference=str(ROOT / 'out/ios26-r1/system26.apfs'))
    cfg['files'] = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in (image, payload, disk)}
    save(OUT / 'boot-config.json', cfg)
    save(OUT / 'STATUS.json', {
        'built': True, 'hardware_tested': False, 'diagnostic_only': True,
        'icons_fixed': False, 'root_cause_confirmed': False,
        'changed_small_root_files': sorted(changed),
        'kernel_sha256': sha(kernel), 'uefi_binary_changed': False,
        'native_glass_preserved': True, 'touch_preserved': True,
        'gpu_driver_binaries_changed': False, 'system_changed': False,
        'no_partition_flash': True,
        'rollback_payload': str(BASE / 'ios-payload.bin'),
        'probe_runs_automatically': False,
        'observer': json.loads((BASE / 'sdf-observer-build-1/build.json').read_text()),
        'files': cfg['files']})
    print('PASS SDF diagnostic package; actual phone tests pending.', flush=True)


if __name__ == '__main__':
    main()
