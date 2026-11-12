#!/usr/bin/env python3
"""Rebuild AIR in a private tree, including warsaw's corrected framebuffer-layer fix."""
import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--cached-workspace', type=Path, required=True)
    ap.add_argument('--sdk', type=Path, required=True)
    ap.add_argument('--ld', type=Path, required=True)
    ap.add_argument('--ldid', type=Path, required=True)
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    build = args.work / 'build'
    dest = args.work / 'air-rebuild'
    source = dest / 'mesa-26.2.5'
    if dest.exists() and not args.resume:
        raise FileExistsError(dest)
    dest.mkdir(exist_ok=True)
    cached = args.cached_workspace / 'hackios-xnu-kernel/xnu-deps/build/mesa/mesa-26.2.5'
    print('Copying prepared Mesa 26.2.5 sources to the isolated build', flush=True)
    if not source.exists():
        subprocess.run(['cp', '-a', '--reflink=auto', str(cached), str(source)], check=True)
    for file in (build / 'tools/mesa/air').iterdir():
        if file.is_file():
            shutil.copy2(file, source / 'src/compiler/air' / file.name)
    for file in (build / 'tools/mesa/redwood-drm').iterdir():
        if file.is_file():
            shutil.copy2(file, source / 'src/redwood-drm' / file.name)
    patch = build / 'tools/mesa/rebase-26/0016-nir-lower-fb-read-zero-layer.patch'
    if not args.resume:
        with patch.open('rb') as stream, (dest / '0016-apply.log').open('wb') as log:
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(source)],
                           stdin=stream, stdout=log, stderr=log, check=True)
    fixed = (source / 'src/compiler/nir/nir_lower_fb_read.c').read_text()
    assert 'nir_imm_int(b, 0)' in fixed and 'VARYING_SLOT_LAYER' in fixed
    setup = ast.parse((args.cached_workspace / 'tools/k90auto/setup-mesa-arm64e.py').read_text())
    options = next(ast.literal_eval(node.value) for node in setup.body
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'OPTIONS' for t in node.targets))
    wrapper = dest / 'clang'
    wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\n'
                       'c="clang++-21" if sys.argv[0].endswith("++") else "clang-21"\n'
                       'extra=[] if any(x in a for x in ("-c","-E","-S","-fsyntax-only")) else '
                       + repr(['--ld-path=' + str(args.ld)]) + '\nos.execvp(c,[c,*extra,*a])\n')
    wrapper.chmod(0o755)
    if not (dest / 'clang++').is_symlink():
        (dest / 'clang++').symlink_to(wrapper.name)
    target = ['-target', 'arm64e-apple-ios26.0', '-isysroot', str(args.sdk), '-miphoneos-version-min=26.0']
    cross = dest / 'cross.ini'
    cross.write_text("[binaries]\nbison = '/usr/bin/bison'\n" +
                     f'c = {[str(wrapper), *target]!r}\ncpp = {[str(dest / "clang++"), *target, "-fuse-cxa-atexit"]!r}\n' +
                     f'objc = {[str(wrapper), *target]!r}\n' +
                     "ar = '/usr/bin/llvm-ar-21'\nranlib = '/usr/bin/llvm-ranlib-21'\nstrip = '/usr/bin/llvm-strip-21'\n" +
                     "pkg-config = '/usr/bin/false'\n[host_machine]\nsystem = 'darwin'\nsubsystem = 'ios'\n" +
                     "cpu_family = 'aarch64'\ncpu = 'arm64e'\nendian = 'little'\n[properties]\nneeds_exe_wrapper = true\n")
    venv = args.cached_workspace / 'venv/bin'
    env = dict(os.environ, PATH=str(venv) + ':' + os.environ['PATH'], PYTHONHASHSEED='0')
    with (dest / 'setup.log').open('wb') as log:
        subprocess.run([str(venv / 'meson'), 'setup', str(dest / 'build'), str(source),
                        '--cross-file', str(cross), *options], env=env, stdout=log, stderr=log, check=True)
    print('Compiling AIR with framebuffer-layer patch 0016', flush=True)
    library = Path('src/gallium/drivers/freedreno/libredwoodair.dylib')
    with (dest / 'ninja.log').open('wb') as log:
        subprocess.run(['ninja', '-C', str(dest / 'build'), '-j6', str(library)],
                       env=env, stdout=log, stderr=log, check=True)
    output = build / 'userland/obj/libredwoodair.dylib'
    shutil.copy2(dest / 'build' / library, output)
    subprocess.run([str(args.ldid), '-Cadhoc', '-S', str(output)], check=True)
    shutil.copy2(output, build / 'prebuilt/userland/libredwoodair.dylib')
    (args.work / 'air-rebuilt.json').write_text(json.dumps({
        'mesa_version': '26.2.5', 'private_source_copy': True,
        'applied_patch': patch.name, 'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
        'current_source_overlays': ['tools/mesa/air', 'tools/mesa/redwood-drm'],
        'library_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'library_bytes': output.stat().st_size, 'hardware_tested': False}, indent=2) + '\n')
    print('AIR rebuilt and signed with patch 0016 applied.', flush=True)


if __name__ == '__main__':
    main()
