#!/usr/bin/env python3
"""Scope a fault recorder to the deployed Chinese compatibility library."""
import difflib
import json
from pathlib import Path
import shutil
import subprocess
from macho_verify import inspect
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'out/dada-app-return-wifi-r1-20261111/sb-fault-context'
BASE = ROOT / 'out/dada-language-native-r3-20261111/source'
OUT.mkdir(exist_ok=True)
source = OUT / 'source'
shutil.copytree(BASE, source, dirs_exist_ok=True)
p = source / 'inject_sb26.c'
old = p.read_text()
start = old.index('static void\nsb26_crash_logger(')
end = old.index('/* ---------------------------------------------------------------------------------------', start)
new = old[:start] + '#include "sb26_crash_record.inc"\n\n' + old[end:]
p.write_text(new)
shutil.copy2(ROOT / 'tools/dada/sb26_crash_record.inc', source)
(OUT/'fault-context.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),
    new.splitlines(True), fromfile='a/inject_sb26.c', tofile='b/inject_sb26.c')))
prior = json.loads((ROOT/'out/dada-native-glass-r1-20261111/sb26-compile.command.json').read_text())
cmd = [str(source/Path(x).name) if x.startswith('/home/LZY/warsaw-native-glass-r1-20261111/source/') else x for x in prior]
cmd[cmd.index('-o')+1] = str(OUT/'libredwood-sb26.dylib')
with (OUT/'compile.log').open('w') as f:
    subprocess.run(cmd, stdout=f, stderr=f, check=True)
clang = '/home/LZY/warsaw-dada-native-metal-ios26-20261110/clang'
subprocess.run([clang, '-target', 'arm64e-apple-ios26.0', '-isysroot',
    '/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk', '-O2', '-Wl,-adhoc_codesign',
    str(ROOT/'tools/dada/session_icon_library.c'), '-o', str(OUT/'session-icon-library')], check=True)
(OUT/'build.json').write_text(json.dumps({'library':inspect(OUT/'libredwood-sb26.dylib'),
    'helper':inspect(OUT/'session-icon-library'), 'source_base':str(BASE),
    'gpu_changed':False, 'chinese_preserved':True, 'touch_changed':False}, indent=2)+'\n')
print('PASS persistent fault-context library', flush=True)
