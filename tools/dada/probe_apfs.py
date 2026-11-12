#!/usr/bin/env python3
"""Inspect an Apple compressed inode without changing any firmware files."""
import sys
import zlib
from pathlib import Path
from dissect.apfs import APFS
from extract_firmware import decoder

decoder(Path('/home/LZY/dada-ios26-k90auto-20261106/libzbitmap.so'))
with Path('/home/LZY/warsaw-ios26-20261110/ipsw/os.dmg').open('rb') as image:
    volume = APFS(image).volumes[0]
    node = volume.get('private/var/staged_system_apps/Books.app/Frameworks/BlissReader.framework/BlissReader')
    stream = node.open()
    print('node', node, 'size', node.size, 'header', stream._header)
    print('fork', stream._fork, 'entries', len(stream._entries), stream._entries[:3])
    for i, (offset, length) in enumerate(stream._entries):
        stream._fork.seek(offset)
        chunk = stream._fork.read(length)
        try:
            data = zlib.decompress(chunk) if chunk[0] == 0x78 else chunk[1:]
        except Exception as exc:
            print('bad chunk', i, 'offset', offset, 'length', length, 'got', len(chunk),
                  'head', chunk[:24].hex(), 'tail', chunk[-24:].hex(), 'error', str(exc))
            print('fork size', stream._fork.size)
            raise
        if i < 3:
            print('chunk', i, 'decoded', len(data))
