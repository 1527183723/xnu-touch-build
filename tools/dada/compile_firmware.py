#!/usr/bin/env python3
"""Build current warsaw userland fixes with native arm64e LLVM and a local SDK."""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--sdk', type=Path, required=True)
    ap.add_argument('--ld', type=Path, required=True)
    ap.add_argument('--ldid', type=Path, required=True)
    args = ap.parse_args()
    build = args.work / 'build'
    userland = build / 'userland'
    logs = args.work / 'compile-logs'
    logs.mkdir(exist_ok=True)
    commands = []
    cc = ['clang-21', '-target', 'arm64-apple-ios16.5', '--ld-path=' + str(args.ld),
          '-Os', '-static', '-nostdlib', '-ffreestanding', '-fno-stack-protector', '-fno-builtin']
    dynamic = ['clang-21', '-target', 'arm64e-apple-ios26.0', '--ld-path=' + str(args.ld),
               '-isysroot', str(args.sdk), '-miphoneos-version-min=26.0', '-O1']
    assert (args.sdk / 'System/Library/Frameworks/CoreFoundation.framework/Headers/CoreFoundation.h').is_file()

    def run(name, command):
        print('Compiling', name, flush=True)
        with (logs / f'{name}.log').open('wb') as log:
            result = subprocess.run(command, cwd=userland, stdout=log, stderr=log)
        if result.returncode:
            print((logs / f'{name}.log').read_text(errors='replace')[-5000:], flush=True)
            raise SystemExit(result.returncode)
        commands.append({'name': name, 'command': command})

    ldflags = ['-Wl,-e,_start', '-Wl,-pagezero_size,0x100000000']
    run('hello', cc + ldflags + ['-o', 'hello', 'src/hello.c'])
    run('mini', cc + ['-Ilibmini', '-c', 'libmini/mini.c', '-o', 'obj/mini.o'])
    run('start', cc + ['-c', 'libmini/start.S', '-o', 'obj/start.o'])
    for name in ('init', 'child', 'stage0', 'var_store'):
        run(name, cc + ['-Ilibmini'] + ldflags + ['-o', 'obj/' + name,
                                               'src/' + name + '.c', 'obj/mini.o', 'obj/start.o'])
    recipe = (userland / 'build.sh').read_text()
    section = recipe[recipe.index('# ordinary dynamically linked programs'):recipe.index('\t\tif [ -n "$GL" ]; then')]
    section = section.replace('\\\n', ' ')
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith('$DYN '):
            continue
        words = shlex.split(line[len('$DYN '):].split(' &&')[0])
        name = Path(words[words.index('-o') + 1]).name
        # iOS 26 uses tools/userland-driver, not the legacy 16.5 Metal implementation.
        if name in ('RedwoodMetal', 'libredwoodmetal.dylib'):
            continue
        run(name, dynamic + words)
    hid = ['-Os', '-framework', 'CoreFoundation', '-framework', 'IOKit',
           'src/input26_service.c', '-o', 'obj/input26-service']
    run('input26-service', dynamic + hid)
    run('libredwood-sb26.dylib', dynamic + [
        '-dynamiclib', '-install_name', '/redwood/lib/libredwood-sb26.dylib', '-Os',
        '-framework', 'CoreFoundation', '-framework', 'CoreGraphics', '-framework', 'Metal',
        '-lobjc', '-lMobileGestalt', 'src/inject_sb26.c', 'src/inject_sb26_layers.c',
        'src/inject_sb26_rate.c', 'src/inject_posterboard.c', '-o', 'obj/libredwood-sb26.dylib'])
    run('libredwood-sbmetal-experiment.dylib', dynamic + [
        '-dynamiclib', '-install_name', '/redwood/lib/libredwood-sbmetal-experiment.dylib',
        '-Os', '-fno-objc-arc', '-framework', 'Foundation', '-framework', 'Metal',
        '-framework', 'UIKit', '-framework', 'IOKit', '-lobjc',
        'src/sb_metal_experiment.m', '-o', 'obj/libredwood-sbmetal-experiment.dylib'])
    driver = ['-framework', 'Foundation', '-framework', 'IOKit', '-framework', 'Metal', '-lobjc',
              '../tools/userland-driver/redwoodmetal.m', '../tools/userland-driver/redwooddrawable.m']
    run('libredwoodmetal.dylib', dynamic + ['-dynamiclib', '-install_name',
        '/redwood/lib/libredwoodmetal.dylib'] + driver + ['-o', 'obj/libredwoodmetal.dylib'])
    run('RedwoodMetal', dynamic + ['-bundle'] + driver + ['-o', 'obj/RedwoodMetal'])
    # notify_* is re-exported by libSystem in this SDK, not a standalone libnotify.
    run('rwsh', dynamic + ['-O2', 'src/rwsh.c', '-o', 'obj/rwsh'])
    entitlement_files = {p.stem: p for p in (userland / 'src').glob('*.entitlements')}
    entitlement_files['input26-service'] = userland / 'src/input26_service.entitlements'
    for file in (userland / 'obj').iterdir():
        if not file.is_file() or file.read_bytes()[:4] != b'\xcf\xfa\xed\xfe' or file.suffix == '.o':
            continue
        command = [str(args.ldid), '-Cadhoc', '-S' + str(entitlement_files[file.name])
                   if file.name in entitlement_files else '-S', str(file)]
        subprocess.run(command, check=True)
        file.chmod(0o755)
    (userland / 'hello').chmod(0o755)
    # Make PREBUILT=1 staging use this run's newly built programs, not older copies.
    (build / 'prebuilt/userland').mkdir(parents=True, exist_ok=True)
    import shutil
    for file in (userland / 'obj').iterdir():
        if file.is_file() and file.suffix != '.o':
            shutil.copy2(file, build / 'prebuilt/userland' / file.name)
    shutil.copy2(userland / 'hello', build / 'prebuilt/userland/hello')
    record = {'built_from_warsaw_sources': True, 'dynamic_abi': 'arm64e PAC ABI v0',
              'driver_sources': 'tools/userland-driver', 'commands': commands,
              'recipe_sha256': hashlib.sha256(recipe.encode()).hexdigest()}
    (args.work / 'userland-compiled.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Current userland sources compiled and ad hoc signed.', flush=True)


if __name__ == '__main__':
    main()
