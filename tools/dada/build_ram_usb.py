#!/usr/bin/env python3
"""Reuse the checked FD and build a USB-only loader without automatic reset/slot writes."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mu', type=Path, required=True)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    (out / 'inputs').mkdir()
    for name in ('BootShim.bin', 'SILICIUM_UEFI.fd'):
        shutil.copy2(args.base / name, out / name)
    shutil.copy2(args.base / 'inputs/dada-merged.dtb', out / 'inputs/dada-merged.dtb')
    flags = ['-DDIRECT_PHONE_PLAT', '-DWARSAW', '-DDADA_DISPLAY_ONLY', '-DDADA_USB_RAM_ONLY']
    compiler = ['clang', '--target=aarch64-unknown-windows', '-ffreestanding', '-fno-builtin',
                '-fno-stack-protector', '-fshort-wchar', '-mgeneral-regs-only', '-O1',
                '-Wall', '-Wextra', '-Wno-unused-parameter', *flags]
    commands = []

    def run(command, **kwargs):
        commands.append([str(x) for x in command])
        subprocess.run(command, check=True, **kwargs)

    # Confirm the actual preprocessed entry path cannot reach the automatic boot/slot branch.
    source = ROOT / 'xnuloader/loader.c'
    preprocessed = subprocess.check_output([*compiler, '-E', '-P', str(source)], text=True)
    entry = preprocessed[preprocessed.rindex('\nefi_main('):]
    for forbidden in ('wdt_arm();', 'ui_boot_menu();', 'slot_maintain_current();', 'block_start();'):
        assert forbidden not in entry, forbidden
    assert 'usb_command_loop();' in entry
    with (out / 'loader-build.log').open('wb') as log:
        run([*compiler, '-c', str(source), '-o', str(out / 'loader.obj')], stdout=log, stderr=log)
        run(['lld-link', '/subsystem:efi_application', '/entry:efi_main', '/nodefaultlib',
             '/base:0xA1000000', '/out:' + str(out / 'loader-dada-ram-usb.efi'),
             str(out / 'loader.obj')], stdout=log, stderr=log)
    loader = out / 'loader-dada-ram-usb.efi'
    assert b'USB RAM boot; watchdog arming and menu countdown disabled' in loader.read_bytes()
    assert b'watchdog: status ret=' not in loader.read_bytes()
    run(['python3', str(ROOT / 'xnuloader/mkpayload.py'), '-o', str(out / 'loader-usb.xnup'),
         'loader.efi=' + str(loader), '--identity', 'v57', '--usb'])
    image_name = 'warsaw-dada-ram-usb-r2.img'
    env = dict(os.environ, MKXNUIMG=str(ROOT / 'mkxnuimg.py'))
    run(['python3', str(ROOT / 'docs/tools/assemble-xnu-img.py'),
         '--shim', str(out / 'BootShim.bin'), '--fd', str(out / 'SILICIUM_UEFI.fd'),
         '--payload', str(out / 'loader-usb.xnup'), '--dtb', str(out / 'inputs/dada-merged.dtb'),
         '--ramdisk', str(args.mu / 'Resources/ramdisk'),
         '--mkbootimg', str(args.mu / 'Resources/Scripts/mkbootimg.py'),
         '--out', str(out / image_name)], env=env)
    run(['python3', str(ROOT / 'tools/dada/verify_uefi.py'), '--mu', str(args.mu),
         '--output', str(out), '--image-name', image_name, '--loader-name', loader.name])
    (out / 'loader-entry.patch').write_bytes(subprocess.check_output(
        ['git', '-C', str(ROOT), 'diff', '--', 'xnuloader/loader_06_usb_entry.inc']))
    report = {'base_fd_sha256': sha(out / 'SILICIUM_UEFI.fd'), 'reuse_completed_edk2_fd': True,
              'image': image_name, 'image_sha256': sha(out / image_name),
              'loader_sha256': sha(loader), 'compiler_defines': flags,
              'loader_watchdog_armed': False, 'host_silence_reset_enabled': False,
              'boot_menu_countdown_enabled': False, 'automatic_slot_maintenance': False,
              'automatic_storage_survey': False, 'kernel_peripheral_drivers_changed': False,
              'entry_preprocessor_checks_passed': True, 'commands': commands,
              'hardware_tested': False}
    (out / 'build-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print('USB RAM r2 built; watchdog and automatic boot menu disabled.', flush=True)


if __name__ == '__main__':
    main()
