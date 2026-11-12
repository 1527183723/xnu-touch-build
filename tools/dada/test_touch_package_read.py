#!/usr/bin/env python3
"""Run the supplied seven SPI/bitbang tests against the integrated driver."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    args = ap.parse_args()
    original = ROOT / 'out/dada-touch-package-review-20261110/source/tools/test-dada-touch-read.py'
    code = original.read_text()
    needle = "src = (repo / 'base/xnu/files/bsd/dev/hakios_dada_touch.c').read_text()"
    assert code.count(needle) == 1
    code = code.replace(needle, 'src = Path(' + repr(str(
        ROOT / 'xnu26/dada-touch-superboyyy/files/bsd/dev/hakios_dada_touch.c')) + ').read_text()')
    # Cancellation is separately tested in test_superboyyy_touch.py. The supplied
    # wire fixtures exercise the same code with cancellation inactive.
    code = code.replace('static uint32_t pending_payload;',
                        'static uint32_t pending_payload;\nstatic bool stop_requested(void) {return false;}')
    runner = args.work / 'supplied-read-tests-integrated.py'
    runner.write_text(code)
    with (args.work / 'supplied-read-tests.log').open('wb') as log:
        subprocess.run(['python3', str(runner)], stdout=log, stderr=log, check=True)
    print((args.work / 'supplied-read-tests.log').read_text())


if __name__ == '__main__':
    main()
