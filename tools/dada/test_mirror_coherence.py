#!/usr/bin/env python3
"""Upload and run a private Metal probe without restarting iOS processes."""
import argparse
import atexit
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--probe', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--xr', action='store_true')
    ap.add_argument('--library-present', action='store_true')
    ap.add_argument('--quiet-appprotection', action='store_true')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    fb = ROOT.parent / 'tools/platform-tools/fastboot.exe'

    def run(cmd, name, timeout=60):
        cmd = list(map(str, cmd))
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        text = r.stdout.decode('utf-8', 'replace')
        (out / (name + '.txt')).write_text(text, encoding='utf-8')
        commands.append(cmd)
        if r.returncode or 'FAILED (remote' in text:
            raise RuntimeError(text)

    def sh(cmd, name, timeout=15):
        run([sys.executable, ROOT / 'tools/dada/live_command.py', cmd,
             '--output', out / name, '--timeout', timeout], name + '-host', timeout + 45)
        return (out / name / 'command-output.txt').read_text(encoding='utf-8')

    if args.quiet_appprotection:
        processes = sh('ps', 'processes')
        row = re.search(r'\|\s+(\d+)\s+\d+\s+\d+\s+[\d.]+\s+[\d.]+\s+\d+\s+appprotectiond', processes)
        if not row:
            raise RuntimeError('No confirmed appprotectiond PID; refusing to pause an unknown process')
        pid = row.group(1)
        # Only a short standalone GPU test runs while noisy logging is quiet.
        # No desktop restart, launchd changes, or persistent service disable.
        def resume():
            sh('kill 19 ' + pid, 'resume-appprotection', 8)
        atexit.register(resume)
        sh('kill 17 ' + pid, 'quiet-appprotection', 8)

    for source, remote, name in ((args.build / 'RedwoodMetal', '/private/var/tmp/mirror-metal', 'library'),
                                 (args.probe, '/private/var/tmp/mirror-check', 'probe')):
        if name == 'library' and args.library_present:
            continue
        data = source.read_bytes()
        # The running kernel's inbox is bounded; rwsh supports explicit offsets.
        for offset in range(0, len(data), 2 << 20):
            part = out / (name + '-part.bin')
            part.write_bytes(data[offset:offset + (2 << 20)])
            label = name + '-' + str(offset)
            run([fb, '-s', 'xnuloader', 'oem', 'push'], label + '-push')
            run([fb, '-s', 'xnuloader', 'stage', part], label + '-stage')
            sh('pull ' + remote + (' ' + str(offset) if offset else ''), label + '-pull')
    sh('chmod 755 /private/var/tmp/mirror-check', 'chmod')
    baseline = sh('/private/var/tmp/mirror-check', 'baseline', 60)
    fixed = sh('/private/var/tmp/mirror-check /private/var/tmp/mirror-metal', 'fixed', 60)
    results = {}
    for name, text in [('baseline', baseline), ('fixed', fixed)]:
        match = re.search(r'(?:xr-probe|mirror-lifecycle): (PASS|FAIL) checks=(\d+) failures=(\d+)', text)
        results[name] = {'completion_seen': bool(match),
                         'result': match.group(1) if match else None,
                         'checks': int(match.group(2)) if match else None,
                         'failures': int(match.group(3)) if match else None}
    results.update(commands=commands, desktop_driver_replaced=False, rebooted=False)
    (out / 'result.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({k:v for k,v in results.items() if k != 'commands'}, indent=2))
    if results['fixed']['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
