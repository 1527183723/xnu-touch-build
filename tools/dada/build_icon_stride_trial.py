#!/usr/bin/env python3
"""Build a reversible icon experiment from the frozen, tested r5 source."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-native-metal-ios26-r5-orientation'
SOURCE = Path('/home/LZY/warsaw-dada-native-metal-ios26-r5-20261110/source')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--mode', choices=['stride', 'native'], default='stride')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(SOURCE, out / 'source')
    path = out / 'source/inject_sb26.c'
    before = path.read_text()
    if args.mode == 'stride':
        anchor = 'ctx = CGBitmapContextCreate(NULL, w, h, 8, w * 4, cs,'
        assert before.count(anchor) == 1
        after = before.replace(anchor,
            '/* Trial: align bitmap rows to the IOSurface/texture upload boundary. */\n'
            '    ctx = CGBitmapContextCreate(NULL, w, h, 8, (w * 4 + 63) & ~(size_t)63, cs,')
        change = 'bitmap row rounded up to 64 bytes; 120 pixels becomes 512 instead of 480'
    else:
        anchor = '    sb26_fill_all_icons();\n'
        assert before.count(anchor) == 2
        after = before.replace(anchor,
            '    /* Native Metal icons are already populated. Do not overwrite their\n'
            '     * layers with the old timed PNG bitmap fallback. */\n')
        change = 'preserve native icons: omit timed/notification PNG layer overwrites; keep wallpaper and hooks'
    path.write_text(after)
    (out / 'icon-trial.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile='r5/inject_sb26.c',
        tofile='trial/inject_sb26.c')))
    command = json.loads((BASE / 'sb26-compile.command.json').read_text())
    command = [str(out / 'source' / Path(x).name) if x.startswith(str(SOURCE) + '/') else x for x in command]
    command[command.index('-o') + 1] = str(out / 'libredwood-sb26.dylib')
    (out / 'compile.command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'compile.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    shutil.copy2(BASE / 'session-icon-library', out / 'session-icon-library')
    report = inspect(out / 'libredwood-sb26.dylib')
    report.update({'trial_only': True, 'hardware_tested': False,
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'base_source_sha256': hashlib.sha256(before.encode()).hexdigest(),
        'mode': args.mode, 'change': change,
        'kernel_changed': False, 'system_changed': False})
    (out / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(out / 'libredwood-sb26.dylib')


if __name__ == '__main__':
    main()
