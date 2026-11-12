#!/usr/bin/env python3
"""Keep Chinese System intact; package new kernel and stock peach firmware."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from package_touch_experimental import kernel_layout, payload_check
from build_app_return_wifi import ROOT, WORK, OUT

BASE = ROOT / 'out/dada-language-native-r3-20261111/boot-package'
ORIGINAL_ROOT = Path('/home/LZY/warsaw-dada-native-chinese-r3-20261111/root')
FW = Path('/home/LZY/warsaw-ios26-20261110')
STOCK = Path('/mnt/e/2026/AI/ios26/驱动/WiFi')

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        while data := f.read(8 << 20): h.update(data)
    return h.hexdigest()

def save(p, value):
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')

def run(name, cmd, **kw):
    save(OUT / (name + '.command.json'), list(map(str, cmd)))
    with (OUT / (name + '.log')).open('w') as log:
        subprocess.run(list(map(str, cmd)), stdout=log, stderr=log, check=True, **kw)
    print('PASS', name, flush=True)

def main():
    root = WORK / 'root'
    if not root.exists(): shutil.copytree(ORIGINAL_ROOT, root, symlinks=True)
    manifest = []
    for name in ['amss.bin', 'amss20.bin', 'phy_ucode.elf', 'phy_ucode20.elf',
        'aux_ucode.elf', 'aux_ucode20.elf', 'bd_o3.elf', 'regdb_xiaomi.bin',
        'regdb.bin', 'qdss_trace_config_v2.cfg']:
        src, dest = STOCK / name, root / 'fw' / name
        assert src.is_file() and src.stat().st_size > 0
        assert not dest.exists() or sha(src) == sha(dest), dest
        shutil.copy2(src, dest)
        dest.chmod(0o644)
        manifest.append({'file': 'fw/' + name, 'bytes': dest.stat().st_size, 'sha256': sha(dest)})
    shutil.copy2(STOCK / 'source-info.json', root / 'etc/dada-wifi-source.json')
    # Every existing frontend/input/localization file must be unchanged.
    for folder, dirs, files in os.walk(ORIGINAL_ROOT, followlinks=False):
        for name in files:
            src = Path(folder) / name
            dest = root / src.relative_to(ORIGINAL_ROOT)
            if src.is_symlink(): assert os.readlink(src) == os.readlink(dest)
            else: assert sha(src) == sha(dest), src
    save(OUT / 'wifi-firmware.json', {'source': json.loads((STOCK / 'source-info.json').read_text()),
        'files': manifest, 'hardware_network_tested': False})
    disk = OUT / 'root26.apfs'
    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    run('apfs-build', ['python3', FW / 'build/tools/apfs/mkapfs.py', '-z', '--plain',
        'fw', '-o', disk, 'redwood=' + str(root)], cwd=FW / 'build/userland', env=env)
    run('apfs-check', ['bash', FW / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(root)], cwd=FW / 'build/tools/apfs', env=env)
    cfg = json.loads((BASE / 'boot-config.json').read_text())
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = OUT / 'kernel-app-return-wifi.bin'
    save(OUT / 'kernel-layout.json', kernel_layout(kernel))
    report = json.loads((OUT / 'linux-touch-compile.json').read_text())
    assert report['full_kernel_build_returncode'] == 0 and report['kernel_linked']
    symbols = subprocess.check_output(['llvm-nm-21', WORK / 'xnu12377/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped'], text=True)
    for required in ['_hakios_dada_touch_init', '_redwood_gpu_fault_note', '_lx_start', '_dada_exit_sysctl']:
        assert required in symbols, required
    # Two explicit boot profiles allow GPU transition validation independently
    # of the first real power-up of the Wi-Fi device. Same kernel/root/System.
    tokens = [s for s in cfg['cmdline'].split() if s.split('=')[0] not in
        ('epoch', 'pcie', 'wifi', 'bt', 'dada_wifi', 'pcie_power', 'pcie_sleep_clock', 'pcie_aop_pdc')]
    base = ' '.join(tokens) + f' epoch={int(time.time())} bt=0 dada_wifi=1'
    profiles = {}
    for name, extra in [('graphics', ' wifi=0 pcie=0'),
        ('wifi', ' wifi=0 pcie=10 pcie_power=1 pcie_sleep_clock=board pcie_aop_pdc=1')]:
        payload = OUT / ('ios-payload-' + name + '.bin')
        cmdline = base + extra
        run('payload-' + name, ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
            'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
            '--identity', cfg['identity'], '--usb', '--memsize', cfg['memsize_upper_bound_hex'],
            '--cmdline', cmdline, '--require-cmd', 'dadatouch=1', '--require-cmd', 'surf_native_xr=1',
            '--forbid-cmd', 'loader_return_after'])
        entries = payload_check(payload, {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk})
        profiles[name] = {'cmdline': cmdline, 'payload': payload.name, 'entries': entries,
            'sha256': sha(payload), 'hardware_tested': False}
    image = OUT / 'warsaw-dada-app-return-wifi-r1.img'
    shutil.copy2(BASE / cfg['uefi'], image)
    system = BASE / 'system26.apfs'
    # The existing Chinese System was already fully checked. Do not rewrite it.
    assert system.stat().st_size == cfg['files']['system26.apfs']['bytes']
    save(OUT / 'boot-config.json', {'version': cfg['version'], 'build': cfg['build'],
        'uefi': image.name, 'uefi_sha256': sha(image), 'uefi_binary_changed': False,
        'system_reference': str(system), 'system_recorded_sha256': cfg['files']['system26.apfs']['sha256'],
        'profiles': profiles, 'root_sha256': sha(disk), 'kernel_sha256': sha(kernel),
        'memsize_upper_bound_hex': cfg['memsize_upper_bound_hex'], 'automatic_return_timeout': False})
    save(OUT / 'STATUS.json', {'built': True, 'hardware_tested': False,
        'soft_restart_fixed': None, 'wifi_connected': None, 'native_glass_preserved': True,
        'touch_source_preserved': True, 'chinese_system_preserved': True,
        'rollback_package': str(BASE), 'no_partition_flash': True,
        'experimental_toolchain': True, 'wifi_stub': 'ath12k_wifi7_pci_init; hardware bus replaced by lx_pci.c'})
    print('PASS paired app-return / Wi-Fi test packages; phone verification pending', flush=True)

if __name__ == '__main__': main()
