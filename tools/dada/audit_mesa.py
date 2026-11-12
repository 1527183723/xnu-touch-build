#!/usr/bin/env python3
"""Read-only reverse dry runs report which Mesa rebase patches are present."""
import json
import subprocess
from pathlib import Path

work = Path('/home/LZY/warsaw-ios26-20261110')
source = work / 'air-rebuild/mesa-26.2.5'
reports = []
for patch in sorted((work / 'build/tools/mesa/rebase-26').glob('*.patch')):
    result = subprocess.run(['patch', '--dry-run', '--batch', '--force', '-R', '-p1', '-d', str(source)],
                            input=patch.read_bytes(), capture_output=True)
    reports.append({'patch': patch.name, 'fully_reverse_applicable': result.returncode == 0,
                    'output': (result.stdout + result.stderr).decode(errors='replace')})
    print(patch.name, 'present' if result.returncode == 0 else 'partial or not applicable', flush=True)
(work / 'mesa-patch-audit.json').write_text(json.dumps(reports, indent=2) + '\n')
devices = (source / 'src/freedreno/common/freedreno_devices.py').read_text()
shadow = (source / 'src/gallium/drivers/freedreno/freedreno_resource.c').read_text()
util = (source / 'src/util/meson.build').read_text()
assert 'GPUProps(has_early_preamble = False, has_fs_tex_prefetch = False)' in devices
start = shadow.index('fd_try_shadow_resource(')
guard = shadow[shadow.index('if (rsc->b.is_shared)', start):]
assert 'return false;' in guard[:700]
assert "host_machine.system() != 'darwin'" in util
notes = {
    '0003': 'Only src/util/meson.build context differs: later 0004-turnip-msm-darwin changes its Darwin sync-provider condition; OSMesa restoration is otherwise present.',
    '0004-a830': 'Later 0006 combines early-preamble and fragment-texture-prefetch switches in one GPUProps expression; both switches are false.',
    '0011': 'Later 0012 adds shadow diagnostics around the original is_shared guard; it still returns false before swapping imported storage.'}
(work / 'mesa-patch-overlap.json').write_text(json.dumps({'semantic_assertions_passed': True, 'overlap_explanations': notes}, indent=2) + '\n')
print('Three overlapping patch contexts checked in final source; guards are present.', flush=True)
