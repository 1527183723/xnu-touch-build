#!/usr/bin/env python3
"""Prepare an independent Linux build from pinned userland plus warsaw overlays."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path('/home/LZY/warsaw-ios26-20261110')
BUILD = WORK / 'build'
DOWNLOADS = ROOT / 'out/ios26-r1/downloads'


def extract(name, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(DOWNLOADS / name) as archive:
        members = archive.getmembers()
        top = members[0].name.split('/')[0]
        archive.extractall(destination, filter='data')
    return destination / top


def main():
    if (WORK / 'sources-prepared.json').exists():
        print('Independent source snapshot already prepared.')
        return
    if BUILD.exists():
        raise FileExistsError(BUILD)
    extracted = extract('userland-source.tar.gz', WORK / 'dependencies/userland')
    shutil.copytree(extracted, BUILD, symlinks=True)
    source_files = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--stage', '-z']).split(b'\0')
    for row in source_files:
        if not row:
            continue
        metadata, raw = row.split(b'\t', 1)
        name = raw.decode()
        src, target = ROOT / name, BUILD / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        shutil.copyfile(src, target)
        target.chmod(0o755 if metadata.startswith(b'100755 ') else 0o644)
    artifact_dirs = {}
    for kind in ('kernel', 'metal', 'air'):
        artifact_dirs[kind] = extract(f'{kind}-artifacts.tar.gz', WORK / f'dependencies/{kind}')
    for name in ('init', 'child', 'stage0', 'var_store', 'hello'):
        source = BUILD / 'prebuilt/userland' / name
        if not source.exists():
            continue
        target = BUILD / 'userland/obj' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755)
    for source in artifact_dirs['metal'].iterdir():
        if source.name == 'SHA256SUMS':
            continue
        shutil.copy2(source, BUILD / 'userland/obj' / source.name)
    checksums = (artifact_dirs['metal'] / 'SHA256SUMS').read_text().splitlines()
    for row in checksums:
        wanted, name = row.split()
        actual = hashlib.sha256((artifact_dirs['metal'] / name).read_bytes()).hexdigest()
        assert actual == wanted, name
    air = artifact_dirs['air'] / 'libredwoodair.dylib'
    assert hashlib.sha256(air.read_bytes()).hexdigest() == (artifact_dirs['air'] / 'libredwoodair.sha256').read_text().split()[0]
    shutil.copy2(air, BUILD / 'userland/obj/libredwoodair.dylib')
    kernel = artifact_dirs['kernel'] / 'kernel-ath12k.bin'
    assert hashlib.sha256(kernel.read_bytes()).hexdigest() == (artifact_dirs['kernel'] / 'kernel-ath12k.sha256').read_text().split()[0]
    shutil.copy2(kernel, BUILD / 'prebuilt/kernel-ath12k.bin')
    # Vendor bytes are independent inputs, not reused driver code.
    vendor = Path('/mnt/e/2026/AI/ios26/firmware/firmware-gpu')
    if vendor.exists():
        shutil.copytree(vendor, BUILD / 'firmware-gen8', dirs_exist_ok=True)
    prepared = {'kernel_source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                'userland_commit': '6313d6ab17cbe79abde19096902c980fb4dd6779',
                'tracked_overlay_files': sum(bool(row) for row in source_files),
                'official_artifacts_sha256_verified': True,
                'artifact_commits': {kind: json.loads((DOWNLOADS / f'{kind}-artifacts.tar.gz.json').read_text())['git_commit']
                                     for kind in artifact_dirs}}
    (WORK / 'sources-prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
    print(json.dumps(prepared, indent=2), flush=True)


if __name__ == '__main__':
    main()
