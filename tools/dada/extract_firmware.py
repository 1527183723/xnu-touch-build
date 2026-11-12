#!/usr/bin/env python3
"""Extract the documented iOS layout with native APFS metadata and ZBM decoding."""
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path, PurePosixPath

from dissect.apfs import APFS
from dissect.apfs.stream import FileStream
from dissect.util.compression import lzbitmap


def decoder(library_path):
    library = ctypes.CDLL(str(library_path))
    decode = library.zbm_decompress
    decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    decode.restype = ctypes.c_int

    def decompress(data):
        data = data.read() if hasattr(data, 'read') else bytes(data)
        length = ctypes.c_size_t()
        if decode(None, 0, data, len(data), ctypes.byref(length)) or length.value > 64 << 20:
            raise ValueError('Invalid ZBM stream size')
        output = ctypes.create_string_buffer(length.value)
        if decode(output, len(output), data, len(data), ctypes.byref(length)):
            raise ValueError('ZBM decompression failed')
        return output.raw[:length.value]

    lzbitmap.decompress = decompress
    original = FileStream._read

    def read_range(self, offset, length):
        if self.volume.is_encrypted:
            return original(self, offset, length)
        result = []
        block = self.volume.container.block_size
        while length:
            logical, physical, extent_length, _ = self._lookup(offset)
            relative = offset - logical
            assert 0 <= relative < extent_length
            take = min(length, extent_length - relative)
            first, prefix = divmod(relative, block)
            data = (self.volume.container._read_block(physical + first, (prefix + take + block - 1) // block)
                    if physical else bytes(prefix + take))
            result.append(data[prefix:prefix + take])
            assert len(result[-1]) == take
            offset, length = offset + take, length - take
        return b''.join(result)

    FileStream._read = read_range
    return library


def safe_path(root, relative):
    parts = PurePosixPath(relative)
    assert not parts.is_absolute() and '..' not in parts.parts
    target = root / relative
    for parent in target.parents:
        if parent == root:
            break
        assert not parent.is_symlink(), f'Unexpected symlink parent: {relative}'
    return target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True, type=Path)
    ap.add_argument('--zbm-library', required=True, type=Path)
    ap.add_argument('--seven', required=True, type=Path)
    ap.add_argument('--root-only', action='store_true')
    ap.add_argument('--restart-incomplete', action='store_true')
    args = ap.parse_args()
    library = decoder(args.zbm_library)
    work, ios = args.work, args.work / 'build/ios26'
    ios.mkdir(exist_ok=True)
    inputs = work / 'ipsw'
    assert (inputs / ('.os-decrypted' if args.root_only else '.systemos-decrypted')).exists()
    logs = work / 'extraction-logs'
    logs.mkdir(exist_ok=True)
    if not (ios / '.done-rootfs').exists():
        with (ios / 'rootfs.slt').open('wb') as output:
            subprocess.run([str(args.seven), 'l', '-slt', str(inputs / 'os.dmg')], stdout=output, check=True)
        subprocess.run(['python3', str(ios / 'select-system.py'), str(ios / 'rootfs.slt'),
                        str(ios / 'system.files'), str(ios / 'system.manifest')], check=True)
        with (ios / 'var.skel').open('wb') as output:
            subprocess.run(['python3', str(work / 'build/ios165/mkvarskel.py'), str(ios / 'rootfs.slt')],
                           stdout=output, check=True)
    last = time.monotonic()
    total = 0

    def copy_node(node, target, relative):
        nonlocal total, last
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        temporary = target.with_name(target.name + '.partial')
        digest, count = hashlib.sha256(), 0
        with node.open() as source, temporary.open('xb') as dest:
            while data := source.read(1 << 20):
                dest.write(data)
                digest.update(data)
                count += len(data)
                if time.monotonic() - last > 15:
                    print(f'Extracted {total + count:,} bytes: {relative}', flush=True)
                    last = time.monotonic()
        assert count == node.size, (relative, count, node.size)
        temporary.chmod(node.mode & 0o7777)
        temporary.rename(target)
        total += count
        return {'bytes': count, 'sha256': digest.hexdigest(), 'mode': node.mode,
                'uid': node.uid, 'gid': node.gid}

    for name, image in (('rootfs', 'os.dmg'), ('cryptex', 'systemos.dmg'), ('cryptex-app', 'appos.dmg')):
        if args.root_only and name != 'rootfs':
            continue
        if (ios / ('.done-' + name)).exists():
            continue
        dest = ios / name
        if dest.exists():
            if not args.restart_incomplete:
                raise FileExistsError(f'Inspect unfinished extraction before retrying: {dest}')
            previous = dest.with_name(name + '.incomplete-' + str(time.time_ns()))
            dest.rename(previous)
            print(f'Preserved incomplete extraction at {previous}', flush=True)
        dest.mkdir()
        files = {}
        print(f'Extracting {name} from freshly decrypted {image}', flush=True)
        with (inputs / image).open('rb') as stream:
            volume = APFS(stream).volumes[0]
            if name == 'rootfs':
                selected = (ios / 'system.files').read_text().splitlines()
                prefix = 'private/var/staged_system_apps/'
                staged = {p[len(prefix):].split('/')[0] for p in selected if p.startswith(prefix)}
                def source_path(path):
                    parts = path.split('/')
                    return prefix + '/'.join(parts[1:]) if len(parts) > 1 and parts[0] == 'Applications' and parts[1] in staged else path
                rows = [row.split('\t', 4) for row in (ios / 'system.manifest').read_text().splitlines()]
                for mode, uid, gid, relative, target in rows:
                    relative = source_path(relative)
                    path = safe_path(dest, relative)
                    if stat.S_ISDIR(int(mode, 8)):
                        path.mkdir(parents=True, exist_ok=True)
                        path.chmod(int(mode, 8) & 0o7777)
                for relative in selected:
                    files[relative] = copy_node(volume.get(relative), safe_path(dest, relative), relative)
                for mode, uid, gid, relative, target in rows:
                    if stat.S_ISLNK(int(mode, 8)):
                        path = safe_path(dest, source_path(relative))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        if path.is_symlink():
                            assert os.readlink(path) == target
                        else:
                            path.symlink_to(target)
            else:
                symlinks = []
                def walk(directory, parent=''):
                    for entry in directory.iterdir():
                        relative = '/'.join(filter(None, (parent, entry.name)))
                        if name == 'cryptex' and (relative == 'System/DriverKit' or relative.endswith('.symbols')):
                            continue
                        node, path = entry.inode, safe_path(dest, relative)
                        if node.is_dir():
                            path.mkdir(parents=True, exist_ok=True)
                            walk(node, relative)
                            path.chmod(node.mode & 0o7777)
                        elif node.is_symlink():
                            symlinks.append((path, node.readlink()))
                        elif node.is_file():
                            files[relative] = copy_node(node, path, relative)
                        else:
                            raise ValueError(f'Unsupported inode type {relative}')
                walk(volume.root)
                for path, target in symlinks:
                    path.symlink_to(target)
            (logs / f'{name}.json').write_text(json.dumps({'volume': volume.name, 'files': files}, indent=2) + '\n')
        (ios / ('.done-' + name)).touch()
        print(f'Completed {name}: {len(files)} regular files', flush=True)
    if args.root_only:
        return
    cache = ios / '23G90__iPhone18,3'
    cache.mkdir(exist_ok=True)
    for source in (ios / 'cryptex/System/Library/Caches/com.apple.dyld').glob('dyld_shared_cache_arm64e*'):
        if not source.name.endswith('.symbols'):
            os.link(source, cache / source.name)
    assert (cache / 'dyld_shared_cache_arm64e').is_file()
    shutil.copyfile(inputs / 'appos.dmg', ios / 'cryptex-app.dmg')
    assert b'\r' not in (ios / 'system.manifest').read_bytes()
    (work / 'extraction-complete.json').write_text(json.dumps({
        'fresh_from_local_ipsw': True, 'previous_extracted_images_used': False,
        'version': '26.6.2', 'build': '23G90', 'selected_root_recipe': 'ios165/select-system.py',
        'cache_files': len(list(cache.iterdir())), 'written_regular_file_bytes': total}, indent=2) + '\n')
    print('Fresh firmware extraction complete.', flush=True)


if __name__ == '__main__':
    main()
