#!/usr/bin/env python3
"""Fetch bounded SDF producer/upload evidence using serialized USB commands."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--probe', action='store_true')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = []

    def run(command):
        subprocess.run([str(x) for x in command], check=True)

    def sh(command, name, timeout=25):
        evidence = out / name
        run([sys.executable, ROOT / 'tools/dada/live_command.py', command,
             '--output', evidence, '--timeout', str(timeout)])
        text = (evidence / 'command-output.txt').read_text()
        commands.append({'command': command, 'evidence': str(evidence)})
        return text

    def fetch(remote, name):
        local = out / name
        run([sys.executable, ROOT / 'tools/dada/fetch_live_file.py', remote,
             '--output', local, '--evidence', out / (name + '-fetch')])
        return local

    sh('ps', 'processes')
    producer = fetch('/private/var/tmp/icon-sdf-producer.jsonl', 'producer.jsonl')
    rows = [json.loads(line) for line in producer.read_text().splitlines() if line.strip()]
    paths = set()
    # Four exact producer samples are sufficient for the first comparison;
    # leave the remaining bounded samples resident for adaptive follow-ups.
    for row in rows[:4]:
        if row.get('decoded'):
            paths.add(row['decoded'])
        for source in row.get('sources', []):
            if source.get('compressed'):
                paths.add(source['compressed'])
    for remote in sorted(paths):
        if not remote.startswith('/private/var/tmp/icon-sdf-') or '..' in remote:
            raise ValueError('Unexpected diagnostic file path')
        fetch(remote, Path(remote).name)
    origins = fetch('/private/var/tmp/icon-buffer-origins.jsonl', 'metal-origins.jsonl')
    rows = [json.loads(line) for line in origins.read_text().splitlines() if line.strip()]
    for row in rows[:4]:
        remote = row.get('origin', {}).get('raw')
        if remote and remote.startswith('/private/var/tmp/icon-origin-') and '..' not in remote:
            fetch(remote, Path(remote).name)
    if args.probe:
        sh('/redwood/bin/mirror-lifecycle', 'mirror-lifecycle', timeout=60)
    (out / 'manifest.json').write_text(json.dumps({
        'commands': commands, 'source_samples': len(paths),
        'no_reboot_stop_or_flash': True, 'probe_executed': args.probe,
        'gpu_driver_changed': False}, indent=2) + '\n')
    print('PASS diagnostic files collected; inspect results before claiming a fix.', flush=True)


if __name__ == '__main__':
    main()
