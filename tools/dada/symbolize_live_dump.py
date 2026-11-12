#!/usr/bin/env python3
"""Resolve live thread PCs against the exact local shared cache (Linux host)."""
import argparse
import bisect
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dsc'))
from cache import Cache
import macho


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('log', type=Path)
    ap.add_argument('--cache', type=Path, required=True)
    ap.add_argument('--pid', type=int, required=True)
    args = ap.parse_args()
    text = args.log.read_text(errors='replace')
    slide = int(re.findall(r'tl: td ' + str(args.pid) + r' slide (0x[0-9a-f]+)', text)[-1], 16)
    cache = Cache(str(args.cache))
    images = cache.images
    starts = [im.address for im in images]
    tables = {}
    print(f'PID {args.pid}; shared-cache slide {slide:#x}; cache UUID {cache.uuid}', flush=True)
    for tid, pcs in re.findall(r'tl: td ' + str(args.pid) + r' tid (\d+) bt\+? ([0-9a-f +]+)', text):
        print(f'thread {tid}', flush=True)
        for pc in pcs.split():
            if pc == '+':
                continue
            loaded = int(pc, 16)
            addr = loaded - slide
            i = bisect.bisect_right(starts, addr) - 1
            image = images[i] if i >= 0 else None
            if not image or not image.address <= addr < image.address + image.text_size:
                print(f'  {loaded:#x} outside cache executable images', flush=True)
                continue
            if image.path not in tables:
                exports = [(value, name) for name, kind, value, _ in macho.exports(cache, image)
                           if kind == 'regular' and image.address <= value < image.address + image.text_size]
                exports.sort()
                tables[image.path] = exports
            table = tables[image.path]
            j = bisect.bisect_right([t[0] for t in table], addr) - 1
            symbol = (f'nearest export (not necessarily containing function): {table[j][1]}+{addr - table[j][0]:#x}'
                      if j >= 0 else '(no preceding export)')
            print(f'  {loaded:#x} -> {addr:#x} {image.name}+{addr - image.address:#x} {symbol}', flush=True)
    cache.close()


if __name__ == '__main__':
    main()
