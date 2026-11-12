#!/usr/bin/env python3
"""Serial USB collection of retained GPU and process-exit records."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]

def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seconds', type=int, default=100)
    a = ap.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    fb = ROOT.parent / 'tools/platform-tools/fastboot.exe'
    events = []
    def call(*words):
        r = subprocess.run([str(fb), '-s', 'xnuloader', *map(str, words)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        text = r.stdout.decode('utf-8', 'replace')
        if r.returncode or 'FAILED' in text: raise RuntimeError(text)
        return text
    cycle = 0
    start = time.monotonic()
    while time.monotonic() - start < a.seconds:
        cycle += 1
        commands = ['sysctl kern.redwood.gpu_fault', 'sysctl kern.redwood.desktop_exits',
            'sysctl kern.redwood.frames', 'ps']
        for index, command in enumerate(commands):
            answer = call('oem', 'sh', command)
            match = re.search(r'\(bootloader\)\s*(\d+)', answer)
            if not match: raise RuntimeError(answer)
            seq = match.group(1)
            completed = False
            seen = set()
            text = ''
            for poll in range(8):
                time.sleep(.35)
                call('oem', 'log')
                path = a.output / f'{cycle:03d}-{index}-{poll}.txt'
                call('get_staged', path)
                text = path.read_bytes().decode('utf-8', 'replace')
                for line in text.splitlines():
                    if re.search(r'\brwsh ' + seq + r'(?:\|| )', line) and line not in seen:
                        seen.add(line)
                        print(line, flush=True)
                if re.search(r'\brwsh ' + seq + r' exit ', text):
                    completed = True
                    break
            events.append({'seconds': round(time.monotonic()-start, 2), 'command': command,
                'sequence': int(seq), 'completed': completed, 'lines': sorted(seen)})
            (a.output / 'events.json').write_text(json.dumps(events, indent=2) + '\n')
            if not completed: raise RuntimeError('Command did not finish: ' + command)
        print(f'Captured app-return sample {cycle}', flush=True)
        time.sleep(2)
    print('Capture finished; no stop/restart or flash sent', flush=True)

if __name__ == '__main__': main()
