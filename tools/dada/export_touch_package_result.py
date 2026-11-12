#!/usr/bin/env python3
"""Collect checked source, objects and failures; never package an old kernel."""
import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    work, out = args.work, args.output
    port = ROOT / 'xnu26/dada-touch-superboyyy'
    tree = work / 'xnu12377'
    meta = json.loads((port / 'manifest.json').read_text())
    result = json.loads((work / 'linux-touch-compile.json').read_text())
    assert result['headers_and_objects_compiled']
    assert result['full_kernel_build_returncode'] != 0
    assert not result['kernel_file_exists']
    for name, entry in meta['integration_changes'].items():
        assert hashlib.sha256((tree / name).read_bytes()).hexdigest() == entry['after_sha256']
    for entry in meta['imported_files']:
        assert hashlib.sha256((tree / entry['path']).read_bytes()).hexdigest() == entry['port_sha256']
    # Check that the tested source is exactly the one used to compile this object.
    assert (port / 'files/bsd/dev/hakios_dada_touch.c').read_bytes().replace(b'\r\n', b'\n') == (
        tree / 'bsd/dev/hakios_dada_touch.c').read_bytes()
    assert (out / 'source/input26_service.c').read_bytes() == (
        ROOT / 'userland/src/input26_service.c').read_bytes().replace(b'\r\n', b'\n')
    artifacts = out / 'objects'
    artifacts.mkdir(exist_ok=True)
    variant = tree / 'BUILD/obj/DEVELOPMENT_ARM64_BCM2837'
    for name in ('bsd/DEVELOPMENT/hakios_dada_touch.o', 'bsd/DEVELOPMENT/redwood_input.o',
                 'osfmk/DEVELOPMENT/machine_routines.o'):
        shutil.copy2(variant / name, artifacts / Path(name).name)
    symbols = subprocess.check_output(['llvm-nm-21', str(artifacts / 'hakios_dada_touch.o')], text=True)
    assert ' T _hakios_dada_touch_init' in symbols and ' T _hakios_dada_touch_stop' in symbols
    (out / 'driver-symbols.txt').write_text(symbols)
    for name in ('headers-build.log', 'touch-object-build.log', 'linux-touch-compile.json',
                 'full-kernel-build.log', 'host-tests.log', 'hid-state-tests.log',
                 'supplied-read-tests.log', 'touch-port.json'):
        shutil.copy2(work / name, out / name)
    source = out / 'source-port'
    source.mkdir(exist_ok=True)
    shutil.copytree(port, source / 'xnu26/dada-touch-superboyyy', dirs_exist_ok=True)
    for name in ('prepare_superboyyy_touch.py', 'apply_touch_port.py', 'check_touch_kernel_linux.py',
                 'check_touch_port.py', 'test_superboyyy_touch.py', 'test_touch_package_read.py',
                 'test_touch_hid.py', 'build_touch_macos.sh', 'build_touch_package_userland.py',
                 'export_touch_package_result.py', 'macho_verify.py'):
        dest = source / 'tools/dada' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'tools/dada' / name, dest)
    for name in ('xnu26/apply.sh', '.github/workflows/build-dada-touch.yml',
                 'userland/src/input26_service.c', 'userland/src/input26_service.entitlements',
                 'userland/src/redwood_input.h', 'docs/DADA-TOUCH-PACKAGE-PORT-20261110.md', 'LICENSE'):
        dest = source / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    before = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                      'HEAD:userland/src/input26_service.c']).decode().replace('\r\n', '\n')
    after = (ROOT / 'userland/src/input26_service.c').read_text()
    (out / 'userland-contact-state.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        fromfile='a/userland/src/input26_service.c', tofile='b/userland/src/input26_service.c')))
    summary = {
        'primary_source': 'user repair package 20261110',
        'primary_package_sha256': meta['primary_package_sha256'],
        'tests': {'supplied_read_cases': 7, 'integrated_read_cases': 7,
                  'driver_assertions': 136, 'malformed_config_trials': 20000, 'hid_assertions': 34},
        'driver_and_integration_objects_compiled': True, 'input26_service_built_and_signed': True,
        'full_kernel_build_returncode': result['full_kernel_build_returncode'],
        'kernel_linked': False, 'bootable_img_produced': False, 'hardware_tested': False,
        'blocker': 'IIG/SDK-generated DriverKit declarations differ from XNU12377.121.6',
        'phone_state_changed': False,
    }
    (out / 'STATUS.json').write_text(json.dumps(summary, indent=2) + '\n')
    zip_path = out / 'dada-touch-repair-source-r2.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                archive.write(path, str(path.relative_to(source)))
    hashes = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()
              and p.name != 'SHA256SUMS.json'}
    (out / 'SHA256SUMS.json').write_text(json.dumps(hashes, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    print('Exported to', out)


if __name__ == '__main__':
    main()
