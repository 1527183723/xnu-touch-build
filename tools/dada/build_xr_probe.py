#!/usr/bin/env python3
"""Build native XR atlas probes with a pinned public AIR fixture (under WSL)."""
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
    ap.add_argument('--lifecycle', action='store_true')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = WORK / 'source/gpu/metal2vulkan/crates/metal2vulkan-validation/fixtures/public/examples/runtime_examples.ll'
    original = fixture.read_text()
    anchor = '%5 = fmul fast <2 x float> %4, splat (float 1.250000e-01)'
    assert original.count(anchor) == 1
    declarations, entries, commands = [], [], []
    for w, h in ((192, 192), (194, 194), (1024, 832)):
        constants = []
        for size in (w, h):
            value = struct.unpack('f', struct.pack('f', 1.0 / size))[0]
            constants.append('float 0x' + struct.pack('>d', value).hex().upper())
        replacement = '%5 = fmul fast <2 x float> %4, <' + ', '.join(constants) + '>'
        source, air = out / f'xr-{w}-{h}.ll', out / f'xr-{w}-{h}.air'
        source.write_text(original.replace(anchor, replacement))
        command = ['llvm-as-21', str(source), '-o', str(air)]
        subprocess.run(command, check=True)
        commands.append(command)
        data, name = air.read_bytes(), f'air_{w}_{h}'
        declarations.append(f'static const unsigned char {name}[]={{' + ','.join(map(str, data)) + '};')
        entries.append(f'{{{w},{h},{name},sizeof {name}}}')
    declarations.append('static const struct {unsigned w,h;const unsigned char *bytes;size_t size;} xr_fixtures[]={' + ','.join(entries) + '};')
    (out / 'xr_probe_fixtures.h').write_text('\n'.join(declarations) + '\n')
    command = json.loads((ROOT / 'out/dada-native-metal-ios26-r5-orientation/orientation-probe.command.json').read_text())
    source = ROOT / 'tools/dada' / ('native_metal_mirror_lifecycle.m' if args.lifecycle else 'native_metal_xr_probe.m')
    command = [str(source) if Path(x).name == 'native_metal_orientation.m' else x for x in command]
    command[command.index('-o') + 1] = str(out / 'native-metal-xr-probe')
    command += ['-I', str(out)]
    with (out / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    commands.append(command)
    (out / 'build.json').write_text(json.dumps({'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'fixture': str(fixture), 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(), 'commands': commands}, indent=2) + '\n')
    print(out / 'native-metal-xr-probe', flush=True)


if __name__ == '__main__':
    main()
