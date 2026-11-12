#!/usr/bin/env python3
"""Run one resident rwsh command and preserve matching logs without stopping iOS."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('command')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--timeout', type=float, default=25)
    ap.add_argument('--fastboot', type=Path, default=ROOT.parent / 'tools/platform-tools/fastboot.exe')
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)

    def fb(*words):
        result = subprocess.run([str(args.fastboot), '-s', 'xnuloader', *words],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        text = result.stdout.decode('utf-8', 'replace')
        if result.returncode or 'FAILED' in text:
            raise RuntimeError(text)
        return text

    answer = fb('oem', 'sh', args.command)
    (out / 'command-response.txt').write_text(answer, encoding='utf-8')
    seq = re.search(r'\(bootloader\)\s*(\d+)', answer)
    if not seq:
        raise RuntimeError('Missing rwsh command sequence: ' + answer)
    seq = seq.group(1)
    started = time.monotonic()
    finished = False
    index = 0
    seen = set()
    lines = []
    while time.monotonic() - started < args.timeout:
        index += 1
        fb('oem', 'log')
        path = out / f'log-{index:03d}.txt'
        fb('get_staged', str(path))
        data = path.read_bytes()
        (out / 'latest.txt').write_bytes(data)
        for line in data.decode('utf-8', 'replace').splitlines():
            if re.search(r'\brwsh ' + seq + r'(?:\|| )', line) and line not in seen:
                seen.add(line)
                lines.append(line)
                print(line, flush=True)
                if re.search(r'\brwsh ' + seq + r' exit ', line):
                    finished = True
        if finished:
            break
        time.sleep(1)
    (out / 'command-output.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (out / 'result.json').write_text(json.dumps({
        'command': args.command, 'sequence': int(seq), 'rwsh_exit_seen': finished,
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'no_stop_reboot_flash': True}, indent=2) + '\n')
    if not finished:
        raise SystemExit('rwsh completion was not observed before the host timeout')


if __name__ == '__main__':
    main()
