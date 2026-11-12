#!/usr/bin/env python3
"""Capture running iOS graphics evidence without stopping the kernel or flashing."""
import argparse
import base64
import json
import re
import subprocess
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fastboot', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    serial = 'xnuloader'
    counter = 0

    def fb(*words):
        p = subprocess.run([str(args.fastboot), '-s', serial, *words],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
        text = p.stdout.decode('utf-8', 'replace').replace('\r', '')
        with (out / 'commands.txt').open('a', encoding='utf-8') as f:
            f.write(' '.join(words) + '\n' + text + '\n')
        if p.returncode:
            raise RuntimeError(text)
        return text

    def log():
        nonlocal counter
        counter += 1
        file = out / f'log-{counter:03d}.txt'
        fb('oem', 'log')
        fb('get_staged', str(file))
        return file.read_text(encoding='utf-8', errors='replace')

    def sh(command):
        reply = fb('oem', 'sh', command)
        seq = int(re.search(r'\(bootloader\)\s*(\d+)', reply).group(1))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.4)
            text = log()
            done = re.search(rf'^rwsh {seq} exit (\d+) ', text, re.M)
            if done:
                if int(done.group(1)):
                    raise RuntimeError('\n'.join(x for x in text.splitlines() if f'rwsh {seq}' in x))
                return seq, text
        raise TimeoutError('No completion for ' + command)

    def pull(path, dest):
        # rwsh cat uses base64 and a bounded chunk. Require exact offsets so a
        # lost log-ring segment cannot silently produce a corrupt evidence file.
        data = bytearray()
        total = None
        stalled = 0
        while total is None or len(data) < total:
            before = len(data)
            seq, text = sh(f'cat {path} {len(data)}')
            rows = re.findall(rf'^rwsh {seq}\| cat (\d+) ([A-Za-z0-9+/=]+)$', text, re.M)
            for raw_offset, encoded in rows:
                offset = int(raw_offset)
                if offset != len(data):
                    # Busy kernel logs can overwrite part of one response. Keep
                    # the verified prefix and request the missing offset again.
                    break
                data.extend(base64.b64decode(encoded, validate=True))
            end = re.search(rf'^rwsh {seq}\| cat end (\d+) next (\d+)$', text, re.M)
            if not end or not rows:
                raise RuntimeError('Incomplete cat output: ' + path)
            total, following = map(int, end.groups())
            if following < len(data) or len(data) > total:
                raise RuntimeError('Inconsistent cat size')
            stalled = stalled + 1 if before == len(data) else 0
            if stalled >= 4:
                raise RuntimeError('No readable progress: ' + path)
            dest.with_suffix(dest.suffix + '.partial').write_bytes(data)
            print(f'{path}: {len(data)}/{total}', flush=True)
        dest.write_bytes(data)
        dest.with_suffix(dest.suffix + '.partial').unlink()

    result = fb('getvar', 'result')
    if 'running' not in result:
        raise RuntimeError('A running kernel is required')
    fb('oem', 'screenshot')
    fb('get_staged', str(out / 'screen.raw'))
    from PIL import Image
    raw = (out / 'screen.raw').read_bytes()
    if len(raw) != 1200 * 2670 * 4:
        raise RuntimeError('Unexpected dada framebuffer geometry')
    im = Image.frombytes('RGBA', (1200, 2670), raw, 'raw', 'BGRA')
    im.save(out / 'screen.png')
    im.resize((400, 890)).save(out / 'screen-small.png')
    _, processes = sh('ps')
    (out / 'processes.txt').write_text('\n'.join(x for x in processes.splitlines() if 'rwsh ' in x) + '\n')
    sh('notify com.redwood.layers.full')
    # Notification completion only acknowledges posting; allow the main-queue
    # dump to finish before reading the file, then require its end marker.
    time.sleep(3)
    pull('/private/var/tmp/layers-dump.txt', out / 'layers.txt')
    if 'sb26 layers end lines=' not in (out / 'layers.txt').read_text(errors='replace'):
        raise RuntimeError('Layer dump is still incomplete')
    pull('/redwood/etc/glass.conf', out / 'glass.conf')
    (out / 'manifest.json').write_text(json.dumps({
        'kernel_running': True, 'width': 1200, 'height': 2670, 'stride': 4800,
        'flashed': False, 'stopped_or_rebooted': False,
        'graphics_or_process_configuration_changed': False,
        'layer_file_offsets_verified': True,
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
