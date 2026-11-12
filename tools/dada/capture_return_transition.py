#!/usr/bin/env python3
"""Bounded serial USB capture, with automatic resumption of a noisy service."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]

def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seconds', type=int, default=80)
    ap.add_argument('--noise-pid', type=int, default=68)
    a = ap.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    fb = ROOT.parent / 'tools/platform-tools/fastboot.exe'
    events = []
    def call(*args):
        p = subprocess.run([str(fb), '-s', 'xnuloader', *map(str, args)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12)
        answer = p.stdout.decode('utf-8', 'replace')
        if p.returncode or 'FAILED' in answer:
            raise RuntimeError(answer)
        return answer
    def cmd(s):
        answer = call('oem', 'sh', s)
        events.append({'time': time.time(), 'command': s, 'answer': answer})
        time.sleep(.4)
    def log(name):
        call('oem', 'log')
        call('get_staged', a.output / name)
    resumed = False
    try:
        cmd(f'kill 17 {a.noise_pid}')
        cmd('ps')
        log('before.txt')
        print('READY: bounded capture active; noise service temporarily paused', flush=True)
        start = time.monotonic()
        i = 0
        while time.monotonic() - start < a.seconds:
            i += 1
            cmd('ps' if i % 3 else 'dump backboardd')
            log(f'live-{i:03d}.txt')
            print(f'captured {i}, elapsed {time.monotonic()-start:.1f}s', flush=True)
            time.sleep(2)
    finally:
        try:
            cmd(f'kill 19 {a.noise_pid}')
            resumed = True
            cmd('ps')
            log('after-resume.txt')
        finally:
            (a.output / 'capture.json').write_text(json.dumps({
                'events': events, 'noise_service_resumed_command_accepted': resumed,
                'no_reboot_or_flash': True}, indent=2) + '\n')
            print(f'capture ended; resume command accepted={resumed}', flush=True)

if __name__ == '__main__':
    main()
