#!/usr/bin/env python3
"""Resolve a few exact shared-cache addresses without indexing all exports."""
import argparse
import bisect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/dsc'))
from cache import Cache
import macho


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('addresses', nargs='+', type=lambda x: int(x, 0))
    ap.add_argument('--cache', default='/home/LZY/warsaw-ios26-20261110/build/ios26/23G90__iPhone18,3')
    args = ap.parse_args()
    c = Cache(args.cache)
    result = []
    pending = set(args.addresses)
    for image in c.images:
        hits = [a for a in pending if any(s.name == '__TEXT' and s.vmaddr <= a < s.vmaddr + s.vmsize
                for s in macho.segments(c, image.address))]
        if not hits:
            continue
        exports = sorted((e[2], e[0]) for e in macho.exports(c, image) if e[1] == 'regular')
        values = [e[0] for e in exports]
        for a in hits:
            i = bisect.bisect_right(values, a) - 1
            sym = exports[i] if i >= 0 else None
            result.append({'address': hex(a), 'image': image.path,
                           'offset': hex(a - image.address),
                           'nearest_export': sym[1] if sym else None,
                           'export_displacement': hex(a - sym[0]) if sym else None})
            pending.remove(a)
        if not pending:
            break
    print(json.dumps({'matches': result, 'unresolved': [hex(a) for a in pending]}, indent=2))
    c.close()


if __name__ == '__main__':
    main()
