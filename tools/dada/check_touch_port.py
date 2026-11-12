#!/usr/bin/env python3
"""Check overlay application, reapplication and conflict refusal in temporary trees."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepared', type=Path, required=True)
    ap.add_argument('--apple-cache', type=Path, required=True)
    ap.add_argument('--port', type=Path, default=ROOT / 'xnu26/dada-touch')
    args = ap.parse_args()
    port = args.port
    meta = json.loads((port / 'manifest.json').read_text())
    objects = subprocess.check_output(['git', '-C', str(args.apple_cache),
                                      'rev-parse', '--git-path', 'objects'], text=True).strip()
    objects = (args.apple_cache / objects).resolve()
    with tempfile.TemporaryDirectory(prefix='dada-touch-apply-') as temporary:
        tree = Path(temporary)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        (tree / '.git/objects/info/alternates').write_text(str(objects) + '\n')
        subprocess.run(['git', '-C', str(tree), 'update-ref', 'HEAD', meta['apple_base']], check=True)
        for name in meta['integration_changes']:
            dest = tree / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((args.prepared / name).read_bytes())
        subprocess.run(['git', '-C', str(tree), 'apply', '-R',
                        str(port / 'integration.patch')], check=True)
        gpu = tree / 'gpu-unchanged.c'
        gpu.write_bytes(b'/* unrelated GPU source sentinel */\n')
        command = ['python3', str(ROOT / 'tools/dada/apply_touch_port.py'), '--tree', str(tree),
                   '--port', str(port)]
        subprocess.run(command, check=True)
        for name, entry in meta['integration_changes'].items():
            assert hashlib.sha256((tree / name).read_bytes()).hexdigest() == entry['after_sha256']
        snapshot = {str(p.relative_to(tree)): p.read_bytes() for p in tree.rglob('*')
                    if p.is_file() and '.git' not in p.parts}
        subprocess.run(command, check=True)
        assert snapshot == {str(p.relative_to(tree)): p.read_bytes() for p in tree.rglob('*')
                            if p.is_file() and '.git' not in p.parts}
        conflict = tree / 'bsd/dev/redwood_input.c'
        conflict.write_bytes(conflict.read_bytes() + b'/* unexpected edit */\n')
        before = conflict.read_bytes()
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert result.returncode != 0
        assert conflict.read_bytes() == before
        assert gpu.read_bytes() == b'/* unrelated GPU source sentinel */\n'
    print('Overlay apply, exact reapply, conflict refusal and unrelated-source preservation passed')


if __name__ == '__main__':
    main()
