#!/usr/bin/env python3
"""Read network and crash state during a short pause of a verified noisy process."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []

    def command(text, label, timeout=8):
        out = args.output / label
        result = subprocess.run([sys.executable, str(ROOT / 'tools/dada/live_command.py'),
            text, '--output', str(out), '--timeout', str(timeout)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout + 45)
        host = result.stdout.decode('utf-8', 'replace')
        print(host, end='', flush=True)
        (args.output / (label + '-host.txt')).write_text(host, encoding='utf-8')
        records.append({'command': text, 'label': label, 'host_exit': result.returncode})
        path = out / 'command-output.txt'
        return path.read_text(encoding='utf-8') if path.exists() else host

    processes = command('ps', 'processes-before')
    match = re.search(r'\|\s+(\d+)\s+\d+\s+\d+\s+[\d.]+\s+[\d.]+\s+\d+\s+appprotectiond', processes)
    if not match:
        raise RuntimeError('No verified noisy service PID; no process paused')
    pid = match.group(1)
    resumed = False
    try:
        command('kill 17 ' + pid, 'pause-noise')
        command('/redwood/bin/net_test show', 'interfaces')
        command('sysctl kern.redwood.desktop_exits', 'desktop-exits')
        command('sysctl kern.redwood.gpu_fault', 'gpu-fault')
        command('ls /private/var/tmp', 'crash-files')
        command('ps', 'processes-paused')
    finally:
        result = command('kill 19 ' + pid, 'resume-noise')
        resumed = bool(re.search(r'rwsh \d+ exit 0 ', result))
        command('ps', 'processes-after')
        (args.output / 'capture.json').write_text(json.dumps({
            'records': records, 'noise_pid_verified': int(pid),
            'noise_resume_completed': resumed, 'network_settings_changed': False,
            'reboot_stop_flash_commands': False,
        }, indent=2) + '\n', encoding='utf-8')
        print('Capture ended; noisy service resume completed=' + str(resumed), flush=True)


if __name__ == '__main__':
    main()
