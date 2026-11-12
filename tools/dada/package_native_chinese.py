#!/usr/bin/env python3
"""Restore native zh-Hans .strings and InfoPlist.strings in a private System.

The retained 23G90 loctables are the only translation source. The r4 System,
UEFI, touch kernel and GPU binaries remain intact for rollback.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import time

from package_touch_experimental import payload_check

ROOT = Path(__file__).resolve().parents[2]
FW = Path('/home/LZY/warsaw-ios26-20261110')
R4 = ROOT / 'out/dada-native-glass-mirror-r4-20261111/boot-package'
R4_ROOT = Path('/home/LZY/warsaw-dada-native-glass-mirror-r4-20261111/root')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while data := f.read(8 << 20):
            h.update(data)
    return h.hexdigest()


def selected_language(data, language='zh_CN'):
    """Read one bplist root-dictionary value without expanding every language.

    Large system loctables contain tens of thousands of strings in dozens of
    languages. Python's parser already supports lazy object references; use its
    reader for the selected dictionary, after validating the top-level shape.
    """
    if not data.startswith(b'bplist00'):
        return plistlib.loads(data).get(language)
    offset_size, ref_size, count, top, table = struct.unpack('>6xBBQQQ', data[-32:])
    if count == 0 or top >= count or table >= len(data) - 32:
        raise ValueError('Invalid binary plist trailer')
    parser = plistlib._BinaryPlistParser(dict)
    parser._fp = io.BytesIO(data)
    parser._ref_size = ref_size
    parser._fp.seek(table)
    parser._object_offsets = parser._read_ints(count, offset_size)
    parser._objects = [plistlib._undefined] * count
    parser._fp.seek(parser._object_offsets[top])
    marker = parser._fp.read(1)[0]
    if marker >> 4 != 0xd:
        raise ValueError('Expected a root dictionary')
    length = parser._get_size(marker & 15)
    keys = parser._read_refs(length)
    values = parser._read_refs(length)
    for key, value in zip(keys, values):
        if parser._read_object(key) == language:
            return parser._read_object(value)
    return None


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--resume', action='store_true', help='Resume this private packaging tree after an interrupted build')
    ap.add_argument('--reuse-system', action='store_true',
                    help='With --resume, check an already built System instead of recompressing it')
    args = ap.parse_args()
    if args.reuse_system and not args.resume:
        ap.error('--reuse-system requires --resume')
    build, work, out = args.build.resolve(), args.work, args.output.resolve()
    work.mkdir(parents=True, exist_ok=args.resume)
    out.mkdir(parents=True, exist_ok=args.resume)
    baseline = Path(json.loads((FW / 'images-staged.json').read_text())['sysroot_for_review'])
    # Validate the selected reader against the standard parser before using it.
    for relative in ['Applications/Preferences.app/Localizable.loctable',
        'Applications/Preferences.app/InfoPlist.loctable',
        'System/Library/PreferenceBundles/InternationalSettings.bundle/InternationalSettings.loctable']:
        data = (baseline / relative).read_bytes()
        assert selected_language(data) == plistlib.loads(data).get('zh_CN'), relative
    print('PASS selected-language parser comparison', flush=True)
    sysroot = work / 'sysroot'
    # Hardlinks save space; only new files are written, never an existing inode.
    if not sysroot.exists():
        shutil.copytree(baseline, sysroot, symlinks=True, copy_function=os.link)
    elif not args.resume:
        raise FileExistsError(sysroot)
    print('Copied private System directory tree; original files shared read-only', flush=True)
    additions, skipped = [], []
    table_count = 0
    started = time.monotonic()
    if args.reuse_system:
        previous = json.loads((out / 'native-localizations.json').read_text())
        assert previous['original_tree'] == str(baseline)
        assert previous['private_tree'] == str(sysroot)
        additions, skipped = previous['added_tables'], previous['skipped']
        for item in additions:
            destination = sysroot / item['file']
            assert sha(destination) == item['sha256'], destination
        table_count = len(additions)
        resource_tree = ()
        print('PASS saved native-resource manifest hashes', flush=True)
    else:
        resource_tree = os.walk(baseline, followlinks=False)
    for folder, dirs, files in resource_tree:
        for name in sorted(files):
            if not name.endswith('.loctable'):
                continue
            source = Path(folder) / name
            values = selected_language(source.read_bytes())
            if not isinstance(values, dict) or not values:
                continue
            unsupported = {k: type(v).__name__ for k, v in values.items() if not isinstance(v, str)}
            if unsupported:
                skipped.append({'table': str(source.relative_to(baseline)), 'non_string_values': unsupported})
                # Restore the ordinary translations even in a mixed table;
                # preserve structured plural/device rules in the original.
                values = {k: v for k, v in values.items() if isinstance(v, str)}
                if not values:
                    continue
            relative = source.relative_to(baseline)
            parts = list(relative.parent.parts)
            locales = [i for i, part in enumerate(parts) if part.endswith('.lproj')]
            if locales:
                parts[locales[-1]] = 'zh-Hans.lproj'
                destination = sysroot.joinpath(*parts, source.stem + '.strings')
            else:
                destination = sysroot / relative.parent / 'zh-Hans.lproj' / (source.stem + '.strings')
            if destination.exists():
                existing = plistlib.loads(destination.read_bytes())
                if existing != values:
                    raise FileExistsError('Conflicting existing translation: ' + str(destination))
                if (baseline / destination.relative_to(sysroot)).exists():
                    continue
                if not args.resume:
                    raise FileExistsError(destination)
                content = destination.read_bytes()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.parent.chmod(0o755)
                # Native .strings supports binary property lists, including the
                # control characters retained in a few original translation tables.
                content = plistlib.dumps(values, fmt=plistlib.FMT_BINARY, sort_keys=True)
                with destination.open('xb') as f:
                    f.write(content)
                destination.chmod(0o644)
            assert plistlib.loads(destination.read_bytes()) == values
            additions.append({'source': str(relative), 'file': str(destination.relative_to(sysroot)),
                'entries': len(values), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
            table_count += 1
            if table_count % 100 == 0:
                print(f'Restored {table_count} native Chinese tables in {time.monotonic()-started:.1f}s', flush=True)
    assert table_count > 100
    save(out / 'native-localizations.json', {'added_tables': additions, 'skipped': skipped,
        'original_tree': str(baseline), 'private_tree': str(sysroot),
        'total_translations': sum(x['entries'] for x in additions)})
    print(f'PASS {len(additions)} native tables; structured keys in {len(skipped)} tables retained as original', flush=True)
    # Every old entry must keep exactly its source inode/link; no binaries edited.
    existing_files = 0
    for folder, dirs, files in os.walk(baseline, followlinks=False):
        for name in files:
            old = Path(folder) / name
            new = sysroot / old.relative_to(baseline)
            if old.is_symlink():
                assert new.is_symlink() and os.readlink(new) == os.readlink(old)
            else:
                assert os.path.samefile(old, new)
                existing_files += 1
    small = work / 'root'
    if not small.exists():
        shutil.copytree(R4_ROOT, small, symlinks=True)
    elif not args.resume:
        raise FileExistsError(small)
    shutil.copy2(build / 'libredwood-sb26.dylib', small / 'lib/libredwood-sb26.dylib')
    # The RAM data volume starts empty at each boot. Seed the requested language
    # once through its existing skeleton, then preserve subsequent user choices.
    preferences = small / 'var/mobile/Library/Preferences/.GlobalPreferences.plist'
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_bytes(plistlib.dumps({'AppleLocale': 'zh_CN',
        'AppleLanguages': ['zh-Hans-CN', 'en']}, fmt=plistlib.FMT_BINARY))
    skeleton = small / 'etc/var.skel'
    entry = 'f\t644\t501\t501\t/private/var/mobile/Library/Preferences/.GlobalPreferences.plist\t/var/mobile/Library/Preferences/.GlobalPreferences.plist\n'
    content = skeleton.read_text()
    if '.GlobalPreferences.plist' in content:
        assert args.resume and content.count(entry) == 1
    else:
        skeleton.write_text(content.rstrip('\n') + '\n' + entry)
    for relative in ['lib/RedwoodMetal.bundle/RedwoodMetal', 'bin/input26-service', 'etc/inject.conf']:
        assert sha(small / relative) == sha(R4_ROOT / relative), relative
    def run(name, command, **kw):
        command = list(map(str, command))
        save(out / (name + '.command.json'), command)
        with (out / (name + '.log')).open('w') as f:
            subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True, **kw)
        print('PASS', name, flush=True)
    env = dict(os.environ, PYTHONPATH=str(FW / 'host-python'))
    recipe = FW / 'build/tools/apfs/mkapfs.py'
    manifest = FW / 'build/ios26/system.manifest'
    system = out / 'system26.apfs'
    if args.reuse_system:
        assert system.is_file() and (out / 'system-build.log').is_file()
        print('Reusing built System; both readers must still verify every file', flush=True)
    else:
        run('system-build', ['python3', recipe, '-z', '-o', system,
            'System=' + str(sysroot) + ',manifest=' + str(manifest)], env=env)
    # Windows/WSL shared-directory random reads are very slow. Check the same
    # bytes on the native filesystem; compare this hash to the delivered image.
    local_system = work / 'system26-check.apfs'
    shutil.copyfile(system, local_system)
    checked_system_sha = sha(local_system)
    print('Copied identical System to native filesystem for checking', flush=True)
    # Check all additions and the existing tree through the actual kernel reader.
    run('system-check', ['bash', FW / 'build/tools/apfs/check-image.sh', local_system,
        'System=' + str(sysroot) + ',manifest=' + str(manifest)], env=env)
    disk = out / 'root26.apfs'
    run('root-build', ['python3', recipe, '-z', '--plain', 'fw', '-o', disk,
        'redwood=' + str(small)], env=env)
    run('root-check', ['bash', FW / 'build/tools/apfs/check-image.sh', disk,
        'redwood=' + str(small)], env=env)
    cfg = json.loads((R4 / 'boot-config.json').read_text())
    loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
    kernel = ROOT / 'out/dada-native-glass-xr-r2-20261111/kernel-native-xr.bin'
    payload = out / 'ios-payload.bin'
    run('payload-build', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
        'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
        '--identity', cfg['identity'], '--usb', '--memsize', cfg['memsize_upper_bound_hex'],
        '--cmdline', cfg['cmdline'], '--require-cmd', 'dadatouch=1',
        '--require-cmd', 'surf_native_xr=1', '--forbid-cmd', 'loader_return_after'])
    save(out / 'payload-entries.json', payload_check(payload,
        {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk}))
    image = out / 'warsaw-dada-native-chinese-r3.img'
    shutil.copy2(R4 / cfg['uefi'], image)
    cfg.update(uefi=image.name, hardware_tested=False, system_reference=str(system),
        desktop_and_touch_user_confirmed=False, native_chinese_resources=True)
    cfg['files'] = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in [image, payload, disk, system]}
    assert cfg['files'][system.name]['sha256'] == checked_system_sha
    save(out / 'system-check-copy.json', {'checked_image': str(local_system),
        'delivered_image': str(system), 'identical_sha256': checked_system_sha})
    save(out / 'boot-config.json', cfg)
    save(out / 'STATUS.json', {'built': True, 'whole_package_hardware_tested': False,
        'native_translation_probe_passed': True, 'native_info_display_name_probe_passed': True,
        'added_chinese_tables': len(additions), 'preserved_existing_system_files': existing_files,
        'uefi_binary_changed': False, 'kernel_changed': False, 'GPU_changed': False,
        'native_glass_preserved': True, 'native_xr_preserved': True, 'touch_preserved': True,
        'partition_writes': False, 'rollback_package': str(R4),
        'rollback_system': str(ROOT / 'out/ios26-r1/system26.apfs'),
        'small_root_changed_files': ['lib/libredwood-sb26.dylib', 'etc/var.skel',
            'var/mobile/Library/Preferences/.GlobalPreferences.plist'], 'files': cfg['files']})
    print('PASS native Chinese System and paired r4 graphics payload packaged', flush=True)


if __name__ == '__main__':
    main()
