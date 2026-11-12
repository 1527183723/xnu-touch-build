#!/usr/bin/env python3
"""Package the tested frontend fix over XR r2, with the original SB injection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from package_touch_experimental import payload_check

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-native-glass-xr-r2-20261111'
FW = Path('/home/LZY/warsaw-ios26-20261110')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--probe', type=Path, required=True)
    ap.add_argument('--tests', type=Path, required=True)
    ap.add_argument('--xr-tests', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    args.work.mkdir(parents=True, exist_ok=False)
    tests = json.loads(args.tests.read_text())
    xr = json.loads(args.xr_tests.read_text())
    assert tests['fixed']['result'] == 'PASS' and tests['fixed']['checks'] == 18
    assert xr['fixed']['result'] == 'PASS' and xr['fixed']['checks'] == 28
    original = Path('/home/LZY/warsaw-dada-native-xr-r2-20261111/root')
    root = args.work / 'root'
    shutil.copytree(original, root, symlinks=True)
    shutil.copy2(args.build / 'RedwoodMetal', root / 'lib/RedwoodMetal.bundle/RedwoodMetal')
    shutil.copy2(args.probe, root / 'bin/mirror-lifecycle')
    (root / 'bin/mirror-lifecycle').chmod(0o755)
    assert '/lib/libredwood-sb26.dylib' in (root / 'etc/inject.conf').read_text()
    assert 'observer' not in (root / 'etc/inject.conf').read_text()
    changed = []
    for p in root.rglob('*'):
        old = original / p.relative_to(root)
        if p.is_symlink():
            assert old.is_symlink() and os.readlink(old) == os.readlink(p)
        elif p.is_file() and (not old.is_file() or sha(p) != sha(old)):
            changed.append(str(p.relative_to(root)))
    assert sorted(changed) == ['bin/mirror-lifecycle', 'lib/RedwoodMetal.bundle/RedwoodMetal'], changed

    def run(name, command, **kw):
        command = list(map(str, command))
        save(out / (name + '.command.json'), command)
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, **kw)
        print('PASS', name, flush=True)

    disk = out / 'root26.apfs'
    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    run('apfs-build', ['python3', FW / 'build/tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
        '-o', disk, 'redwood=' + str(root)], cwd=FW / 'build/userland', env=env)
    run('apfs-check', ['bash', FW / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(root)], cwd=FW / 'build/tools/apfs', env=env)
    cfg = json.loads((BASE / 'boot-config.json').read_text())
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = BASE / 'kernel-native-xr.bin'
    payload = out / 'ios-payload.bin'
    run('payload-build', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
        '--identity', cfg['identity'], '--usb', '--memsize', cfg['memsize_upper_bound_hex'],
        '--cmdline', cfg['cmdline'], '--require-cmd', 'dadatouch=1',
        '--require-cmd', 'surf_native_xr=1', '--forbid-cmd', 'loader_return_after'])
    save(out / 'payload-entries.json', payload_check(payload,
        {'loader.efi':loader, 'kernel':kernel, 'ramdisk':disk}))
    image = out / 'warsaw-dada-native-glass-mirror-r4.img'
    shutil.copy2(BASE / cfg['uefi'], image)
    cfg.update(uefi=image.name, hardware_tested=False,
               system_reference=str(ROOT / 'out/ios26-r1/system26.apfs'))
    cfg['files'] = {p.name:{'bytes':p.stat().st_size, 'sha256':sha(p)} for p in (image,payload,disk)}
    save(out / 'boot-config.json', cfg)
    save(out / 'STATUS.json', {
        'built':True, 'whole_package_hardware_tested':False,
        'isolated_driver_hardware_tested':True,
        'mirror_checks':tests['fixed'], 'xr_checks':xr['fixed'],
        'icons_fixed':False, 'icon_root_cause_confirmed':False,
        'changed_small_root_files':sorted(changed), 'uefi_binary_changed':False,
        'kernel_changed':False, 'system_changed':False, 'no_partition_flash':True,
        'native_glass_preserved':True, 'native_xr_preserved':True,
        'touch_driver_preserved':True, 'diagnostic_sb_injection':False,
        'rollback_payload':str(BASE / 'ios-payload.bin'),
        'driver_build':json.loads((args.build / 'build.json').read_text()),
        'files':cfg['files']})
    (out / 'README.md').write_text('''# dada Native Glass mirror r4 test package

This package fixes confirmed no-copy shared-buffer coherence failures. It does
not yet establish that the desktop icon stripes are fixed. The driver is tested
in an isolated device process before packaging: 18 lifecycle checks and 28 XR
render/blend/sample checks must pass. Whole-desktop testing is still required.

The UEFI `.img` is the exact XR r2 loader binary. The new GPU frontend resides
inside `root26.apfs` in `ios-payload.bin`; booting only the `.img` does not install
the fix. Load that payload and the existing `out/ios26-r1/system26.apfs` through
the normal RAM boot flow. Never flash these files to a partition.

Kernel, TouchCom, original compatibility injection, native Liquid Glass, two-plane
XR atlas, System, memory size and disabled automatic return are preserved.
The disruptive SDF observer is not included. Original XR r2 files are retained.
''')
    print('PASS mirror r4 package built; desktop result remains unverified.', flush=True)


if __name__ == '__main__':
    main()
