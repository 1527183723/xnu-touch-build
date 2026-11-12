#!/usr/bin/env python3
"""Upload a System APFS to xnuloader RAM and retain bounded host-side live logs."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['start', 'observe'])
    ap.add_argument('--system', type=Path)
    ap.add_argument('--payload', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--window', default='240000000')
    ap.add_argument('--seconds', type=int, default=120)
    ap.add_argument('--fastboot', type=Path, default=ROOT.parent / 'tools/platform-tools/fastboot.exe')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    timeline = out / 'host-timeline.txt'

    def record(message):
        line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {message}'
        print(line, flush=True)
        with timeline.open('a', encoding='utf-8') as log:
            log.write(line + '\n')

    def fb(*words, timeout=20):
        try:
            result = subprocess.run([str(args.fastboot), '-s', 'xnuloader', *words],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            record('TIMEOUT: ' + ' '.join(words))
            raise
        text = result.stdout.decode('utf-8', 'replace').replace('\r', '')
        if result.returncode or 'FAILED' in text:
            record('FAILED: ' + ' '.join(words) + ': ' + text.strip())
            raise RuntimeError(text)
        return text

    def var(name):
        text = fb('getvar', name)
        found = re.search(r'(?:^|\n)(?:\(bootloader\)\s*)?' + re.escape(name) + r':\s*([^\n]*)', text)
        if not found:
            raise RuntimeError('Missing getvar ' + name + ': ' + text)
        return found[1].strip()

    def fetch_log(name):
        fb('oem', 'log')
        path = out / name
        fb('get_staged', str(path))
        return path

    assert var('product') == 'xnuloader'
    record('Connected; watchdog=' + var('watchdog'))
    if args.action == 'start':
        assert args.system and args.system.is_file() and args.payload and args.payload.is_file()
        total = args.system.stat().st_size
        window = int(args.window, 16)
        fb('oem', 'ramdisk', 'begin', format(total, 'x'), args.window, timeout=45)
        record('RAM allocation: ' + var('ramdisk-state'))
        got = 0
        block = out / 'system-upload-block.bin'
        started = time.monotonic()
        with args.system.open('rb') as source:
            while got < total:
                count = min(256 << 20, total - got)
                with block.open('wb') as dest:
                    left = count
                    while left:
                        data = source.read(min(left, 8 << 20))
                        if not data:
                            raise RuntimeError('System ended before its declared size')
                        dest.write(data)
                        left -= len(data)
                fb('stage', str(block), timeout=900)
                got += count
                state = var('ramdisk-state').split()
                expected_state = 'ready' if got == total else 'receiving'
                assert len(state) == 4 and state[0] == expected_state and [int(x, 16) for x in state[1:]] == [got, total, window], state
                record(f'System {got / (1 << 20):.1f}/{total / (1 << 20):.1f} MiB, '
                       f'{got / (1 << 20) / (time.monotonic() - started):.1f} MiB/s')
        block.unlink()
        fetch_log('before-kernel.txt')
        record('Staging matched kernel/root payload')
        fb('stage', str(args.payload), timeout=180)
        record('Starting kernel: ' + fb('oem', 'run', timeout=30).strip())
    started = time.monotonic()
    previous = b''
    index = 0
    while time.monotonic() - started < args.seconds:
        index += 1
        path = fetch_log(f'live-{index:03d}.txt')
        data = path.read_bytes()
        if data != previous:
            previous = data
            (out / 'latest.txt').write_bytes(data)
            record(f'Log {len(data):,} bytes; result={var("result")}')
        text = data.decode('utf-8', 'replace')
        returned = re.search(r'kernel returned: log at ([a-fA-F0-9]+), ([0-9]+) bytes', text)
        if returned:
            address, length = returned.groups()
            fb('oem', 'stage', address, format(int(length), 'x'))
            fb('get_staged', str(out / 'kernel-full.txt'))
            record('Kernel returned; saved full RAM log, leaving UEFI running')
            break
        time.sleep(5)
    (out / 'session.json').write_text(json.dumps({
        'action': args.action, 'system': str(args.system), 'payload': str(args.payload),
        'window_hex': args.window, 'no_flash_commands': True,
        'host_observation_seconds': round(time.monotonic() - started),
        'host_observation_does_not_stop_kernel': True}, indent=2) + '\n')
    record('Host observation ended; no stop/reboot command sent')


if __name__ == '__main__':
    main()
