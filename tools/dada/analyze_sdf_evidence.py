#!/usr/bin/env python3
"""Compare exact CPU SDF decode outputs with their subsequent Metal inputs."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct


def digest(data):
    return hashlib.sha256(data).hexdigest()


def describe(data):
    chunks = []
    for off in range(0, len(data), 16384):
        part = data[off:off + 16384]
        count = Counter(part)
        chunks.append({'offset': off, 'bytes': len(part), 'zero_bytes': count[0],
            'entropy': round(-sum(n / len(part) * math.log2(n / len(part)) for n in count.values()), 4)})
    return {'bytes': len(data), 'sha256': digest(data), 'blocks16k': chunks}


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('directory', type=Path)
    args = ap.parse_args()
    directory = args.directory
    producer = [json.loads(s) for s in (directory / 'producer.jsonl').read_text().splitlines() if s]
    origins = [json.loads(s) for s in (directory / 'metal-origins.jsonl').read_text().splitlines() if s]
    records = []
    for row in producer:
        if not row.get('decoded'):
            continue
        path = directory / Path(row['decoded']).name
        if not path.is_file():
            continue
        decoded = path.read_bytes()
        item = {'producer': row, 'decoded': describe(decoded), 'metal_inputs': []}
        for origin in origins:
            source = origin.get('origin', {})
            if source.get('pointer') != row.get('pointer'):
                continue
            local = directory / Path(source.get('raw', 'missing')).name
            if not local.is_file():
                continue
            data = local.read_bytes()
            item['metal_inputs'].append({'path': str(local), **describe(data),
                'matches_cpu_decode': data == decoded})
        from PIL import Image
        w, h, stride = 194, 194, 784
        if len(decoded) == stride * h:
            image = Image.new('RGB', (w, h))
            pixels = []
            for y in range(h):
                for x in range(w):
                    word = struct.unpack_from('<I', decoded, y * stride + x * 4)[0]
                    pixels.append(tuple(round(((word >> shift) & 1023) * 255 / 1023)
                                        for shift in (0, 10, 20)))
            image.putdata(pixels)
            image.resize((388, 388)).save(path.with_suffix('.rgb10.png'))
        records.append(item)
    report = {'root_cause_confirmed': False,
              'note': 'Matching CPU bytes do not establish whether the SDF decoder or a later GPU stage is correct.',
              'samples': records}
    (directory / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'decoded_samples': len(records),
        'matching_metal_inputs': sum(v['matches_cpu_decode'] for r in records for v in r['metal_inputs']),
        'mismatching_metal_inputs': sum(not v['matches_cpu_decode'] for r in records for v in r['metal_inputs'])}, indent=2))


if __name__ == '__main__':
    main()
