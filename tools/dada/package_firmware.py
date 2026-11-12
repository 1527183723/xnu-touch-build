#!/usr/bin/env python3
"""Export matched, locally built iOS 26 firmware artifacts without using the phone."""
import argparse
import importlib.util
import json
import shutil
import struct
import subprocess
import time
from pathlib import Path

from stage_firmware import digest

REPO = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--uefi-output', required=True, type=Path)
    ap.add_argument('--memsize', default='240000000', help='hex window; RAM upload requires this much contiguous free memory')
    args = ap.parse_args()
    work, build, out = args.work, args.work / 'build', args.output
    staged = json.loads((work / 'images-staged.json').read_text())
    out.mkdir(parents=True, exist_ok=True)
    cmd = ('debug=0x10e rootdev=md0 launchd_unsecure_cache=1 panic_on_crash_disabled=1 '
           'cpus=7 bootcpu=7 small_cores_after=0 '
           'adreno8=23 adreno8_pause=0 adreno8_fine=0 adreno8_watch=0 adreno8_keep=1 '
           'adreno8_return=0 adreno8_speed=14 adreno8_secure_preflight=4 '
           'touchscreen=2 buttons=1 battery=1 gpu_coherent=1 metal=1 cpufreq=1 '
           'fb_threads=2 fb_flip=2 fbwidth=1200 fbheight=2670 fbpitch=460 '
           'applog=2 autounlock=1 wifi=1 bt=4 pcie=10 '
           'metal_profile=1 fb_hz=120 fb_ca_hz=120 touch_poll_us=4000 '
           f'epoch={int(time.time())}')
    assert 'loader_return_after' not in cmd
    images = {}

    def copy(source, name):
        target = out / name
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f'Exporting {name}: {source.stat().st_size:,} bytes', flush=True)
        shutil.copy2(source, target)
        images[name] = {'bytes': target.stat().st_size, 'sha256': digest(target)}
        return target

    root = copy(build / 'userland/root26.apfs', 'root26.apfs')
    system = copy(build / 'userland/system26.apfs', 'system26.apfs')
    for name in ('root26.apfs', 'system26.apfs'):
        assert images[name] == staged['images'][name]
    kernel = copy(build / 'prebuilt/kernel-ath12k.bin', 'kernel-ath12k.bin')
    loader = copy(args.uefi_output / 'loader-dada-display.efi', 'loader-dada-display.efi')
    copy(args.uefi_output / 'warsaw-dada-display-only-r1.img', 'warsaw-dada-display-only-r1.img')
    metal = build / 'userland/root/lib'
    for name in ('libredwoodmetal.dylib', 'libredwoodair.dylib', 'libredwood-sb26.dylib'):
        copy(metal / name, 'Metal/' + name)
    for name in ('RedwoodMetal', 'Info.plist'):
        copy(metal / 'RedwoodMetal.bundle' / name, 'Metal/RedwoodMetal.bundle/' + name)
    payload = out / 'ios-payload.bin'
    subprocess.run(['python3', str(REPO / 'xnuloader/mkpayload.py'), '-o', str(payload),
                    'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(root),
                    '--identity', 'v57', '--usb', '--memsize', args.memsize,
                    '--cmdline', cmd, '--forbid-cmd', 'loader_return_after',
                    '--require-cmd', 'fbwidth=1200', '--require-cmd', 'fbheight=2670'], check=True)
    spec = importlib.util.spec_from_file_location('mkxnuimg', REPO / 'mkxnuimg.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_container(payload.read_bytes(), 0x03700000)
    assert payload.read_bytes()[-24:] == bytes(24)
    images[payload.name] = {'bytes': payload.stat().st_size, 'sha256': digest(payload)}
    report = out / 'build-info'
    report.mkdir(exist_ok=True)
    for name in ('sources-prepared.json', 'userland-compiled.json', 'air-rebuilt.json',
                 'mesa-source-overlay.json', 'mesa-patch-audit.json', 'mesa-patch-overlap.json',
                 'fix-source-hashes.json', 'host-staging-fixes.json', 'source-snapshot-audit.json',
                 'extraction-complete.json', 'images-staged.json', 'apfs-build.log'):
        shutil.copy2(work / name, report / name)
    shutil.copy2(work / 'ipsw/ipsw-inputs.json', report / 'ipsw-inputs.json')
    shutil.copy2(work / 'air-rebuild/0016-apply.log', report / '0016-apply.log')
    shutil.copy2(work / 'air-rebuild/ninja.log', report / 'air-build.log')
    shutil.copy2(REPO / 'out/ios26-r1/downloads/current-source-sha256.json', report / 'current-source-sha256.json')
    for name in ('verification.json', 'build-manifest.json'):
        source = args.uefi_output / name
        if source.exists():
            shutil.copy2(source, report / ('uefi-' + name))
    (out / 'cmdline.txt').write_text(cmd + '\n')
    (out / 'boot-config.json').write_text(json.dumps({
        'version': '26.6.2', 'build': '23G90', 'identity': 'v57', 'system_device': 'md0',
        'small_root_device': 'md1', 'memsize_upper_bound_hex': args.memsize,
        'ramdisk_upload_requires_exact_window': True,
        'window_must_be_available_in_firmware_memory_map': True,
        'actual_assigned_ram_requires_device_log': True, 'cmdline': cmd,
        'display': {'width': 1200, 'height': 2670, 'stride_bytes': 4800, 'fbpitch_density': 460},
        'peripheral_drivers': 'unmodified warsaw', 'kernel': 'official prebuilt Darwin 25.6.0 / XNU 12377.121.6',
        'system_recipe': 'repository selected-root RAM recipe, not an untrimmed stock filesystem',
        'additional_old_dada_driver_ports': False,
        'automatic_return_timeout': False, 'hardware_tested': False,
        'files': images}, indent=2) + '\n')
    shutil.copy2(REPO / 'docs/DADA-IOS26-FIRMWARE.md', out / 'README.md')
    (out / 'SHA256SUMS').write_text(''.join(f'{v["sha256"]}  {name}\n' for name, v in sorted(images.items())))
    (work / 'firmware-exported.json').write_text(json.dumps({'output': str(out), 'files': images}, indent=2) + '\n')
    print('Matched firmware bundle exported. No device operations performed.', flush=True)


if __name__ == '__main__':
    main()
