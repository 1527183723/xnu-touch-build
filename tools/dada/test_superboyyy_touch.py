#!/usr/bin/env python3
"""ASAN/UBSAN check of the actual imported TouchCom C source on a Linux host."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    args = ap.parse_args()
    tests = ROOT / 'xnu26/dada-touch-superboyyy/tests'
    includes = args.work / 'test-include'
    for name in ('kern/clock.h', 'kern/thread.h', 'libkern/libkern.h', 'mach/mach_types.h',
                 'pexpert/pexpert.h', 'sys/redwood_input.h', 'sys/systm.h'):
        path = includes / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "host_kernel.h"\n')
    binary = args.work / 'test-superboyyy-touch'
    command = ['clang-21', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
               '-I', str(includes), '-I', str(tests), str(tests / 'test_touch.c'), '-o', str(binary)]
    # The production worker's DSB is an ARM instruction; leave it as a compiler
    # barrier on this host. Every other driver instruction is compiled unchanged.
    source = (tests / '../files/bsd/dev/hakios_dada_touch.c').resolve().read_text()
    source = source.replace('__asm__ volatile("dsb sy" ::: "memory");',
                            '__asm__ volatile("" ::: "memory");')
    (args.work / 'host-driver.c').write_text(source)
    harness = (tests / 'test_touch.c').read_text().replace(
        '#include "../files/bsd/dev/hakios_dada_touch.c"', '#include "host-driver.c"')
    (args.work / 'test-host.c').write_text(harness)
    command[command.index(str(tests / 'test_touch.c'))] = str(args.work / 'test-host.c')
    with (args.work / 'host-tests.log').open('wb') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
        subprocess.run([str(binary)], stdout=log, stderr=log, timeout=30, check=True)
    print((args.work / 'host-tests.log').read_text())


if __name__ == '__main__':
    main()
