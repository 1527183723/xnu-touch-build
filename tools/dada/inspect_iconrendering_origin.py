#!/usr/bin/env python3
"""Read-only, exact-address disassembly of the measured IconRendering upload path."""
import argparse
import bisect
import json
from pathlib import Path
import sys
import struct

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools/dsc'), '/home/LZY/dada-diagnostics-python']
from cache import Cache
import macho
import disasm


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--cache', default='/home/LZY/warsaw-ios26-20261110/build/ios26/23G90__iPhone18,3')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--image', default='IconRendering')
    ap.add_argument('--offset', type=lambda s: int(s, 0), default=335160)
    ap.add_argument('--start', type=lambda s: int(s, 0))
    ap.add_argument('--end', type=lambda s: int(s, 0))
    ap.add_argument('--resolve-calls', action='store_true')
    args = ap.parse_args()
    args.output.mkdir(exist_ok=False)
    cache = Cache(args.cache)
    image = cache.find_image(args.image)
    exports = sorted((e[2], e[0]) for e in macho.exports(cache, image) if e[2])
    starts = sorted(macho.function_starts(cache, image))
    pc = image.address + args.offset
    index = bisect.bisect_right(starts, pc) - 1
    lo = starts[index]
    hi = starts[index + 1]
    if args.start is not None:
        lo = image.address + args.start
    if args.end is not None:
        hi = image.address + args.end
    symbols = dict(exports)
    md = disasm.capstone_md()
    if args.resolve_calls:
        for dependency in cache.images:
            if dependency == image:
                continue
            try:
                symbols.update((e[2], e[0]) for e in macho.exports(cache, dependency) if e[1] == 'regular')
            except Exception:
                pass
        print(f'Indexed {len(symbols)} exports', flush=True)
    def stub_note(target):
        if target in symbols:
            return symbols[target]
        try:
            stub_resolver = disasm.Resolver()
            value = None
            for si in md.disasm(cache.read(target, 24), target):
                hit = stub_resolver.target(si)
                if hit:
                    value = hit[1]
                    if si.mnemonic == 'ldr':
                        value = cache.decode(cache.u64(value))
                        if si.op_str.startswith('x1,'):
                            return 'objc:' + cache.read(value, 180).split(b'\0', 1)[0].decode('utf8', 'replace')
                if si.mnemonic in ('br', 'braa', 'braaz') and value:
                    return f'{value:#x} ' + symbols.get(value, '')
        except Exception:
            pass
        return ''
    resolver = disasm.Resolver()
    lines = []
    references = []
    for insn in md.disasm(cache.read(lo, hi - lo), lo):
        hit = resolver.target(insn)
        note = ''
        if not hit and insn.mnemonic in ('b', 'bl') and insn.op_str.startswith('#'):
            hit = ('call', int(insn.op_str[1:], 0))
        if hit:
            kind, target = hit
            note = f' ; {kind} {target:#x}'
            if kind == 'call':
                note += ' ' + stub_note(target)
            if target in symbols:
                note += ' ' + symbols[target]
            try:
                raw = cache.u64(target)
                decoded = cache.decode(raw)
                note += f' raw={raw:#x} decoded={decoded}'
                if decoded in symbols:
                    note += ' ' + symbols[decoded]
                if decoded:
                    text = disasm._printable(cache, decoded, 180)
                    if text:
                        note += ' ' + repr(text)
            except Exception:
                pass
            references.append({'pc': hex(insn.address), 'kind': kind, 'target': hex(target), 'note': note})
        lines.append(f'{insn.address:#x} +{insn.address-image.address:#x}: {insn.mnemonic:8} {insn.op_str}{note}')
    (args.output / 'disassembly.txt').write_text('\n'.join(lines) + '\n')
    (args.output / 'references.json').write_text(json.dumps(references, indent=2) + '\n')
    (args.output / 'exports.json').write_text(json.dumps(exports, indent=2) + '\n')
    classes = []
    def pointer(at):
        return cache.decode(cache.u64(at))
    def cstring(at):
        return cache.read(at, 256).split(b'\0', 1)[0].decode('utf8', 'replace')
    for segment in macho.segments(cache, image.address):
        for section in segment.sections:
            if section.name != '__objc_classlist':
                continue
            for at in range(section.addr, section.addr + section.size, 8):
                try:
                    klass = pointer(at)
                    ro = pointer(klass + 32) & ~7
                    name = cstring(pointer(ro + 24))
                    ivars = pointer(ro + 48)
                    rows = []
                    if ivars:
                        stride, count = struct.unpack('<II', cache.read(ivars, 8))
                        if stride < 32 or count > 200:
                            raise ValueError('bad ivar list')
                        for n in range(count):
                            entry = ivars + 8 + n * stride
                            offset_at = pointer(entry)
                            rows.append({'name': cstring(pointer(entry + 8)),
                                         'type': cstring(pointer(entry + 16)),
                                         'offset_at': hex(offset_at),
                                         'offset': cache.u32(offset_at)})
                    classes.append({'class': name, 'address': hex(klass), 'ro': hex(ro), 'ivars': rows})
                except Exception as exc:
                    classes.append({'entry': hex(at), 'error': str(exc)})
    (args.output / 'classes.json').write_text(json.dumps(classes, indent=2) + '\n')
    meta = {'image': image.path, 'base': hex(image.address), 'measured_pc': hex(pc),
            'cache_slide_version': cache.slide_version,
            'function_start': hex(lo), 'function_end': hex(hi), 'cache_uuid': cache.uuid}
    (args.output / 'metadata.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(json.dumps(meta, indent=2))
    print('\n'.join(lines[:20]))
    cache.close()


if __name__ == '__main__':
    main()
