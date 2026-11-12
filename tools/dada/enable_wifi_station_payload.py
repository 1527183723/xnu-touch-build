#!/usr/bin/env python3
"""Add the station interface option before the initial Wi-Fi payload upload."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from package_touch_experimental import payload_check

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'out/dada-app-return-wifi-r2-20261111/package'


def main():
    config = json.loads((OUT / 'boot-config.json').read_text())
    payload = OUT / config['payload']
    backup = OUT / 'before-station-profile'
    backup.mkdir(exist_ok=False)
    for name in (payload.name, 'boot-config.json', 'payload-wifi.command.json', 'SHA256SUMS'):
        shutil.copy2(OUT / name, backup / name)
    command = json.loads((OUT / 'payload-wifi.command.json').read_text())
    if os.name == 'nt':
        command = [arg.replace('/mnt/e/', 'E:/') for arg in command]
    command[0] = sys.executable
    temporary = payload.with_suffix('.station-tmp')
    command[command.index('-o') + 1] = str(temporary)
    cmdline = config['cmdline'] + ' wifi_station=1'
    assert 'wifi_station=' not in config['cmdline']
    command[command.index('--cmdline') + 1] = cmdline
    command += ['--require-cmd', 'wifi_station=1']
    with (OUT / 'payload-station-build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
    expected = {}
    for arg in command:
        if any(arg.startswith(name + '=') for name in ('loader.efi', 'kernel', 'ramdisk')):
            name, path = arg.split('=', 1)
            expected[name] = Path(path)
    entries = payload_check(temporary, expected)
    config['cmdline'] = cmdline
    config['payload_entries'] = entries
    config['payload_sha256'] = hashlib.sha256(temporary.read_bytes()).hexdigest()
    config['wifi_profile']['station_interface_enabled'] = True
    temporary.replace(payload)
    (OUT / 'boot-config.json').write_text(json.dumps(config, indent=2) + '\n')
    command[command.index('-o') + 1] = str(payload)
    (OUT / 'payload-wifi.command.json').write_text(json.dumps(command, indent=2) + '\n')
    manifest = [(config['uefi_sha256'], config['uefi']),
        (config['payload_sha256'], config['payload']),
        (config['root_sha256'], 'root26.apfs'),
        (config['kernel_sha256'], 'kernel-app-return-wifi.bin')]
    (OUT / 'SHA256SUMS').write_text(''.join(h + '  ' + p + '\n' for h, p in manifest))
    (OUT / 'station-profile.json').write_text(json.dumps({
        'previous_payload_preserved': str(backup / payload.name),
        'added_boot_argument': 'wifi_station=1',
        'reason': 'wifi=0 disables the legacy defaults; lx_start needs this option to offer en0 to iOS',
        'payload_sha256': config['payload_sha256'],
        'new_payload_bytes': payload.stat().st_size,
        'system_root_kernel_unchanged': True,
    }, indent=2) + '\n')
    print('PASS explicit station interface; prior payload preserved; no USB operation')


if __name__ == '__main__':
    main()
