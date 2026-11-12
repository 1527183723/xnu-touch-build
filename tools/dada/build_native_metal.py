#!/usr/bin/env python3
"""Port the pinned ios-dodge GPU userspace to Warsaw's iOS 26 RedwoodGPU.

Run on Linux/WSL. Every stage writes into an isolated work/output directory;
the existing kernel, System and working graphics root are never edited.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import tarfile
import time
import tomllib

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
MESA_SHA = '44457ab703f3141ca699095f2f4e0c6e4259f86b005d2ade0b41b14b8e751b0f'
SOURCE_REV = '2b3f8ae564807268f3e595c30f682e23a947816a'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + '\n')


def inspect_rust_archive(path):
    """Inspect the real member headers; never promote ordinary ARM64 objects by relabelling."""
    data = path.read_bytes()
    assert data[:8] == b'!<arch>\n'
    cursor, members, subtypes = 8, 0, {}
    while cursor < len(data):
        header = data[cursor:cursor + 60]
        assert header[58:60] == b'`\n'
        size = int(header[48:58])
        start = cursor + 60
        name = header[:16].decode().strip()
        if name.startswith('#1/'):
            start += int(name[3:])
        if data[start:start + 4] == bytes.fromhex('cffaedfe'):
            _, cpu, subtype = struct.unpack_from('<3I', data, start)
            assert cpu == 0x100000c and (subtype & 0xffffff) == 2, (name, hex(cpu), hex(subtype))
            members += 1
            subtypes[hex(subtype)] = subtypes.get(hex(subtype), 0) + 1
        cursor += 60 + size + (size & 1)
    assert cursor == len(data) and members > 0
    return {'arm64e_object_members': members, 'cpu_subtypes': subtypes,
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class Build:
    def __init__(self, a):
        self.a = a
        self.work, self.out = a.work, a.output
        self.gpu = self.work / 'source/gpu'
        self.mesa = self.work / 'source/mesa'
        self.sdk = a.sdk
        self.work.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update(RUSTUP_HOME=str(self.work / 'toolchains/rustup'),
                        CARGO_HOME=str(self.work / 'toolchains/cargo'),
                        SDKROOT=str(self.sdk), IPHONEOS_DEPLOYMENT_TARGET='26.0')
        self.env['PATH'] = ':'.join([str(self.work / 'toolchains/cargo/bin'),
                                    str(a.meson.parent), self.env['PATH']])

    def run(self, name, cmd, cwd=None, env=None):
        cmd = [str(x) for x in cmd]
        print('RUN', name, flush=True)
        write_json(self.out / (name + '.command.json'), cmd)
        with (self.out / (name + '.log')).open('w') as log:
            r = subprocess.run(cmd, cwd=cwd, env=env or self.env,
                               stdout=log, stderr=subprocess.STDOUT)
        if r.returncode:
            print((self.out / (name + '.log')).read_text(errors='replace')[-14000:], flush=True)
            raise SystemExit(f'{name}: exit {r.returncode}')
        print('PASS', name, flush=True)

    def prepare(self):
        if not self.gpu.exists():
            shutil.copytree(self.a.source / 'source/ios27/gpu', self.gpu)
        identity = self.out / 'source-manifest.json'
        origin = {'repository': 'https://github.com/faridbtw/ios-dodge',
                  'branch': 'iOS27', 'revision': SOURCE_REV,
                  'upstream': json.loads((self.a.source / 'source/ios27/UPSTREAM.json').read_text()),
                  'gpu_files': {str(p.relative_to(self.gpu)): sha(p)
                                for p in sorted(self.gpu.rglob('*')) if p.is_file()}}
        if identity.exists():
            # The isolated tree now also contains the recorded translator patches.
            assert json.loads(identity.read_text())['revision'] == SOURCE_REV
        else:
            write_json(identity, origin)
        assert sha(self.a.mesa_archive) == MESA_SHA, 'Mesa archive checksum'
        if not self.mesa.exists():
            self.mesa.mkdir(parents=True)
            with tarfile.open(self.a.mesa_archive) as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    rel = Path(member.name.split('/', 1)[1])
                    assert '..' not in rel.parts and not rel.is_absolute()
                    dest = self.mesa / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as src, dest.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    dest.chmod(member.mode & 0o777)
        self.apply_patches(self.mesa, [self.gpu / 'mesa/darwin-adreno.patch'], 'mesa')
        shutil.copytree(self.gpu / 'mesa/files', self.mesa, dirs_exist_ok=True)
        # ios-dodge vendors the patched, subsequently formatted translator itself.
        # Applying its historical patch series again would duplicate definitions.
        self.verify_translator_snapshot()
        generated = self.work / 'generated'
        generated.mkdir(exist_ok=True)
        lines = ['// Generated from this snapshot smoke shaders with glslc.', '#pragma once', '#include <stdint.h>']
        for src in sorted((self.gpu / 'nativemetal/shaders').glob('smoke_*')):
            dest = generated / (src.name + '.spv')
            self.run('shader-' + src.name, ['glslc', '--target-env=vulkan1.1', '-O', src, '-o', dest])
            self.run('validate-' + src.name, ['spirv-val', '--target-env', 'vulkan1.1', dest])
            data = dest.read_bytes()
            words = struct.unpack('<%dI' % (len(data) // 4), data)
            lines.append('static const uint32_t kSpv_' + src.name.replace('.', '_') + '[] = {')
            for offset in range(0, len(words), 8):
                lines.append('    ' + ', '.join('0x%08x' % w for w in words[offset:offset + 8]) + ',')
            lines.append('};')
        (generated / 'NativeTurnipShaders.h').write_text('\n'.join(lines) + '\n')
        # Use the same target and linker as the existing verified Warsaw arm64e libraries.
        for name, compiler in [('clang', 'clang-21'), ('clang++', 'clang++-21')]:
            wrapper = self.work / name
            wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\n'
                               'extra=[] if any(x in a for x in ("-c","-E","-S","-fsyntax-only")) else '
                               + repr(['--ld-path=' + str(self.a.ld)]) + '\n'
                               + f'os.execvp({compiler!r},[{compiler!r},*extra,*a])\n')
            wrapper.chmod(0o755)

    def apply_patches(self, directory, patches, label):
        stamp = self.work / (label + '-patches.json')
        values = {p.name: sha(p) for p in patches}
        if stamp.exists():
            assert json.loads(stamp.read_text()) == values
            return
        for p in patches:
            normalized = self.work / (label + '-' + p.name)
            normalized.write_text(p.read_text(), newline='\n')
            # Windows snapshots use CRLF; git apply compares the actual bytes, including on reverse checks.
            for line in normalized.read_text().splitlines():
                if line.startswith('+++ b/'):
                    target = directory / line[6:]
                    if target.is_file():
                        target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n'))
            check = subprocess.run(['git', 'apply', '--check', str(normalized)], cwd=directory,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if check.returncode:
                reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(normalized)], cwd=directory,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if reverse.returncode:
                    raise RuntimeError(check.stdout.decode(errors='replace'))
                print('Already applied upstream:', p.name, flush=True)
            else:
                self.run(label + '-' + p.stem, ['git', 'apply', str(normalized)], cwd=directory)
        write_json(stamp, values)

    def verify_translator_snapshot(self):
        checks = {
            'metal2vulkan-air/src/canon/sccp.rs': ['fn leafwise(', 'self.leafwise(*v)', 'leaf_chain'],
            'metal2vulkan-runtime/src/used_resources.rs': ['fn buffers_overlap(', 'pub fn references_resource(', 'pub fn references_heap('],
            'metal2vulkan-runtime/src/device/prime.rs': ['fn cache_key(', 'entry-point-priming-v1', 'primer.disk.keep('],
            'metal2vulkan-runtime/src/shader_cache/mod.rs': ['struct PrimingCache', 'fn priming('],
            'metal2vulkan-air/src/canon/const_loads.rs': ['let first_class', '!t.is_aggregate()'],
            'metal2vulkan-air/src/canon/mod.rs': ['prune_entry(', 'const_loads::fold('],
            'metal2vulkan/src/device/profile.rs': ['fn render_fingerprint(', 'kept.push('],
            'metal2vulkan/src/pipeline/compile.rs': ['fn decode_kept(', 'KEPT_DECODES'],
            'metal2vulkan-runtime/src/render.rs': ['if self.pipeline == vk::Pipeline::null()'],
            'metal2vulkan-runtime/src/ycbcr.rs': ['b"_RB4"'],
        }
        for name, anchors in checks.items():
            text = (self.gpu / 'metal2vulkan/crates' / name).read_text()
            for anchor in anchors:
                assert anchor in text, (name, anchor)
        write_json(self.out / 'translator-snapshot.json', {'patches_already_vendored': True,
                   'verified_feature_anchors': checks,
                   'historical_patches': {p.name: sha(p) for p in sorted(
                       (self.gpu / 'nativemetal/bridge/patches').glob('*.patch'))}})

    def toolchain(self):
        manifest = self.out / 'downloads/channel-rust-1.98.0.toml'
        assert sha(manifest) == '3f7d139b73bbbd0004ef6e58b430831c68cdad2b1f64ee2eb35d54c09199489a'
        packages = tomllib.loads(manifest.read_text())['pkg']
        prefix = self.work / 'toolchains/compiler'
        for name in ['rustc', 'cargo', 'rust-std', 'rust-src']:
            target = '*' if name == 'rust-src' else 'x86_64-unknown-linux-gnu'
            expected = packages[name]['target'][target]['xz_hash']
            archive = self.work / 'toolchains/rustup/downloads' / expected
            assert sha(archive) == expected, name
            dest = self.work / 'toolchains/installers' / name
            if not dest.exists():
                dest.mkdir(parents=True)
                with tarfile.open(archive) as tar:
                    tar.extractall(dest, filter='data')
            installer = next(dest.glob('*/install.sh'))
            self.run('install-' + name, ['bash', installer, '--prefix=' + str(prefix), '--disable-ldconfig'])
        self.run('rust-version', [prefix / 'bin/rustc', '-vV'])

    def turnip(self):
        # RedwoodGPU reports the Qualcomm catalogue ID, not a decimal model encoding.
        # The source backend only special-cased A740. Match the pinned Mesa catalogue's A830.
        backend = self.mesa / 'src/freedreno/vulkan/tu_knl_darwin.cc'
        original = (self.gpu / 'mesa/files/src/freedreno/vulkan/tu_knl_darwin.cc').read_text()
        before = '   if (info->chip_id == 0x43050a01)\n      device->dev_id.gpu_id = 740;'
        after = before + '\n   else if (info->chip_id == 0x44050001)\n      device->dev_id.gpu_id = 830;'
        assert original.count(before) == 1
        patched = original.replace(before, after)
        if backend.read_text() != patched:
            backend.write_text(patched)
        import difflib
        (self.out / 'warsaw-turnip.patch').write_text(''.join(difflib.unified_diff(
            original.splitlines(keepends=True), patched.splitlines(keepends=True),
            fromfile='a/src/freedreno/vulkan/tu_knl_darwin.cc',
            tofile='b/src/freedreno/vulkan/tu_knl_darwin.cc')))
        flags = ['-target', 'arm64e-apple-ios26.0', '-isysroot', str(self.sdk),
                 '-miphoneos-version-min=26.0', '-fstack-protector-strong']
        cross = self.work / 'ios26.cross'
        cross.write_text('[binaries]\n' + '\n'.join([
            'c = ' + repr([str(self.work / 'clang'), *flags]),
            'cpp = ' + repr([str(self.work / 'clang++'), *flags, '-fuse-cxa-atexit']),
            'objc = ' + repr([str(self.work / 'clang'), *flags]),
            "ar = '/usr/bin/llvm-ar-21'", "strip = '/usr/bin/llvm-strip-21'",
            "bison = '/usr/bin/bison'", "pkg-config = '/usr/bin/false'",
            '[host_machine]', "system = 'darwin'", "subsystem = 'ios'",
            "cpu_family = 'aarch64'", "cpu = 'arm64e'", "endian = 'little'",
            '[properties]', 'needs_exe_wrapper = true', '']))
        options = ['-Dgallium-drivers=', '-Dvulkan-drivers=freedreno', '-Dfreedreno-kmds=darwin',
                   '-Dtu-darwin-abi-dir=' + str(self.gpu / 'nativeadreno'), '-Dplatforms=macos',
                   '-Dglx=disabled', '-Degl=disabled', '-Dgbm=disabled', '-Dopengl=false',
                   '-Dgles1=disabled', '-Dgles2=disabled', '-Dllvm=disabled', '-Dshared-glapi=disabled',
                   '-Dxmlconfig=disabled', '-Dshader-cache=enabled', '-Dtools=', '-Dbuild-tests=false',
                   '-Dvideo-codecs=', '-Dvulkan-layers=', '-Dlibunwind=disabled', '-Dlmsensors=disabled',
                   '-Dvalgrind=disabled', '-Dexpat=disabled', '-Dzstd=disabled', '-Dbuildtype=release',
                   '-Db_ndebug=true', '-Db_lundef=true']
        dest = self.work / 'turnip-build'
        if (dest / 'build.ninja').exists():
            command = [self.a.meson, 'configure', dest, *options]
        else:
            command = [self.a.meson, 'setup', dest, self.mesa, '--cross-file', cross,
                       '--wrap-mode=nodownload', *options]
        self.run('turnip-configure', command)
        self.run('turnip-compile', ['ninja', '-C', dest, '-j2', 'src/freedreno/vulkan/libvulkan_freedreno.dylib'])
        shutil.copy2(dest / 'src/freedreno/vulkan/libvulkan_freedreno.dylib', self.out / 'libvulkan_freedreno.dylib')

    def bridge(self):
        generated = self.work / 'generated'
        for dest, src in [('runtime.air', 'examples/runtime_examples.ll'),
                          ('function-constant.air', 'kernel_function_constant_gate_fold.ll')]:
            self.run('air-' + dest, ['llvm-as-21', '-o', generated / dest,
                     self.gpu / 'metal2vulkan/crates/metal2vulkan-validation/fixtures/public' / src])
        bridge = self.work / 'bridge'
        bridge.mkdir(exist_ok=True)
        (bridge / 'Cargo.toml').write_text('\n'.join([
            '[package]', 'name = "adreno-metal-bridge"', 'version = "0.1.0"',
            'edition = "2024"', 'rust-version = "1.98"', '[workspace]', '[lib]',
            'crate-type = ["staticlib"]', 'path = ' + json.dumps(str(self.gpu / 'nativemetal/bridge/NativeMetalBridge.rs')),
            '[dependencies]', 'metal2vulkan = { path = ' + json.dumps(str(self.gpu / 'metal2vulkan/crates/metal2vulkan')) + ', features = ["ash"] }',
            'metal2vulkan-runtime = { path = ' + json.dumps(str(self.gpu / 'metal2vulkan/crates/metal2vulkan-runtime')) + ' }',
            'libc = "0.2"', 'sha2 = { version = "0.10", features = ["asm"] }',
            '[profile.release]', 'panic = "unwind"', 'debug = 1', 'codegen-units = 16', '']))
        target = 'arm64e-apple-ios'
        compiler = self.work / 'toolchains/compiler/bin'
        env = dict(self.env, RUSTC_BOOTSTRAP='1', RUSTC=str(compiler / 'rustc'), CARGO_BUILD_JOBS='1',
                   NMB_RUNTIME_ID=sha(self.out / 'source-manifest.json')[:32],
                   NMB_FIXTURE_AIR=str(generated / 'runtime.air'), NMB_FC_AIR=str(generated / 'function-constant.air'),
                   CC_arm64e_apple_ios=str(self.work / 'clang'),
                   CFLAGS_arm64e_apple_ios='-target arm64e-apple-ios26.0 -isysroot ' + str(self.sdk),
                   RUSTFLAGS='-C debuginfo=1')
        self.run('bridge-compile', [compiler / 'cargo', 'build', '--release', '-Z', 'build-std=std,panic_unwind',
                                   '--manifest-path', bridge / 'Cargo.toml', '--target', target,
                                   '--target-dir', self.work / 'cargo-target'], env=env)
        shutil.copy2(self.work / 'cargo-target' / target / 'release/libadreno_metal_bridge.a',
                     self.out / 'libadreno_metal_bridge.a')
        shutil.copy2(bridge / 'Cargo.lock', self.out / 'Cargo.lock')
        write_json(self.out / 'rust-archive-report.json', inspect_rust_archive(self.out / 'libadreno_metal_bridge.a'))

    def objects(self):
        flags = ['-target', 'arm64e-apple-ios26.0', '-isysroot', str(self.sdk),
                 '-O2', '-gline-tables-only',
                 '-std=c++17', '-fobjc-arc', '-fstack-protector-strong', '-Wno-deprecated-declarations']
        for directory in [self.gpu / 'nativemetal', self.gpu / 'nativeadreno',
                          self.gpu / 'adrenopm4', self.mesa / 'include', self.work / 'generated']:
            flags += ['-I', str(directory)]
        generated_plugin = self.work / 'generated/NativeAdrenoMetalPlugin.mm'
        original = (self.gpu / 'nativemetal/NativeAdrenoMetalPlugin.mm').read_text()
        replacements = {
            'static id clientDevice(unsigned port) {':
                'static id clientDevice(unsigned port) {\n'
                '    // Preserve Warsaw A830 IR3 workarounds through Mesa\'s documented feature overrides.\n'
                '    setenv("FD_DEV_FEATURES", "has_early_preamble=0:has_fs_tex_prefetch=0", 1);',
            'if (Gpu *gpu = stateExport ? nullptr : makeGpu(port)) {':
                'if (Gpu *gpu = nullptr) { // Warsaw has no NativeAdreno cache-export ABI.',
            'if (getenv("GPU2_NATIVE_BACKBOARDD") == nullptr || strcmp(getprogname(), "backboardd") != 0) return device = clientDevice(port);':
                'return device = clientDevice(port); // Warsaw: always use the RedwoodGPU transport.',
        }
        patched = original
        for before, after in replacements.items():
            assert patched.count(before) == 1, before
            patched = patched.replace(before, after)
        generated_plugin.write_text(patched)
        import difflib
        (self.out / 'warsaw-frontend.patch').write_text(''.join(difflib.unified_diff(
            original.splitlines(keepends=True), patched.splitlines(keepends=True),
            fromfile='a/nativemetal/NativeAdrenoMetalPlugin.mm',
            tofile='b/nativemetal/NativeAdrenoMetalPlugin.mm')))
        for name in ['NativeBridgeMetalFrontend', 'NativeAdrenoMetalPlugin', 'NativeOsLogExport']:
            obj = self.work / (name + '.o')
            source = generated_plugin if name == 'NativeAdrenoMetalPlugin' else self.gpu / 'nativemetal' / (name + '.mm')
            self.run(name, [self.work / 'clang++', *flags, '-c', source, '-o', obj])
        obj = self.work / 'AdrenoPM4.o'
        self.run('AdrenoPM4', [self.work / 'clang', '-target', 'arm64e-apple-ios26.0', '-isysroot', self.sdk,
                              '-O2', '-c', self.gpu / 'adrenopm4/AdrenoPM4.c', '-o', obj])
        self.run('metal-probe', [self.work / 'clang', '-target', 'arm64e-apple-ios26.0', '-isysroot', self.sdk,
                 '-O2', '-fobjc-arc', ROOT / 'tools/dada/native_metal_probe.m', '-framework', 'Foundation',
                 '-framework', 'Metal', '-Wl,-adhoc_codesign', '-o', self.out / 'native-metal-probe'])
        inspect(self.out / 'native-metal-probe')
        write_json(self.work / 'frontend-flags.json', flags)

    def frontend(self):
        write_json(self.out / 'rust-archive-report.json', inspect_rust_archive(self.out / 'libadreno_metal_bridge.a'))
        flags = json.loads((self.work / 'frontend-flags.json').read_text())
        objects = [self.work / (name + '.o') for name in
                   ['NativeBridgeMetalFrontend', 'NativeAdrenoMetalPlugin', 'NativeOsLogExport', 'AdrenoPM4']]
        self.run('native-metal-link', [self.work / 'clang++', *flags, '-bundle', *objects,
                 self.out / 'libadreno_metal_bridge.a', '-Wl,-dead_strip', '-Wl,-adhoc_codesign',
                 '-lobjc', '-liconv', '-lz', '-framework', 'Foundation', '-framework', 'Metal',
                 '-framework', 'IOSurface', '-framework', 'IOKit', '-o', self.out / 'RedwoodMetal'])
        for name in ['RedwoodMetal', 'libvulkan_freedreno.dylib']:
            binary = self.out / name
            self.run('strip-' + name, ['llvm-strip-21', '-x', binary])
            self.run('iokit-install-name-' + name, ['llvm-install-name-tool-21', '-change',
                     '/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit',
                     '/System/Library/Frameworks/IOKit.framework/IOKit', binary])
            self.run('sign-' + name, [self.a.ldid, '-S', '-Cadhoc', binary])
        write_json(self.out / 'macho-report.json', {n: inspect(self.out / n)
                   for n in ['RedwoodMetal', 'libvulkan_freedreno.dylib']})
        for name in ['RedwoodMetal', 'libvulkan_freedreno.dylib']:
            symbols = subprocess.check_output(['llvm-nm-21', '-m', '-u', str(self.out / name)], text=True)
            (self.out / (name + '.imports.txt')).write_text(symbols)
            unknown = [line for line in symbols.splitlines()
                       if '(undefined)' in line and 'weak external' not in line and '(from ' not in line]
            assert not unknown, (name, unknown)

    def package(self):
        root = self.work / 'root'
        if root.exists():
            raise FileExistsError('Refusing to reuse an already packaged private root')
        shutil.copytree(self.a.base_root, root, symlinks=True)
        bundle = root / 'lib/RedwoodMetal.bundle'
        for name in ['RedwoodMetal', 'libvulkan_freedreno.dylib']:
            inspect(self.out / name)
            shutil.copy2(self.out / name, bundle / name)
        inspect(self.out / 'native-metal-probe')
        shutil.copy2(self.out / 'native-metal-probe', root / 'bin/native-metal-probe')
        # Keep the kernel's existing discovery name and System symlink, use the new principal class.
        (bundle / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.redwood.metal', 'CFBundleName': 'RedwoodMetal',
            'CFBundleExecutable': 'RedwoodMetal', 'CFBundlePackageType': 'BNDL',
            'CFBundleVersion': '4', 'CFBundleSupportedPlatforms': ['iPhoneOS'],
            'NSPrincipalClass': 'NativeAdrenoMetalDevice', 'MinimumOSVersion': '26.0'}))
        (root / 'lib/NativeAdrenoMetal.publish').write_text('dada ios26 ios-dodge GPU port r4\n')
        (root / 'lib/NativeAdrenoMetal.clients').write_text('*\n')
        # Old libredwoodmetal/AIR are not injected in this base; retain them for diagnostics.
        conf = (root / 'etc/inject.conf').read_text()
        assert 'libredwoodmetal' not in conf and 'libredwoodair' not in conf, conf
        (root / 'etc/native-metal-build.json').write_text((self.out / 'source-manifest.json').read_text())
        changed = []
        for p in sorted(root.rglob('*')):
            rel = p.relative_to(root)
            base = self.a.base_root / rel
            if p.is_symlink():
                assert base.is_symlink() and os.readlink(base) == os.readlink(p)
            elif p.is_file() and (not base.exists() or sha(base) != sha(p)):
                changed.append(str(rel))
        build = self.a.firmware_work / 'build'
        env = dict(self.env, PYTHONPATH=str(self.a.firmware_work / 'host-python'))
        disk = self.out / 'root26.apfs'
        self.run('apfs-build', ['python3', build / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw',
                 '-o', disk, 'redwood=' + str(root)], cwd=build / 'userland', env=env)
        self.run('apfs-check', ['bash', build / 'tools/apfs/check-image.sh', disk, 'redwood=' + str(root)],
                 cwd=build / 'tools/apfs', env=env)
        config = json.loads((self.a.base / 'boot-config.json').read_text())
        cmdline = ' '.join(x for x in config['cmdline'].split() if not x.startswith('epoch=')) + f' epoch={int(time.time())}'
        payload = self.out / 'ios-payload.bin'
        self.run('payload', ['python3', ROOT / 'xnuloader/mkpayload.py', '-o', payload,
                 'loader.efi=' + str(self.a.base / 'loader-dada-ram-usb.efi'),
                 'kernel=' + str(ROOT / 'out/ios26-r1/kernel-ath12k.bin'), 'ramdisk=' + str(disk),
                 '--identity', 'v57', '--usb', '--memsize', '240000000', '--cmdline', cmdline,
                 '--forbid-cmd', 'loader_return_after'])
        image = self.out / 'warsaw-dada-native-metal-r4.img'
        shutil.copy2(self.a.base / 'warsaw-dada-ram-usb-r2.img', image)
        report = {
            'variant': 'ios-dodge NativeMetal + metal2vulkan + Turnip on Warsaw iOS26 RedwoodGPU',
            'source_revision': SOURCE_REV, 'gpu_kernel': 'unchanged Warsaw RedwoodGPU',
            'system_reference': str(ROOT / 'out/ios26-r1/system26.apfs'),
            'rollback_reference': str(ROOT / 'out/dada-graphics-r3'),
            'changed_small_root_files': changed, 'cmdline': cmdline,
            'touch_kernel_port_included': False, 'hardware_tested': False,
            'apfs_checks_passed': True, 'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
                for p in [disk, payload, image, self.out / 'RedwoodMetal', self.out / 'libvulkan_freedreno.dylib',
                          self.out / 'native-metal-probe']}}
        write_json(self.out / 'build-manifest.json', report)
        write_json(self.out / 'boot-config.json', {
            'version': config['version'], 'build': config['build'], 'identity': 'v57',
            'cmdline': cmdline, 'memsize_upper_bound_hex': '240000000',
            'system_reference': '../ios26-r1/system26.apfs', 'payload': payload.name,
            'uefi': image.name, 'uefi_reuses_r2_binary': True,
            'gpu_stack': 'ios-dodge NativeMetal / metal2vulkan / Turnip / Warsaw RedwoodGPU',
            'automatic_return_timeout': False, 'touch_kernel_port_included': False,
            'hardware_tested': False, 'files': report['files']})
        licenses = self.out / 'licenses'
        licenses.mkdir(exist_ok=True)
        for prefix, directory in [('AdrenoMetal', self.gpu / 'nativemetal'),
                                  ('metal2vulkan', self.gpu / 'metal2vulkan'),
                                  ('Mesa', self.mesa), ('AdrenoPM4', self.gpu / 'adrenopm4')]:
            for p in directory.glob('*'):
                if p.is_file() and p.name.upper().startswith(('LICENSE', 'COPYING')):
                    shutil.copy2(p, licenses / (prefix + '-' + p.name))
        shutil.copy2(self.mesa / 'docs/license.rst', licenses / 'Mesa-license.rst')
        shutil.copytree(self.mesa / 'licenses', licenses / 'Mesa-licenses', dirs_exist_ok=True)


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('stage', choices=['prepare', 'toolchain', 'turnip', 'bridge', 'objects', 'frontend', 'package'])
    p.add_argument('--work', type=Path, default=Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110'))
    p.add_argument('--output', type=Path, default=ROOT / 'out/dada-native-metal-ios26-r4')
    p.add_argument('--source', type=Path, default=ROOT.parent / 'ios-dodge-dada')
    p.add_argument('--mesa-archive', type=Path, default=ROOT.parent / 'dada-adrenometal-port/downloads/mesa-85fda3.tar.gz')
    p.add_argument('--sdk', type=Path, default=Path('/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk'))
    p.add_argument('--meson', type=Path, default=Path('/home/LZY/dada-ios26-k90auto-20261106/venv/bin/meson'))
    p.add_argument('--ld', type=Path, default=Path('/home/LZY/hakios-full-rebuild-20261031/deps/theos/toolchain/linux/iphone/bin/ld'))
    p.add_argument('--ldid', type=Path, default=Path('/home/LZY/dada-gpu-build-r1/theos/toolchain/linux/iphone/bin/ldid'))
    p.add_argument('--firmware-work', type=Path, default=Path('/home/LZY/warsaw-ios26-20261110'))
    p.add_argument('--base-root', type=Path, default=Path('/home/LZY/warsaw-dada-graphics-r3-20261110/root'))
    p.add_argument('--base', type=Path, default=ROOT / 'out/dada-ram-usb-r2')
    a = p.parse_args()
    getattr(Build(a), a.stage)()


if __name__ == '__main__':
    main()
