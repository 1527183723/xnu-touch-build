#!/usr/bin/env python3
"""Build a read-only RenderBox surface observer over the native Glass root (WSL)."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'out/dada-native-glass-r1-20261111'


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, default=BASE / 'surface-observer-build-1')
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--upload-trace', action='store_true')
    ap.add_argument('--sdf-trace', action='store_true')
    args = ap.parse_args()
    out = args.output
    out.mkdir(exist_ok=False)
    source = ROOT / 'tools/dada/icon_surface_observer.m'
    library = out / 'libredwood-sb26.dylib'
    command = [
        '/home/LZY/warsaw-dada-native-metal-ios26-20261110/clang',
        '-target', 'arm64e-apple-ios26.0', '-isysroot',
        '/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk',
        '-dynamiclib', '-fobjc-arc', '-fblocks', '-O2', '-Wl,-adhoc_codesign',
        '-framework', 'Foundation', '-framework', 'UIKit', '-framework', 'QuartzCore',
        '-framework', 'IOSurface', str(source), '-o', str(library),
    ]
    if args.trace:
        command += ['-DDADA_ICON_GPU_TRACE=1', '-framework', 'Metal']
    if args.upload_trace:
        command += ['-DDADA_ICON_UPLOAD_TRACE=1', '-framework', 'Metal']
    if args.sdf_trace:
        command += ['-DDADA_ICON_SDF_TRACE=1']
    (out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'compile.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    helper = out / 'session-icon-library'
    helper_command = command[:5] + ['-O2', '-Wl,-adhoc_codesign',
        str(ROOT / 'tools/dada/session_icon_library.c'), '-o', str(helper)]
    with (out / 'helper-compile.log').open('w') as log:
        subprocess.run(helper_command, stdout=log, stderr=subprocess.STDOUT, check=True)
    (out / 'build.json').write_text(json.dumps({
        'observer_only': True,
        'gpu_input_metadata_trace': args.trace,
        'cpu_upload_trace': args.upload_trace,
        'cpu_sdf_producer_trace': args.sdf_trace,
        'base_library': '/redwood/lib/libredwood-sb26.dylib',
        'requires_native_glass_root': True,
        'rewrites_layers': False,
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'library': inspect(library),
        'helper': inspect(helper),
    }, indent=2) + '\n')
    print('PASS native Glass surface observer compiled; device output not yet checked.', flush=True)


if __name__ == '__main__':
    main()
