#!/usr/bin/env python3
"""Build an isolated native Liquid Glass trial over the working iOS 26 touch r3.

The existing ios-dodge NativeMetal/metal2vulkan/Turnip binaries already contain
the glass function-linking code. This profile lets SpringBoard use its original
icon layers instead of overriding them with the port's PNG/bitmap fallbacks.
Run in WSL; the original root, kernel and System are never overwritten.
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
from package_touch_experimental import payload_check

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-touch-experimental-r3-20261110'
BASE_ROOT = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110/root')
BASE_SOURCE = Path('/home/LZY/warsaw-dada-native-metal-ios26-r5-20261110/source')
FW = Path('/home/LZY/warsaw-ios26-20261110')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--work', type=Path, default=Path('/home/LZY/warsaw-native-glass-r1-20261111'))
    ap.add_argument('--output', type=Path, default=ROOT / 'out/dada-native-glass-r1-20261111')
    args = ap.parse_args()
    work, out = args.work, args.output
    work.mkdir(parents=True, exist_ok=False)
    out.mkdir(parents=True, exist_ok=False)
    source = work / 'source'
    shutil.copytree(BASE_SOURCE, source)

    def run(name, command, **kw):
        command = [str(x) for x in command]
        write_json(out / (name + '.command.json'), command)
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, **kw)
        print('PASS', name, flush=True)

    # All changes are explicit and isolated to the experimental compatibility
    # library. The stock provider is allowed to return its own layer or nil;
    # diagnostics must not silently replace a failed native layer with a PNG.
    text = (source / 'inject_sb26.c').read_text()
    original = text
    edits = {
        '    install_bitmap_selection(slide);':
            '    fprintf(stderr, "native-glass: stock bitmap selection; PNG and icon-layer overrides disabled\\n");',
        '    sb26_fill_all_icons();':
            '    /* Native Glass trial: preserve the original icon rendering result. */',
        '    sb26_glass_pass();':
            '    /* Native Glass trial: do not simulate glass by lowering layer opacity. */',
        '    sb26_install_all_icon_layer_fallbacks();':
            '    /* Native Glass trial: preserve the original provider methods. */',
        '    if (cal) install_calendar_icon_hooks(cal);':
            '    /* Native Glass trial: keep the original Calendar provider. */',
        '    if (cal_cls) install_calendar_icon_hooks(cal_cls);':
            '    /* Native Glass trial: keep the original Calendar provider. */',
    }
    for before, after in edits.items():
        assert before in text, before
        text = text.replace(before, after)
    assert '    sb26_fill_wallpaper();' in text
    assert '    sb26_install_layer_dump();' in text
    assert '    install_unlock();' in text
    assert 'CGContextTranslateCTM(ctx, 0, (CGFloat)h);' not in text
    (source / 'inject_sb26.c').write_text(text)
    (out / 'native-glass-profile.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), text.splitlines(True),
        fromfile='a/userland/src/inject_sb26.c', tofile='b/userland/src/inject_sb26.c')))

    prior = json.loads((ROOT / 'out/dada-native-metal-ios26-r5-orientation/sb26-compile.command.json').read_text())
    command = [str(source / Path(x).name) if x.startswith(str(BASE_SOURCE) + '/') else x for x in prior]
    library = out / 'libredwood-sb26.dylib'
    command[command.index('-o') + 1] = str(library)
    run('sb26-compile', command, cwd=FW / 'build/userland')
    clang = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110/clang')
    sdk = Path('/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk')
    run('session-helper', [clang, '-target', 'arm64e-apple-ios26.0', '-isysroot', sdk,
        '-O2', '-Wl,-adhoc_codesign', ROOT / 'tools/dada/session_icon_library.c',
        '-o', out / 'session-icon-library'])
    write_json(out / 'macho-report.json', {
        name: inspect(out / name) for name in ['libredwood-sb26.dylib', 'session-icon-library']})

    root = work / 'root'
    shutil.copytree(BASE_ROOT, root, symlinks=True)
    shutil.copy2(library, root / 'lib/libredwood-sb26.dylib')
    (root / 'etc/glass.conf').write_text(
        '# Native CoreAnimation/RenderBox Glass; cosmetic PNG/opacity pass disabled.\n'
        'on = 0\nicon_color = 1.0\nicon_white = 1.0\nicon_sheen = 0.0\n'
        'icon_layer = 1.0\ndock = 1.0\nsearch = 1.0\nwidget = 1.0\n')
    provenance = {
        'source_repository': 'https://github.com/faridbtw/ios-dodge',
        'source_branch': 'iOS27', 'source_revision': '2b3f8ae564807268f3e595c30f682e23a947816a',
        'gpu_code_reused_from_r3': True,
        'feature': 'stock CoreAnimation/RenderBox icon layers using existing NativeMetal linked-function support',
        'compatibility_change': 'disable software bitmap forcing, generated icon layers and timed PNG/opacity replacements',
        'native_glass_or_icon_pixels_verified': False,
        'expected_risk': 'native providers previously returned nil; icons may be missing or corrupt',
    }
    write_json(root / 'etc/native-glass-profile.json', provenance)
    changed = []
    for p in sorted(root.rglob('*')):
        old = BASE_ROOT / p.relative_to(root)
        if p.is_symlink():
            assert old.is_symlink() and os.readlink(old) == os.readlink(p)
        elif p.is_file() and (not old.exists() or sha(p) != sha(old)):
            changed.append(str(p.relative_to(root)))
    assert changed == ['etc/glass.conf', 'etc/native-glass-profile.json', 'lib/libredwood-sb26.dylib'], changed
    for relative in ['bin/input26-service', 'etc/inject.conf',
                     'lib/RedwoodMetal.bundle/RedwoodMetal', 'lib/RedwoodMetal.bundle/libvulkan_freedreno.dylib',
                     'var/mobile/Library/Preferences/com.apple.coreanimation.plist']:
        assert sha(root / relative) == sha(BASE_ROOT / relative), relative

    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    build = FW / 'build'
    disk = out / 'root26.apfs'
    run('apfs-build', ['python3', build / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
        '-o', disk, 'redwood=' + str(root)], cwd=build / 'userland', env=env)
    run('apfs-check', ['bash', build / 'tools/apfs/check-image.sh', disk, 'redwood=' + str(root)],
        cwd=build / 'tools/apfs', env=env)
    config = json.loads((BASE / 'boot-config.json').read_text())
    cmdline = ' '.join(t for t in config['cmdline'].split() if not t.startswith('epoch=')) + f' epoch={int(time.time())}'
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = BASE / 'kernel-touch.bin'
    payload = out / 'ios-payload.bin'
    run('payload', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
        '--identity', 'v57', '--usb', '--memsize', '240000000', '--cmdline', cmdline,
        '--require-cmd', 'dadatouch=1', '--forbid-cmd', 'loader_return_after'])
    write_json(out / 'payload-entries.json', payload_check(payload,
        {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk}))
    image = out / 'warsaw-dada-native-glass-r1.img'
    shutil.copy2(BASE / config['uefi'], image)
    shutil.copytree(BASE / 'licenses', out / 'licenses')
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in [disk, payload, image, library]}
    config.update(cmdline=cmdline, uefi=image.name, hardware_tested=False, files=files,
        native_glass_profile=True, uefi_binary_changed=False)
    write_json(out / 'boot-config.json', config)
    report = dict(provenance, changed_small_root_files=changed,
        system_reference=str(ROOT / 'out/ios26-r1/system26.apfs'),
        touch_kernel_sha256=sha(kernel), apfs_content_checked=True, payload_entries_checked=True,
        kernel_uefi_and_gpu_binaries_changed=False, files=files,
        rollback_payload=str(BASE / 'ios-payload.bin'), private_source_directory=str(source),
        source_files={p.name: sha(p) for p in sorted(source.iterdir()) if p.is_file()})
    write_json(out / 'build-manifest.json', report)
    write_json(out / 'STATUS.json', {'built': True, 'hardware_tested': False,
        'native_glass_pixels_correct': None, 'touch_confirmed': None,
        'rollback_payload': str(BASE / 'ios-payload.bin')})
    shutil.copy2(ROOT / 'docs/DADA-NATIVE-GLASS-PROFILE-20261111.md', out / 'README.md')
    print('PASS native Glass profile packaged; pixel output and touch require live validation.', flush=True)


if __name__ == '__main__':
    main()
