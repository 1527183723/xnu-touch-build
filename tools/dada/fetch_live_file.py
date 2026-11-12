#!/usr/bin/env python3
"""Copy one resident file through rwsh's outbox while iOS keeps running."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('remote')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--fastboot', type=Path, default=ROOT.parent / 'tools/platform-tools/fastboot.exe')
    args = ap.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    command = 'outbox ' + args.remote
    if len(command.encode()) > 119 or any(c.isspace() for c in args.remote):
        raise ValueError('rwsh needs a short path without whitespace')
    subprocess.run([sys.executable, str(ROOT / 'tools/dada/live_command.py'), command,
                    '--output', str(args.evidence), '--fastboot', str(args.fastboot),
                    '--timeout', '20'], check=True)
    result = json.loads((args.evidence / 'result.json').read_text())
    seq = result['sequence']
    text = (args.evidence / 'latest.txt').read_text(encoding='utf-8', errors='replace')
    start = text.index(f'rwsh {seq} start ')
    done = re.search(rf'^rwsh {seq} exit (\d+) ', text[start:], re.M)
    if not done or done[1] != '0':
        raise RuntimeError('outbox did not complete successfully')
    between = text[start:start + done.start()]
    matches = re.findall(r'outbox: (\d+) bytes for the host at (0x[0-9a-fA-F]+)', between)
    if len(matches) != 1:
        raise RuntimeError('Missing unambiguous outbox address in this command interval')
    size, address = int(matches[0][0]), int(matches[0][1], 16)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(args.output.name + '.partial')
    if partial.exists():
        raise FileExistsError(partial)
    commands = []
    for words in [('oem', 'stage', format(address, 'x'), format(size, 'x')),
                  ('get_staged', str(partial))]:
        process = subprocess.run([str(args.fastboot), '-s', 'xnuloader', *words],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        answer = process.stdout.decode('utf-8', 'replace')
        commands.append({'arguments': words, 'response': answer})
        (args.evidence / 'download-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        if process.returncode or 'FAILED' in answer:
            raise RuntimeError(answer)
    if partial.stat().st_size != size:
        raise RuntimeError('Outbox size mismatch')
    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    partial.rename(args.output)
    (args.evidence / 'file-result.json').write_text(json.dumps({'remote': args.remote,
        'local': str(args.output), 'bytes': size, 'sha256': digest,
        'running_kernel_stopped': False, 'partition_written': False}, indent=2) + '\n')
    print(f'Fetched {size} bytes from {args.remote}', flush=True)


if __name__ == '__main__':
    main()
