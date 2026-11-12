#!/usr/bin/env python3
"""Build a scoped 23G90 language fix from the currently deployed Glass library."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
BASE = Path('/home/LZY/warsaw-native-glass-r1-20261111/source')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--native-resources', action='store_true',
                    help='Use restored System localization files; omit the NSBundle workaround')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / 'source'
    shutil.copytree(BASE, source)
    path = source / 'inject_sb26.c'
    original = text = path.read_text()
    before = '''    } else {
        CFTypeRef first = CFArrayGetValueAtIndex(orig_langs, 0);
        if (!first || CFGetTypeID(first) != CFStringGetTypeID() ||
            (!CFEqual(first, CFSTR("zh-Hans-CN")) && !CFEqual(first, CFSTR("zh-Hans")))) {
            need_update = 1;
        }
    }
'''
    assert text.count(before) == 1
    text = text.replace(before, '    } /* Preserve the existing user-selected language order. */\n')
    before = 'CFPreferencesSetAppValue(CFSTR("AppleLocale"), CFSTR("zh_CN"), kCFPreferencesAnyApplication);'
    assert text.count(before) == 1
    text = text.replace(before, 'CFPreferencesSetAppValue(CFSTR("AppleLocale"),\n'
        '            CFDictionaryGetValue(dict, CFSTR("AppleLocale")), kCFPreferencesAnyApplication);')
    text = text.replace('has AppleLanguages with "zh-Hans-CN" prioritized.',
                        'initializes AppleLanguages only when absent or empty.')
    text = text.replace('sb26: AppleLanguages ensured zh-Hans-CN prioritized (updated)',
                        'sb26: initialized missing AppleLanguages; existing language choices preserved')
    before = '    if (strcmp(prog, "SpringBoard") != 0) return;'
    assert text.count(before) == 1
    text = text.replace(before, ('' if args.native_resources else '    language_install_loctable_compat();\n') + '''    if (!strcmp(prog, "Preferences")) {
        language_install_no_watch();
        return;
    }
    if (strcmp(prog, "SpringBoard") != 0) return;''')
    before = '__attribute__((constructor)) static void\ninstall_sb26(void)'
    assert text.count(before) == 1
    text = text.replace(before, '#include "language_watch_compat.inc"\n'
        'extern int language_install_loctable_compat(void);\n\n' + before)
    path.write_text(text)
    shutil.copy2(ROOT / 'tools/dada/language_watch_compat.inc', source)
    (out / 'language-compat.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), text.splitlines(True),
        fromfile='a/inject_sb26.c', tofile='b/inject_sb26.c')))
    commands = []
    def run(name, cmd):
        commands.append(dict(name=name, command=list(map(str, cmd))))
        with (out / (name + '.log')).open('w') as f:
            result = subprocess.run(list(map(str, cmd)), stdout=f, stderr=subprocess.STDOUT)
        if result.returncode:
            print((out / (name + '.log')).read_text()[-5000:])
            raise SystemExit(result.returncode)
        print('PASS', name, flush=True)
    prior = json.loads((ROOT / 'out/dada-native-glass-r1-20261111/sb26-compile.command.json').read_text())
    cmd = [str(source / Path(x).name) if x.startswith(str(BASE) + '/') else x for x in prior]
    library = out / 'libredwood-sb26.dylib'
    cmd[cmd.index('-o') + 1] = str(library)
    if not args.native_resources:
        cmd += ['-framework', 'Foundation', str(ROOT / 'tools/dada/language_loctable_compat.m')]
    run('compat-library', cmd)
    clang = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110/clang')
    flags = ['-target', 'arm64e-apple-ios26.0', '-isysroot',
        '/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk',
        '-O2', '-Wl,-adhoc_codesign']
    run('probe', [clang, *flags, ROOT / 'tools/dada/language_watch_probe.m',
        '-framework', 'Foundation', '-lobjc', '-o', out / 'language-probe'])
    run('localization-probe', [clang, *flags, ROOT / 'tools/dada/language_loctable_probe.m',
        ROOT / 'tools/dada/language_loctable_compat.m',
        '-framework', 'Foundation', '-lobjc', '-o', out / 'loctable-probe'])
    run('session-helper', [clang, *flags, ROOT / 'tools/dada/session_icon_library.c',
        '-o', out / 'session-library'])
    (out / 'build.json').write_text(json.dumps(dict(
        source_base=str(BASE), original_source_sha256=hashlib.sha256(original.encode()).hexdigest(),
        patched_source_sha256=hashlib.sha256(text.encode()).hexdigest(), commands=commands,
        files={p.name: inspect(p) for p in (library, out/'language-probe', out/'session-library')},
        gpu_driver_changed=False, touch_kernel_changed=False, system_changed=False,
        native_system_resources=args.native_resources, hardware_tested=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
