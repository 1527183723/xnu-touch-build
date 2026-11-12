#!/usr/bin/env python3
"""Apply the native dada overlay after the normal warsaw XNU26 patch sequence."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', type=Path, required=True)
    ap.add_argument('--port', type=Path, default=ROOT / 'xnu26/dada-touch')
    args = ap.parse_args()
    tree, port = args.tree, args.port
    meta = json.loads((port / 'manifest.json').read_text())
    base = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip()
    if base != meta['apple_base']:
        raise RuntimeError('The Apple XNU base differs from the checked touch port')
    changes = meta['integration_changes']
    already = all(sha(tree / name) == entry['after_sha256'] for name, entry in changes.items())
    for entry in meta['imported_files']:
        if sha(port / 'files' / entry['path']) != entry['port_sha256']:
            raise RuntimeError('Overlay changed: ' + entry['path'])
        if already and sha(tree / entry['path']) != entry['port_sha256']:
            raise RuntimeError('Installed driver differs: ' + entry['path'])
    if already:
        print(meta.get('name', 'dada native TouchCom') + ' overlay already applied; manifest matches')
        return
    for name, entry in changes.items():
        if sha(tree / name) != entry['before_sha256']:
            raise RuntimeError('Refusing a different or partially modified input file: ' + name)
    patch = port / 'integration.patch'
    if sha(patch) != meta['integration_patch_sha256']:
        raise RuntimeError('Integration patch differs from manifest')
    for entry in meta['imported_files']:
        if (tree / entry['path']).exists():
            raise FileExistsError('Refusing to overwrite ' + entry['path'])
    subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(tree), 'apply', str(patch)], check=True)
    for entry in meta['imported_files']:
        dest = tree / entry['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((port / 'files' / entry['path']).read_bytes().replace(b'\r\n', b'\n'))
    print('Applied ' + meta.get('name', 'dada native TouchCom') + ': ' + meta.get('boot_arguments', ''))


if __name__ == '__main__':
    main()
