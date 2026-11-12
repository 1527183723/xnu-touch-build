#!/usr/bin/env python3
"""Run the packaged Metal readback probe and retain real phone evidence over RAM USB."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--fastboot', type=Path, default=ROOT.parent / 'tools/platform-tools/fastboot.exe')
    ap.add_argument('--probe', type=Path, help='Upload a diagnostic executable to RAM-backed /private/var/tmp')
    ap.add_argument('--air', type=Path, help='AIR fixture for the orientation probe')
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)

    def fb(*words):
        command = [str(args.fastboot), '-s', 'xnuloader', *words]
        p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        text = p.stdout.decode('utf-8', 'replace').replace('\r', '')
        with (out / 'commands.txt').open('a', encoding='utf-8') as stream:
            stream.write(' '.join(words) + '\n' + text + '\n')
        if p.returncode:
            raise RuntimeError(text)
        return text

    state = fb('getvar', 'result')
    if 'running' not in state:
        raise RuntimeError('Kernel is not running: ' + state)
    index = 0

    def sh(command):
        nonlocal index
        reply = fb('oem', 'sh', command)
        seq = int(re.search(r'\(bootloader\)\s*(\d+)', reply).group(1))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            index += 1
            fb('oem', 'log')
            log = out / f'log-{index:03d}.txt'
            fb('get_staged', str(log))
            text = log.read_text(encoding='utf-8', errors='replace')
            (out / 'latest.txt').write_text(text, encoding='utf-8')
            rows = [line for line in text.splitlines() if f'rwsh {seq}' in line]
            done = re.search(rf'^rwsh {seq} exit (\d+) ', text, re.M)
            if done:
                print('\n'.join(rows), flush=True)
                return seq, done, rows
            time.sleep(1)
        raise TimeoutError(command)

    command = '/redwood/bin/native-metal-probe'
    if args.probe:
        for source, dest in [(args.probe, '/private/var/tmp/metal-probe'),
                             *([(args.air, '/private/var/tmp/metal-probe.air')] if args.air else [])]:
            fb('oem', 'push')
            fb('stage', str(source))
            _, done, _ = sh('pull ' + dest)
            if int(done.group(1)):
                raise RuntimeError('RAM upload failed: ' + dest)
        command = '/private/var/tmp/metal-probe' + (' /private/var/tmp/metal-probe.air' if args.air else '')
    seq, done, rows = sh(command)
    status = int(done.group(1)) if done else None
    report = {'sequence': seq, 'exit_status': status, 'probe_lines': rows,
              'metal_clear_readback_passed': status == 0 and any('PASS clear/readback' in line for line in rows),
              'metal_orientation_passed': status == 0 and any('orientation: PASS failures=0' in line for line in rows),
              'shader_execution_or_desktop_performance_proven': False,
              'no_flash_or_reboot_commands': True}
    fb('oem', 'screenshot')
    frame = out / 'screen.raw'
    fb('get_staged', str(frame))
    if frame.stat().st_size != 1200 * 2670 * 4:
        raise RuntimeError('Framebuffer size differs from the dada profile')
    from PIL import Image
    im = Image.frombytes('RGBA', (1200, 2670), frame.read_bytes(), 'raw', 'BGRA')
    im.save(out / 'screen.png')
    im.resize((400, 890)).save(out / 'screen-small.png')
    (out / 'device-result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if not (report['metal_orientation_passed'] if args.air else report['metal_clear_readback_passed']):
        raise SystemExit('Metal probe did not pass; device evidence retained')


if __name__ == '__main__':
    main()
