#!/usr/bin/env python3
"""Run the original warsaw APFS recipe with this run's compiled fixes."""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

from macho_verify import inspect


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        while data := source.read(8 << 20):
            h.update(data)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--cached-workspace', type=Path, required=True)
    args = ap.parse_args()
    work, build = args.work, args.work / 'build'
    for name in ('sources-prepared', 'userland-compiled', 'air-rebuilt', 'extraction-complete'):
        assert (work / (name + '.json')).is_file(), f'Unfinished prerequisite: {name}'
    if (work / 'images-staged.json').exists():
        raise FileExistsError('This run already has completed images; use a separate run for changes')
    userland, ios = build / 'userland', build / 'ios26'
    assert (ios / 'var.skel').stat().st_size > 1000
    air = userland / 'obj/libredwoodair.dylib'
    assert digest(air) == json.loads((work / 'air-rebuilt.json').read_text())['library_sha256']
    # Independent vendor inputs only. No code from older dada driver ports is imported.
    gpu_names = ('gen80000_aqe.fw', 'gen80000_gmu.bin', 'gen80000_sqe.fw', 'gen80000_zap.mbn')
    (build / 'fw-gpu').mkdir(exist_ok=True)
    firmware = {}
    for name in gpu_names:
        src = build / 'firmware-gen8' / name
        assert src.is_file(), f'Missing vendor GPU input: {name}'
        shutil.copy2(src, build / 'fw-gpu' / name)
        firmware[name] = {'bytes': src.stat().st_size, 'sha256': digest(src)}
    # rc.metal is described in the upstream guide but absent from its Git tree.
    # These are the two documented commands, in init's command-list syntax.
    rc = userland / 'rc.dada-display.metal'
    rc.write_text('/redwood/bin/metal_props on\nexec /sbin/launchd.ios\n')
    rc.chmod(0o755)
    for name in ('rc.jobs', 'var.skel.extra'):
        p = userland / name
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
    # The public warsaw snapshot names private probe jobs it does not publish.
    # Preserve published jobs; do not invent unseen diagnostic programs or options.
    recipe = userland / 'build.sh'
    original = 'cp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.probe.plist com.redwood.probe-m.plist com.redwood.probe2.plist com.redwood.rwsh.plist root/LaunchDaemons/'
    fixed = ('for job in com.redwood.demo.plist com.redwood.wifi.plist com.redwood.probe.plist '
             'com.redwood.probe-m.plist com.redwood.probe2.plist com.redwood.rwsh.plist; do\n'
             '\t\tif [ -f "$job" ]; then cp "$job" root/LaunchDaemons/; '
             'else echo "build.sh: unpublished diagnostic job absent: $job"; fi\n\tdone')
    text = recipe.read_text()
    assert original in text or fixed in text
    recipe.write_text(text.replace(original, fixed))
    remote = userland / 'com.redwood.rwsh.plist'
    if not remote.exists():
        remote.write_bytes(plistlib.dumps({
            'Label': 'com.redwood.rwsh', 'ProgramArguments': ['/redwood/bin/rwsh'],
            'RunAtLoad': True, 'KeepAlive': True, 'UserName': 'root',
            'StandardOutPath': '/dev/console', 'StandardErrorPath': '/dev/console'}))
    wallpaper = userland / 'tools/mkwallpaper.py'
    wallpaper.write_text(wallpaper.read_text().replace('W, H = 1280, 2772', 'W, H = 1200, 2670'))
    (work / 'host-staging-fixes.json').write_text(json.dumps({
        'unpublished_optional_probe_jobs': ['com.redwood.probe.plist', 'com.redwood.probe-m.plist', 'com.redwood.probe2.plist'],
        'optional_probe_copy_guard': True, 'rwsh_console_mailbox_job': True,
        'wallpaper_geometry': [1200, 2670], 'peripheral_service_selection_changed': False}, indent=2) + '\n')
    pinned = '1bd1c1c2140d13d1b6d2e03379316572bee70aa3'
    archive = Path(__file__).resolve().parents[2] / 'out/ios26-r1/downloads/apfsprogs-source.tar.gz'
    record = json.loads(archive.with_suffix('.gz.json').read_text())
    assert record['git_commit'] == pinned and record['sha256'] == digest(archive)
    checker = build / 'xnu-deps/build/apfsprogs/apfsck/apfsck'
    if not checker.is_file():
        dependency = work / 'dependencies/apfsprogs'
        dependency.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            top = tar.getmembers()[0].name.split('/')[0]
            tar.extractall(dependency, filter='data')
        shutil.copytree(dependency / top, checker.parents[1], symlinks=True)
        subprocess.run(['make', '-C', str(checker.parent)], check=True)
    env = dict(os.environ, IOS='../ios26', PREBUILT='1', SYSTEM='1', SHARE='1',
               CHECK='1', RC=rc.name, AIR_DYLIB='obj/libredwoodair.dylib',
               PYTHON_CPU_COUNT='4', TZ='Asia/Shanghai',
               PYTHONPATH=str(work / 'host-python'),
               PATH=str(args.cached_workspace / 'venv/bin') + ':' + os.environ['PATH'])
    for name in ('FLASHBOOT', 'GL', 'ADSP', 'STOREFRONT', 'COMPILE_ONLY', 'INJECT', 'JOBS', 'QUIET'):
        env.pop(name, None)
    print('Building root26.apfs and system26.apfs with the original warsaw build.sh', flush=True)
    with (work / 'apfs-build.log').open('wb') as log:
        subprocess.run(['bash', 'build.sh'], cwd=userland, env=env, stdout=log, stderr=log, check=True)
    root = userland / 'root'
    staged = sorted(userland.glob('sysroot.stale.*'), key=lambda p: p.stat().st_mtime_ns)[-1]
    assert (root / 'xpc/launchd.plist').read_bytes() == (staged / 'System/Library/xpc/launchd.plist').read_bytes()
    assert os.readlink(staged / 'System/Library/Extensions/RedwoodMetal.bundle') == '/redwood/lib/RedwoodMetal.bundle'
    assert (staged / 'sbin/launchd').read_bytes() == (userland / 'obj/stage0').read_bytes()
    assert (root / 'sbin/init').stat().st_mode & 0o111
    assert (root / 'sbin/launchd.ios').stat().st_mode & 0o111
    for name in ('rc', 'rc.jobs', 'var.skel', 'glass.conf', 'inject.conf'):
        assert b'\r' not in (root / 'etc' / name).read_bytes(), name
    for name in gpu_names:
        assert digest(root / 'fw' / name) == firmware[name]['sha256']
    assert digest(root / 'lib/libredwoodair.dylib') == digest(air)
    binaries = {}
    for base in ('lib', 'bin', 'sbin'):
        for p in (root / base).rglob('*'):
            if p.is_symlink() or not p.is_file():
                continue
            with p.open('rb') as stream:
                magic = stream.read(4)
            if magic != b'\xcf\xfa\xed\xfe':
                continue
            static = p.name in ('init', 'child', 'var_store')
            # Apple's original launchd keeps its platform signature; verify our rebuilt files.
            if p.name == 'launchd.ios':
                continue
            binaries[str(p.relative_to(root))] = inspect(p, require_arm64e=not static)
    staged_apps = sorted(p.name for p in (ios / 'rootfs/private/var/staged_system_apps').iterdir() if p.name.endswith('.app'))
    assert staged_apps
    for name in staged_apps:
        assert (staged / 'Applications' / name / 'Info.plist').is_file(), name
    cache_names = sorted(p.name for p in (ios / '23G90__iPhone18,3').iterdir())
    for name in cache_names:
        assert (staged / 'System/Cryptexes/OS/System/Library/Caches/com.apple.dyld' / name).stat().st_size == (ios / '23G90__iPhone18,3' / name).stat().st_size
    images = {}
    for name in ('root26.apfs', 'system26.apfs'):
        image = userland / name
        with image.open('rb') as stream:
            assert stream.read(36)[32:36] == b'NXSB'
        images[name] = {'bytes': image.stat().st_size, 'sha256': digest(image)}
    record = {'version': '26.6.2', 'build': '23G90', 'original_apfs_recipe': True,
              'apfsck_clean_and_full_kernel_reader_comparison': True,
              'fresh_firmware_extraction': True, 'staged_apps_relocated': staged_apps,
              'dynamic_helpers_signed_and_arm64e': binaries, 'vendor_gpu': firmware,
              'dyld_cache_files': cache_names, 'images': images,
              'physical_launchd_cache_identical': True, 'sysroot_for_review': str(staged),
              'launchd_jobs': sorted(p.name for p in (root / 'LaunchDaemons').glob('*.plist')),
              'no_additional_peripheral_services_disabled': True, 'hardware_tested': False}
    (work / 'images-staged.json').write_text(json.dumps(record, indent=2) + '\n')
    print('APFS images built, checked, and staged apps verified.', flush=True)


if __name__ == '__main__':
    main()
