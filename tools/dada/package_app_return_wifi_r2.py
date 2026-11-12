#!/usr/bin/env python3
"""Package the XPC lifetime fix with the dada PCIe Wi-Fi-on boot profile."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from build_app_return_wifi import ROOT
from package_touch_experimental import kernel_layout, payload_check

ARTIFACTS = ROOT / 'out/dada-app-return-wifi-r2-20261111'
OUT = ARTIFACTS / 'package'
R1 = ROOT / 'out/dada-app-return-wifi-r1-20261111'
BASE = ROOT / 'out/dada-language-native-r3-20261111/boot-package'
FW = Path('/home/LZY/warsaw-ios26-20261110')
ROOT_SOURCE = Path('/home/LZY/warsaw-dada-app-return-wifi-r1-20261111/root')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(8 << 20): h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def run(name, command, **kwargs):
    save(OUT / (name + '.command.json'), list(map(str, command)))
    with (OUT / (name + '.log')).open('w') as log:
        subprocess.run(list(map(str, command)), stdout=log, stderr=log, check=True, **kwargs)
    print('PASS', name, flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not (OUT / 'root').exists():
        shutil.copytree(ROOT_SOURCE, OUT / 'root', symlinks=True)
    root = OUT / 'root'
    dylib = ARTIFACTS / 'sb-xpc-r2/libredwood-sb26.dylib'
    shutil.copy2(dylib, root / 'lib/libredwood-sb26.dylib')
    shutil.copy2(ARTIFACTS / 'sb-xpc-r2/build.json', OUT / 'springboard-library.json')

    # Preserve the already-tested System and every existing root file except this library.
    for folder, dirs, files in os.walk(ROOT_SOURCE, followlinks=False):
        for name in files:
            source = Path(folder) / name
            target = root / source.relative_to(ROOT_SOURCE)
            if source.relative_to(ROOT_SOURCE).as_posix() == 'lib/libredwood-sb26.dylib':
                continue
            if source.is_symlink(): assert os.readlink(source) == os.readlink(target)
            else: assert sha(source) == sha(target), source

    disk = OUT / 'root26.apfs'
    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    run('apfs-build', ['python3', FW / 'build/tools/apfs/mkapfs.py', '-z', '--plain',
        'fw', '-o', disk, 'redwood=' + str(root)], cwd=FW / 'build/userland', env=env)
    run('apfs-check', ['bash', FW / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(root)], cwd=FW / 'build/tools/apfs', env=env)

    cfg = json.loads((BASE / 'boot-config.json').read_text())
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = R1 / 'kernel-app-return-wifi.bin'
    image = OUT / 'warsaw-dada-app-return-wifi-r2.img'
    shutil.copy2(R1 / 'warsaw-dada-app-return-wifi-r1.img', image)
    system = BASE / 'system26.apfs'
    assert system.stat().st_size == cfg['files']['system26.apfs']['bytes']
    base_tokens = [item for item in cfg['cmdline'].split()
        if item.split('=')[0] not in ('epoch', 'wifi', 'pcie', 'bt', 'dada_wifi',
            'pcie_power', 'pcie_sleep_clock', 'pcie_aop_pdc')]
    # Disable the legacy WCN driver; dada's ath12k driver starts on PCIe step 10.
    cmdline = ' '.join(base_tokens) + (
        f' epoch={int(time.time())} bt=0 dada_wifi=1 wifi=0 pcie=10'
        ' pcie_power=1 pcie_sleep_clock=board pcie_aop_pdc=1 wifi_station=1')
    payload = OUT / 'ios-payload-wifi-r2.bin'
    run('payload-wifi', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
        '--identity', cfg['identity'], '--usb', '--memsize', cfg['memsize_upper_bound_hex'],
        '--cmdline', cmdline, '--require-cmd', 'dadatouch=1', '--require-cmd', 'surf_native_xr=1',
        '--require-cmd', 'dada_wifi=1', '--require-cmd', 'pcie=10', '--require-cmd', 'wifi_station=1',
        '--forbid-cmd', 'loader_return_after'])
    entries = payload_check(payload, {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk})
    shutil.copy2(R1 / 'kernel-app-return-wifi.bin', OUT / 'kernel-app-return-wifi.bin')
    save(OUT / 'boot-config.json', {
        'version': cfg['version'], 'build': cfg['build'], 'uefi': image.name,
        'uefi_sha256': sha(image), 'system_sha256': cfg['files']['system26.apfs']['sha256'],
        'system_reference': str(system), 'kernel_sha256': sha(kernel),
        'root_sha256': sha(disk), 'payload': payload.name, 'payload_sha256': sha(payload),
        'payload_entries': entries, 'cmdline': cmdline,
        'memsize_upper_bound_hex': cfg['memsize_upper_bound_hex'],
        'wifi_profile': {'device': 'dada cnss-peach / 17cb:110e', 'driver': 'Warsaw ath12k via PCIe step 10',
            'wifi_legacy_driver_disabled': True, 'power_profile': 'dada S5F PDC; board sleep clock',
            'network_tested': False},
        'automatic_return_timeout': False,
        'touch_chinese_native_glass_preserved': True,
        'rollback_package': str(BASE),
        'partition_flash_commands': False,
    })
    save(OUT / 'STATUS.json', {
        'built': True, 'hardware_tested': False, 'app_return_fix_verified': False,
        'wifi_enabled': True, 'wifi_device_enumerated': None, 'wifi_connected': None,
        'wifi_scan_verified': False, 'springboard_xpc_stress_test_passed': True,
        'chinese_system_preserved': True, 'touch_preserved': True,
        'native_glass_preserved': True, 'rollback_package': str(BASE),
        'no_partition_flash': True, 'experimental_toolchain': True,
        'kernel_and_uefi_same_as_r1': True,
    })
    print('PASS Wi-Fi-on dada test package; hardware verification pending', flush=True)


if __name__ == '__main__':
    main()
