"""Validate thin Mach-O architecture, deployment target and CodeDirectory hashes."""
import hashlib
import pathlib
import struct

def inspect(path, require_arm64e=True, signature_required=True):
    path = pathlib.Path(path)
    data = path.read_bytes()
    magic, cpu, subtype, kind, count, command_bytes, flags, reserved = struct.unpack_from('<8I', data)
    assert magic == 0xfeedfacf and cpu == 0x100000c, (path, 'not thin ARM64 Mach-O')
    if require_arm64e:
        assert subtype == 0x80000002, (path, 'not arm64e PAC ABI v0', hex(subtype))
    cursor, signature, platform, minimum, imports = 32, None, None, None, []
    for _ in range(count):
        command, size = struct.unpack_from('<II', data, cursor)
        assert size >= 8 and cursor + size <= 32 + command_bytes
        if command == 0x32:
            platform, minimum, sdk = struct.unpack_from('<III', data, cursor + 8)
        elif command == 0x1d:
            signature = struct.unpack_from('<II', data, cursor + 8)
        elif command in (0xc, 0x80000018, 0x8000001f):
            offset = struct.unpack_from('<I', data, cursor + 8)[0]
            imports.append(data[cursor + offset:cursor + size].split(b'\0')[0].decode())
        cursor += size
    if require_arm64e:
        assert platform == 2 and minimum == 0x1a0000, (path, platform, minimum)
    directories = []
    if signature is not None:
        offset, length = signature
        assert offset + length <= len(data)
        sig = data[offset:offset + length]
        magic, total, entries = struct.unpack_from('>III', sig)
        assert magic == 0xfade0cc0 and total <= length
        for index in range(entries):
            slot, start = struct.unpack_from('>II', sig, 12 + 8 * index)
            blob = sig[start:]
            if struct.unpack_from('>I', blob)[0] != 0xfade0c02:
                continue
            length, version, codeflags, hashes, identifier, special, slots, limit = struct.unpack_from('>8I', blob, 4)
            hashsize, hashtype, cdplatform, pageshift = struct.unpack_from('>4B', blob, 36)
            assert hashtype in (1, 2, 3, 4) and 0 < pageshift < 20 and limit <= offset
            algorithm = {1: 'sha1', 2: 'sha256', 3: 'sha256', 4: 'sha384'}[hashtype]
            page = 1 << pageshift
            assert slots == (limit + page - 1) // page
            for index in range(slots):
                actual = hashlib.new(algorithm, data[index * page:min((index + 1) * page, limit)]).digest()[:hashsize]
                expected = blob[hashes + index * hashsize:hashes + (index + 1) * hashsize]
                assert actual == expected, (path, 'signature page hash', index)
            directories.append({'hash_type': hashtype, 'pages': slots, 'adhoc': bool(codeflags & 2)})
    if signature_required:
        assert directories and all(item['adhoc'] for item in directories), (path, 'missing adhoc signature')
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'cpu_subtype': hex(subtype), 'minimum_ios': minimum, 'imports': imports,
            'code_directories': directories, 'hardware_tested': False}
