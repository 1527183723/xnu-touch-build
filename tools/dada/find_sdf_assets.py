#!/usr/bin/env python3
"""Read-only scan of local asset catalogs for the measured chunked SDF header."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from collections import Counter


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--root', type=Path, default=Path('/home/LZY/warsaw-ios26-20261110/build/ios26/rootfs'))
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(exist_ok=False)
    rows = []
    sizes = Counter()
    for path in args.root.rglob('*.car'):
        if not path.is_file():
            continue
        data = path.read_bytes()
        offset = 0
        while (offset := data.find(b'MLEC', offset)) >= 0:
            if offset + 16 > len(data):
                break
            _, flags, compression, count = struct.unpack_from('<4I', data, offset)
            if not(flags & 1) or compression != 4 or count != 4:
                offset += 4
                continue
            cursor, chunks = offset + 16, []
            for i in range(4):
                if cursor + 20 > len(data):
                    break
                signature, a, b, unpacked, stored = struct.unpack_from('<5I', data, cursor)
                if signature not in (0x4342434b, 0x4b434243):
                    break
                if signature == 0x4b434243:
                    a,b,unpacked,stored = struct.unpack('>4I', data[cursor+4:cursor+20])
                if cursor + 20 + stored > len(data) or stored > (2 << 20):
                    break
                # KCBC word 3 is a row count. The LZFSE stream carries its own
                # actual uncompressed byte count after the bvx1/2/- signature.
                payload = data[cursor + 20:cursor + 20 + stored]
                decoded = struct.unpack_from('<I', payload, 4)[0] if payload[:4] in (b'bvx2', b'bvx1', b'bvx-') and len(payload) >= 8 else None
                chunks.append({'offset':cursor-offset, 'a':a, 'b':b,
                    'rows':unpacked, 'uncompressed':decoded, 'compressed':stored})
                cursor += 20 + stored
            total = sum(c['uncompressed'] or 0 for c in chunks)
            sizes[total] += 1
            if len(chunks) == 4 and total == 152096:
                raw = data[offset:cursor]
                dest = args.output / f'asset-{len(rows):03d}.raw'
                dest.write_bytes(raw)
                row = {'catalog':str(path), 'catalog_offset':offset, 'bytes':len(raw), 'flags':flags,
                    'sha256':hashlib.sha256(raw).hexdigest(), 'file':str(dest), 'chunks':chunks}
                rows.append(row)
                print(json.dumps(row), flush=True)
            offset += 16
    (args.output/'assets.json').write_text(json.dumps(rows, indent=2)+'\n')
    (args.output/'sizes.json').write_text(json.dumps(sizes, indent=2)+'\n')
    print(f'Found {len(rows)} matching local SDF payloads', flush=True)


if __name__ == '__main__':
    main()
