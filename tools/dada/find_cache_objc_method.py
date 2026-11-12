#!/usr/bin/env python3
"""Locate an Objective-C method in an exact shared-cache image, without patching it."""
import argparse
import json
from pathlib import Path
import struct
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/dsc'))
from cache import Cache
import macho

def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('image')
    ap.add_argument('selector', nargs='?', default='')
    ap.add_argument('--class-name', help='Limit results to this exact Objective-C class')
    ap.add_argument('--cache', default='/home/LZY/warsaw-ios26-20261110/build/ios26/23G90__iPhone18,3')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    c = Cache(args.cache)
    image = c.find_image(args.image)
    # dyld's large-cache method names use the shared selector base, not the
    # method entry address. The v2 optimization header records that offset.
    header = c.peek(0, 0x1d8, c.main_path)
    objc_opts = c.shared_region_start + struct.unpack_from('<Q', header, 0x1d0)[0]
    selector_base = c.shared_region_start + c.u64(objc_opts + 48)
    selector_bytes = c.u64(objc_opts + 56)
    def ptr(a): return c.decode(c.u64(a))
    def string(a): return c.read(a, 400).split(b'\0', 1)[0].decode('utf8', 'replace')
    records, errors = [], []
    def methods(list_at, owner, category=False):
        if not list_at: return
        if args.class_name and owner != args.class_name: return
        flags, count = struct.unpack('<II', c.read(list_at, 8))
        stride = flags & 0xfffc
        if count > 30000 or stride < 12: return
        for i in range(count):
            a = list_at + 8 + i * stride
            if flags & 0x80000000:
                no, to, io = struct.unpack('<iii', c.read(a, 12))
                name_at = selector_base + no if flags & 0x40000000 else ptr(a + no)
                implementation = a + 8 + io
            else:
                name_at = ptr(a)
                implementation = ptr(a + 16)
            name = string(name_at)
            if args.selector in name:
                records.append({'class': owner, 'selector': name,
                                'implementation': hex(implementation),
                                'offset': hex(implementation - image.address),
                                'category': category, 'flags': hex(flags)})
    for seg in macho.segments(c, image.address):
        for sec in seg.sections:
            if sec.name == '__objc_methlist':
                raw = c.read(sec.addr, sec.size)
                at = 0
                while at + 8 <= len(raw):
                    flags, count = struct.unpack_from('<II', raw, at)
                    stride = flags & 0xfffc
                    if (flags & 0x80000000 and stride == 12 and count and
                            at + 8 + count * stride <= len(raw)):
                        try:
                            methods(sec.addr + at, '<method-list>')
                        except Exception as exc:
                            errors.append({'method_list': hex(sec.addr + at), 'error': str(exc)})
                        at += 8 + count * stride
                    else:
                        at += 4
            if sec.name not in ('__objc_classlist', '__objc_catlist'): continue
            for at in range(sec.addr, sec.addr + sec.size, 8):
                try:
                    obj = ptr(at)
                    if sec.name == '__objc_catlist':
                        name = string(ptr(obj))
                        methods(ptr(obj + 16), name, True)
                        methods(ptr(obj + 24), '+' + name, True)
                    else:
                        for cls, prefix in ((obj, ''), (ptr(obj), '+')):
                            ro = ptr(cls + 32) & ~7
                            name = prefix + string(ptr(ro + 24))
                            methods(ptr(ro + 32), name)
                except Exception as exc:
                    errors.append({'entry': hex(at), 'error': str(exc)})
    # Optimized class metadata may point at merged lists outside this image.
    # Also recover exact relative entries from the local read-only segments.
    pool = c.read(selector_base, selector_bytes)
    needle = args.selector.encode() + b'\0'
    name_offset = pool.find(needle) if args.selector and not args.class_name else -1
    while name_offset >= 0:
        encoded = struct.pack('<i', name_offset)
        for seg in macho.segments(c, image.address):
            if seg.name != '__TEXT': continue
            raw = c.read(seg.vmaddr, seg.filesize)
            pos = raw.find(encoded)
            while pos >= 0:
                if pos % 4 == 0 and pos + 12 <= len(raw):
                    _no, _to, io = struct.unpack_from('<iii', raw, pos)
                    implementation = seg.vmaddr + pos + 8 + io
                    if seg.vmaddr <= implementation < seg.vmaddr + seg.filesize:
                        records.append({'class': '<relative-entry>', 'selector': args.selector,
                                        'entry': hex(seg.vmaddr + pos), 'implementation': hex(implementation),
                                        'offset': hex(implementation - image.address)})
                pos = raw.find(encoded, pos + 4)
        name_offset = pool.find(needle, name_offset + 1)
    result = {'image': image.path, 'base': hex(image.address), 'matches': records, 'errors': errors}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({**result, 'errors': errors[:5]}, indent=2))
    c.close()
if __name__ == '__main__': main()
