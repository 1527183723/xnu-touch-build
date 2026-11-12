#!/usr/bin/env python3
"""Build isolated app-return diagnostics and the dada peach ath12k bridge."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from patch_app_return_exit import patch

ROOT = Path(__file__).resolve().parents[2]
BASE = Path('/home/LZY/warsaw-dada-native-xr-r2-20261111')
CACHE = Path('/home/LZY/dada-k90wifi-20261106/xnu-deps/build/ath12k')
WORK = Path('/home/LZY/warsaw-dada-app-return-wifi-r1-20261111')
OUT = ROOT / 'out/dada-app-return-wifi-r1-20261111'

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(name, cmd, **kwargs):
    save(OUT / (name + '.command.json'), [str(x) for x in cmd])
    with (OUT / (name + '.log')).open('w') as log:
        subprocess.run([str(x) for x in cmd], stdout=log, stderr=log, check=True, **kwargs)
    print('PASS', name, flush=True)

def prepare():
    WORK.mkdir(exist_ok=False)
    OUT.mkdir(exist_ok=False)
    for part in ('xnu12377', 'deps'):
        shutil.copytree(BASE / part, WORK / part, symlinks=True)
    for name in ('touch-port.json', 'experimental-build.json', 'support-build.json'):
        shutil.copy2(BASE / name, WORK / name)
    changed = ['bsd/dev/' + n for n in ('adreno8_bo.c', 'mhi_pcie.c', 'mhi_pcie_fw.c',
        'mhi_pcie_power.c', 'mhi_pcie_private.h')]
    changed += ['iokit/Kernel/' + n for n in ('RedwoodGPU.cpp', 'RedwoodGPUMemory.inc',
        'RedwoodGPUSubmit.inc', 'MiniIOMobileFramebuffer.cpp')]
    records = []
    for rel in changed:
        dest = WORK / 'xnu12377' / rel
        old, new = dest.read_text(), (ROOT / 'xnu/files' / rel).read_text()
        records.append({'path': rel, 'before': sha(dest), 'after': sha(ROOT / 'xnu/files' / rel)})
        (OUT / (Path(rel).name + '.patch')).write_text(''.join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True), fromfile='a/' + rel, tofile='b/' + rel)))
        dest.write_text(new)
    dest = WORK / 'xnu12377/bsd/kern/kern_exit.c'
    old = dest.read_text()
    new = patch(old)
    dest.write_text(new)
    (OUT / 'desktop-exits.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='a/bsd/kern/kern_exit.c', tofile='b/bsd/kern/kern_exit.c')))
    for path in (WORK / 'xnu12377/BUILD').rglob('*'):
        if path.is_symlink() or not path.is_file(): continue
        if 'Makefile' not in path.name and path.suffix not in ('.d', '.mk', '.json'): continue
        before = path.read_bytes()
        after = before.replace(str(BASE).encode(), str(WORK).encode())
        if before != after: path.write_bytes(after)
    names = {'adreno8_bo.o', 'mhi_pcie.o', 'mhi_pcie_fw.o', 'mhi_pcie_power.o',
        'kern_exit.o', 'RedwoodGPU.cpo', 'MiniIOMobileFramebuffer.cpo'}
    for path in (WORK / 'xnu12377/BUILD/obj').rglob('*'):
        if path.name in names and path.is_file():
            assert path.resolve().is_relative_to(WORK.resolve())
            path.unlink()
    save(OUT / 'source-changes.json', records)
    # Confirm the shared PHY table matches every stock dada DT triplet.
    donor = (ROOT / 'out/dada-wifi-port-r1-20261111/donor-hakios_dada_wifi.c').read_text()
    seq = re.search(r'static const uint32_t phy_sequence\[\]\[3\] = \{(.*?)\n\};', donor, re.S).group(1)
    triplets = [tuple(int(v.strip(), 0) for v in row.split(','))
        for row in re.findall(r'\{([^{}]+)\}', seq)]
    hdr = (ROOT / 'xnu/files/bsd/dev/mhi_pcie_phy_seq.h').read_text()
    pairs = [tuple(int(v.strip(), 0) for v in row.split(','))
        for row in re.findall(r'\{([^{}]+)\}', hdr.split('= {', 1)[1])]
    assert triplets[-2:] == [(0x400, 0, 0), (0x444, 3, 0)], triplets[-2:]
    assert [(a, b) for a, b, delay in triplets[:-2]] == pairs
    assert all(delay == 0 for a, b, delay in triplets)
    save(OUT / 'phy-comparison.json', {'dada_donor_triplets': len(triplets),
        'shared_writes': len(pairs), 'identical': True, 'start_reset_written_separately': True})
    print('PASS isolated kernel preparation and dada PHY comparison', flush=True)

def wifi():
    b = WORK / 'ath12k'
    if not b.exists():
        b.mkdir()
        for name in ('src', 'freebsd', 'gen'):
            shutil.copytree(CACHE / name, b / name, symlinks=True)
        machine = b / 'gen/machine'
        machine.unlink()
        machine.symlink_to(b / 'freebsd/sys/arm64/include')
        shutil.copytree(ROOT / 'wifi/lx', WORK / 'wifi-lx', symlinks=True)
        p = b / 'src/ath12k/wifi7/hal_qcc2072.c'
        text = p.read_text()
        assert '0x1e65304' in text
        p.write_text(text.replace('0x1e65304', '0x1e40304'))
    # Compile every driver object for plain arm64, matching this XNU build.
    script = (ROOT / 'tools/mk-ath12k.sh').read_text().split('# --- compile', 1)[1]
    script = script.replace('CC="clang -target', 'CC="clang-21 -target')
    script = script.replace('LD=$ROOT/xnu-deps/host/bin/ld64', 'LD=$DEPS/host/bin/ld64')
    script = script.replace('compile src/ath12k/$f.c $o $DFLAGS &',
        'compile src/ath12k/$f.c $o $DFLAGS &\n\t[ $(jobs -rp | wc -l) -lt 2 ] || wait')
    script = script.replace('compile "$f" $o $DFLAGS -I$W/src &',
        'compile "$f" $o $DFLAGS -I$W/src &\n\t[ $(jobs -rp | wc -l) -lt 2 ] || wait')
    script = script.replace('nm -u -j', 'llvm-nm-21 -u -j')
    script = script.replace('size out/ath12k.o', 'llvm-size-21 out/ath12k.o')
    prefix = '\n'.join(['#!/bin/bash', 'set -e', 'ROOT=' + str(ROOT),
        'B=' + str(b), 'W=' + str(WORK / 'wifi-lx'), 'DEPS=' + str(WORK / 'deps'),
        'S=$B/freebsd/sys', 'cd "$B"', ''])
    (b / 'build.sh').write_text(prefix + script)
    run('ath12k-build', ['bash', b / 'build.sh'])
    obj = b / 'out/ath12k.o'
    arch = subprocess.check_output(['llvm-objdump-21', '--macho', '--private-header', obj], text=True)
    assert 'ARM64E' not in arch and 'ARM64' in arch, arch
    save(OUT / 'ath12k-object.json', {'sha256': sha(obj), 'architecture': arch,
        'stubs': (b / 'out/stubs.txt').read_text().splitlines(), 'hardware_tested': False,
        'board': 'dada / peach 17cb:110e', 'source_cache': str(CACHE)})

def kernel():
    run('kernel-build', ['python3', ROOT / 'tools/dada/check_touch_kernel_linux.py',
        '--work', WORK, '--cache', '/home/LZY/hakios-arm64e-dada-kernel-20261031',
        '--attempt-kernel', '--verbose', '--external-object', WORK / 'ath12k/out/ath12k.o'])
    report = json.loads((WORK / 'linux-touch-compile.json').read_text())
    assert report['full_kernel_build_returncode'] == 0 and report['kernel_linked'], report
    variant = WORK / 'xnu12377/BUILD/obj/DEVELOPMENT_ARM64_BCM2837'
    undefined = subprocess.check_output(['llvm-nm-21', '--undefined-only', '--just-symbol-name',
        variant / 'mach.development.bcm2837.unstripped'], text=True)
    assert not undefined.strip(), undefined
    shutil.copy2(variant / 'mach.development.bcm2837', OUT / 'kernel-app-return-wifi.bin')
    shutil.copy2(WORK / 'linux-touch-compile.json', OUT / 'linux-touch-compile.json')
    print('PASS full kernel link, no undefined symbols', flush=True)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--kernel-only', action='store_true')
    args = ap.parse_args()
    if not args.resume: prepare()
    if not args.kernel_only: wifi()
    kernel()
