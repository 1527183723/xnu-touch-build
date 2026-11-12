#!/usr/bin/env python3
"""Build the pinned superboyyy GPU stack for the working Warsaw TouchCom kernel.

Run in WSL. Sources, objects and the small APFS root are private to this port.
The existing System, loader, touch kernel and rollback payload are preserved.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import time

from build_native_metal import Build, ROOT, inspect_rust_archive, sha, write_json
from macho_verify import inspect
from package_touch_experimental import payload_check

REV = 'f1ae906fcc43ba8f763663257724ba938fb820ab'
DONOR = ROOT.parent / 'hakios-arm64e-dada-gpu-port'
CACHE = Path('/home/LZY/hakios-arm64e-dada-gpu-20261031')
OLD = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110')
TOUCH = ROOT / 'out/dada-touch-experimental-r3-20261110'
TOUCH_ROOT = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110/root')


def extract(archive, destination, strip):
    destination.mkdir(parents=True)
    with tarfile.open(archive) as source:
        for member in source:
            parts = Path(member.name).parts[strip:]
            if not parts or not member.isfile():
                continue
            name = Path(*parts)
            assert not name.is_absolute() and '..' not in name.parts
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as src, path.open('wb') as out:
                shutil.copyfileobj(src, out)
            path.chmod(member.mode & 0o777)


def verify_postimages(tree, patch):
    result = []
    for block in patch.read_text().split('diff --git ')[1:]:
        name = block.splitlines()[0].split(' b/', 1)[1]
        match = re.search(r'^index [0-9a-f]+\.\.([0-9a-f]+)', block, re.M)
        if not match:
            continue
        path = tree / name
        assert path.is_file(), name
        data = path.read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        # The published plugin hunk reconstructs this hash from its exact
        # 73d6eb0 base; its index line was left stale in the upstream export.
        stale = name == 'NativeAdrenoMetalPlugin.mm' and match[1] == '1f18a1b' and blob == '3a4c01ecdb2cb101361ad9ec1cf6c155c323abf9'
        assert blob.startswith(match[1]) or stale, (name, blob, match[1])
        result.append({'file': name, 'git_blob': blob, 'sha256': sha(path),
                       'published_index_stale': stale})
    return result


class Port(Build):
    def prepare(self):
        assert subprocess.check_output(['git', '-C', str(DONOR), 'rev-parse', 'HEAD'], text=True).strip() == REV
        if (self.work / 'prepared.json').exists():
            return
        self.gpu.mkdir(parents=True, exist_ok=True)
        patches = self.work / 'donor-patches'
        patches.mkdir(exist_ok=True)
        for source in (DONOR / 'gpu2/patches').glob('*.patch'):
            (patches / source.name).write_text(source.read_text())
        sources = {}
        for name, filename, expected, strip in [
            ('nativemetal', CACHE / 'nativemetal-c6d29ca.tar', None, 0),
            ('metal2vulkan', ROOT.parent / 'dada-adrenometal-port/downloads/metal2vulkan.tar.gz',
             '6232a3df14ba9a666742fe6fde8cf8b4967eb2d438e6b7244659b23cb0fd80a1', 1),
            ('mesa', self.a.mesa_archive,
             '44457ab703f3141ca699095f2f4e0c6e4259f86b005d2ade0b41b14b8e751b0f', 1),
        ]:
            digest = sha(filename)
            if expected:
                assert digest == expected, filename
            dest = self.mesa if name == 'mesa' else self.gpu / name
            print('EXTRACT', name, flush=True)
            if not dest.exists():
                extract(filename, dest, strip)
            patch_name = {'nativemetal': 'nativemetal-dada.patch',
                          'metal2vulkan': 'metal2vulkan-720ef1c-dada.patch',
                          'mesa': 'mesa-85fda3b-dada.patch'}[name]
            patch = patches / patch_name
            applied = subprocess.run(['git', 'apply', '--check', '-R', str(patch)], cwd=dest,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if not applied:
                self.run('apply-' + name, ['git', 'apply', '--whitespace=nowarn', patch], cwd=dest)
            sources[name] = {'base_archive': str(filename), 'archive_sha256': digest,
                             'patch_sha256': sha(patch), 'postimages': verify_postimages(dest, patch)}
        for name in ['nativeadreno', 'adrenopm4', 'NativeMobileDisplay']:
            shutil.copytree(CACHE / 'src' / name, self.gpu / name, dirs_exist_ok=True)
        # Stable donor already folds 0202/0203 into nativemetal-dada.patch.
        # 0201 requires its experimental newer translator and is excluded.
        frontend = (self.gpu / 'nativemetal/NativeBridgeMetalFrontend.mm').read_text()
        assert 'encoder.commandBuffer=self;return encoder;' in frontend
        assert 'native_bridge_metal_first_problem' in frontend and 'gFirstProblem' in frontend
        generated = self.work / 'generated'
        generated.mkdir()
        for name in ['clang', 'clang++']:
            (self.work / name).symlink_to(OLD / name)
        (self.work / 'toolchains').symlink_to(OLD / 'toolchains', target_is_directory=True)
        # Base Build.turnip() performs only the existing A830 catalogue correction.
        # Supply its source input from THIS donor, not ios-dodge.
        dest = self.gpu / 'mesa/files/src/freedreno/vulkan/tu_knl_darwin.cc'
        dest.parent.mkdir(parents=True)
        shutil.copy2(self.mesa / 'src/freedreno/vulkan/tu_knl_darwin.cc', dest)
        manifest = {'repository': 'https://github.com/superboyyy/hakios-arm64e-dada',
            'revision': REV, 'stable_translator_base': '720ef1ce9ae2a2068999a8675717418b9416283e',
            'sources': sources, 'donor_patches_already_in_stable_export': ['0202', '0203'],
            'pointer_abi': 'Warsaw plain-pointer compatibility mode; no PAC ABI relabelling',
            'kernel': 'working TouchCom r3, reused byte for byte',
            'port_transport': 'RedwoodGPU, existing A830 chip mapping and IR3 workarounds'}
        write_json(self.out / 'source-manifest.json', manifest)
        write_json(self.work / 'prepared.json', {'revision': REV})
        print('PASS verified donor sources', flush=True)

    def objects(self):
        display = self.gpu / 'NativeMobileDisplay'
        if not display.exists():
            shutil.copytree(CACHE / 'src/NativeMobileDisplay', display)
        # Regenerate from this donor: its smoke shader ABI differs from ios-dodge.
        lines = ['// Generated from the pinned superboyyy smoke sources.', '#pragma once', '#include <stdint.h>']
        for source in sorted((self.gpu / 'nativemetal/shaders').glob('smoke_*')):
            binary = self.work / 'generated' / (source.name + '.spv')
            self.run('shader-' + source.name, ['glslc', '--target-env=vulkan1.1', '-O', source, '-o', binary])
            self.run('validate-' + source.name, ['spirv-val', '--target-env', 'vulkan1.1', binary])
            data = binary.read_bytes()
            words = struct.unpack('<%dI' % (len(data) // 4), data)
            lines.append('static const uint32_t kSpv_' + source.name.replace('.', '_') + '[] = {')
            for at in range(0, len(words), 8):
                lines.append('    ' + ', '.join('0x%08x' % word for word in words[at:at + 8]) + ',')
            lines.append('};')
        (self.work / 'generated/NativeTurnipShaders.h').write_text('\n'.join(lines) + '\n')
        write_json(self.out / 'dependency-sources.json', {
            name: {str(path.relative_to(self.gpu / name)): sha(path)
                   for path in sorted((self.gpu / name).rglob('*')) if path.is_file()}
            for name in ['nativeadreno', 'adrenopm4', 'NativeMobileDisplay']})
        super().objects()

    def package(self):
        root = self.work / 'root'
        assert not root.exists()
        shutil.copytree(TOUCH_ROOT, root, symlinks=True)
        bundle = root / 'lib/RedwoodMetal.bundle'
        for name in ['RedwoodMetal', 'libvulkan_freedreno.dylib']:
            inspect(self.out / name)
            shutil.copy2(self.out / name, bundle / name)
        shutil.copy2(self.out / 'native-metal-probe', root / 'bin/native-metal-probe')
        shutil.copy2(self.out / 'source-manifest.json', root / 'etc/superboyyy-gpu-build.json')
        (root / 'lib/NativeAdrenoMetal.publish').write_text('superboyyy stable GPU port, Warsaw iOS26, touch r3\n')
        changed = []
        for path in sorted(root.rglob('*')):
            old = TOUCH_ROOT / path.relative_to(root)
            if path.is_symlink():
                assert old.is_symlink() and os.readlink(path) == os.readlink(old)
            elif path.is_file() and (not old.exists() or sha(path) != sha(old)):
                changed.append(str(path.relative_to(root)))
        fw = self.a.firmware_work
        build = fw / 'build'
        env = dict(self.env, PYTHONPATH=str(fw / 'host-python'))
        disk = self.out / 'root26.apfs'
        self.run('apfs-build', ['python3', build / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
                 '-o', disk, 'redwood=' + str(root)], cwd=build / 'userland', env=env)
        self.run('apfs-check', ['bash', build / 'tools/apfs/check-image.sh', disk, 'redwood=' + str(root)],
                 cwd=build / 'tools/apfs', env=env)
        config = json.loads((TOUCH / 'boot-config.json').read_text())
        cmdline = ' '.join(x for x in config['cmdline'].split() if not x.startswith('epoch=')) + f' epoch={int(time.time())}'
        loader = ROOT / 'out/dada-ram-usb-r2/loader-dada-ram-usb.efi'
        kernel = TOUCH / 'kernel-touch.bin'
        payload = self.out / 'ios-payload.bin'
        self.run('payload', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
                 'loader.efi=' + str(loader), 'kernel=' + str(kernel), 'ramdisk=' + str(disk),
                 '--identity', 'v57', '--usb', '--memsize', '240000000', '--cmdline', cmdline,
                 '--forbid-cmd', 'loader_return_after', '--require-cmd', 'dadatouch=1'])
        write_json(self.out / 'payload-entries.json', payload_check(payload,
                   {'loader.efi': loader, 'kernel': kernel, 'ramdisk': disk}))
        image = self.out / 'warsaw-dada-superboyyy-gpu-r1.img'
        shutil.copy2(TOUCH / config['uefi'], image)
        files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
                 for p in [disk, payload, image, self.out / 'RedwoodMetal', self.out / 'libvulkan_freedreno.dylib']}
        config.update(cmdline=cmdline, uefi=image.name, gpu_stack='superboyyy stable NativeMetal / metal2vulkan / Turnip',
                      files=files, hardware_tested=False, uefi_binary_changed=False)
        write_json(self.out / 'boot-config.json', config)
        final_report = {'revision': REV, 'changed_small_root_files': changed,
                   'touch_kernel_sha256': sha(kernel), 'system_reused': str(ROOT / 'out/ios26-r1/system26.apfs'),
                   'apfs_content_verified': True, 'hardware_tested': False, 'files': files,
                   'rollback_payload': str(TOUCH / 'ios-payload.bin')}
        shutil.copytree(DONOR / 'LICENSES', self.out / 'licenses/donor', dirs_exist_ok=True)
        shutil.copy2(DONOR / 'NOTICE.md', self.out / 'licenses/SUPERBOYYY-NOTICE.md')
        for name, directory in [('AdrenoMetal', self.gpu / 'nativemetal'),
                                 ('metal2vulkan', self.gpu / 'metal2vulkan'), ('Mesa', self.mesa)]:
            for path in directory.iterdir():
                if path.is_file() and path.name.upper().startswith(('LICENSE', 'COPYING')):
                    shutil.copy2(path, self.out / 'licenses' / (name + '-' + path.name))
        shutil.copy2(ROOT / 'docs/DADA-SUPERBOYYY-GPU-PORT-20261111.md', self.out / 'README.md')
        write_json(self.out / 'SOURCE-PATHS.json', {'donor_clone': str(DONOR),
                   'corresponding_gpu_source': str(self.work / 'source'),
                   'port_builder': str(ROOT / 'tools/dada/build_superboyyy_gpu.py'),
                   'donor_revision': REV})
        write_json(self.out / 'build-manifest.json', final_report)
        print('PASS private GPU root/payload; matching touch kernel preserved', flush=True)


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('stage', choices=['prepare', 'turnip', 'bridge', 'objects', 'frontend', 'package'])
    ap.add_argument('--work', type=Path, default=Path('/home/LZY/warsaw-superboyyy-gpu-r1-20261111'))
    ap.add_argument('--output', type=Path, default=ROOT / 'out/dada-superboyyy-gpu-r1-20261111')
    args = ap.parse_args()
    args.sdk = Path('/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk')
    args.ld = Path('/home/LZY/hakios-full-rebuild-20261031/deps/theos/toolchain/linux/iphone/bin/ld')
    args.ldid = Path('/home/LZY/dada-gpu-build-r1/theos/toolchain/linux/iphone/bin/ldid')
    args.meson = Path('/home/LZY/dada-ios26-k90auto-20261106/venv/bin/meson')
    args.mesa_archive = ROOT.parent / 'dada-adrenometal-port/downloads/mesa-85fda3.tar.gz'
    args.firmware_work = Path('/home/LZY/warsaw-ios26-20261110')
    build = Port(args)
    getattr(build, args.stage)()


if __name__ == '__main__':
    main()
