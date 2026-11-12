#!/usr/bin/env python3
"""Build correctly normalized, icon-sized GPU tests using the pinned public AIR fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = WORK / 'source/gpu/metal2vulkan/crates/metal2vulkan-validation/fixtures/public/examples/runtime_examples.ll'
    text = fixture.read_text()
    anchor = '%5 = fmul fast <2 x float> %4, splat (float 1.250000e-01)'
    assert text.count(anchor) == 1
    header, entries, commands = [], [], []
    for width in (16, 120, 180, 256, 360, 512):
        # LLVM's hexadecimal float spelling uses the exact float promoted to double.
        scale = struct.unpack('f', struct.pack('f', 1.0 / width))[0]
        literal = '0x' + struct.pack('>d', scale).hex().upper()
        source, air = out / f'texture-{width}.ll', out / f'texture-{width}.air'
        source.write_text(text.replace(anchor, anchor.replace('1.250000e-01', literal)))
        command = ['llvm-as-21', str(source), '-o', str(air)]
        subprocess.run(command, check=True)
        commands.append(command)
        data = air.read_bytes()
        name = f'air_{width}'
        header.append(f'static const unsigned char {name}[]={{' + ','.join(str(b) for b in data) + '};')
        entries.append(f'{{{width},{name},sizeof {name}}}')
    header.append('static const struct { unsigned width; const unsigned char *bytes; size_t size; } fixtures[]={' + ','.join(entries) + '};')
    (out / 'texture_size_fixtures.h').write_text('\n'.join(header) + '\n')
    baseline = json.loads((ROOT / 'out/dada-native-metal-ios26-r5-orientation/orientation-probe.command.json').read_text())
    command = [str(ROOT / 'tools/dada/native_metal_texture_sizes.m') if Path(x).name == 'native_metal_orientation.m' else x for x in baseline]
    command[command.index('-o') + 1] = str(out / 'native-metal-texture-sizes')
    command += ['-I', str(out)]
    commands.append(command)
    with (out / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    (out / 'build.json').write_text(json.dumps({'fixture': str(fixture),
        'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
        'normalization': 'fragment position divided by tested width; one fixture per size',
        'commands': commands}, indent=2) + '\n')
    print(out / 'native-metal-texture-sizes')


if __name__ == '__main__':
    main()
