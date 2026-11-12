#!/usr/bin/env python3
"""Build the package's HID contact-state fix using Warsaw's checked iOS26 ABI."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--sdk', type=Path, default=Path('/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk'))
    ap.add_argument('--ld', type=Path, default=Path('/home/LZY/hakios-full-rebuild-20261031/deps/theos/toolchain/linux/iphone/bin/ld'))
    ap.add_argument('--ldid', type=Path, default=Path('/home/LZY/dada-gpu-build-r1/theos/toolchain/linux/iphone/bin/ldid'))
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    source = out / 'source'
    source.mkdir(exist_ok=True)
    for name in ('input26_service.c', 'input26_service.entitlements', 'redwood_input.h'):
        (source / name).write_bytes((ROOT / 'userland/src' / name).read_bytes().replace(b'\r\n', b'\n'))
    binary = out / 'input26-service'
    command = ['clang-21', '-target', 'arm64e-apple-ios26.0', '--ld-path=' + str(args.ld),
               '-isysroot', str(args.sdk), '-miphoneos-version-min=26.0', '-Os', '-fblocks',
               '-Wall', '-Wextra', '-Werror', '-framework', 'CoreFoundation', '-framework', 'IOKit',
               str(source / 'input26_service.c'), '-o', str(binary), '-Wl,-adhoc_codesign']
    with (out / 'input26-build.log').open('wb') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
        subprocess.run([str(args.ldid), '-S' + str(source / 'input26_service.entitlements'),
                        '-Cadhoc', str(binary)], stdout=log, stderr=log, check=True)
    report = {'build_command': command, 'sdk': str(args.sdk), 'macho': inspect(binary),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'source_sha256': hashlib.sha256((source / 'input26_service.c').read_bytes()).hexdigest(),
              'hid_abi': 'Warsaw 23G90 standalone service; one input-queue reader',
              'source_reference': 'user repair package 20261110 inject26.c contact state',
              'kernel_or_system_packaged': False, 'hardware_tested': False}
    (out / 'input26-build.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Built and signed', binary)


if __name__ == '__main__':
    main()
