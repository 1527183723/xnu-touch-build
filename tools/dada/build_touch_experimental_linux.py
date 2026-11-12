#!/usr/bin/env python3
"""Build the package/superboyyy TouchCom port with explicit Linux IIG fallbacks.

This is an experimental kernel build. DriverKit user-driver RPCs without a
matching kernel implementation remain unsupported. The strict R2 is preserved.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110')
BASE = Path('/home/LZY/warsaw-dada-touch-package-r2-20261110')
CACHE = Path('/home/LZY/hakios-arm64e-dada-kernel-20261031')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def prepare(work):
    work.mkdir(parents=True, exist_ok=False)
    tree = work / 'xnu12377'
    shutil.copytree(BASE / 'xnu12377', tree, symlinks=True,
                    ignore=shutil.ignore_patterns('BUILD'))
    shutil.copy2(BASE / 'touch-port.json', work / 'touch-port.json')
    deps = work / 'deps'
    shutil.copytree(BASE / 'deps', deps, symlinks=True)
    # Never write through the shared cached implementation symlink.
    impl = deps / 'iig-impl'
    original = impl.resolve()
    impl.unlink()
    shutil.copytree(original, impl)
    changes = []

    def replace(name, old, new):
        path = tree / name
        before = path.read_text()
        assert before.count(old) == 1, (name, old)
        after = before.replace(old, new)
        path.write_text(after)
        changes.append((name, before, after))

    replace('libkern/libkern/c++/OSData.h',
            'static_assert(KALLOC_TYPE_IS_DATA_ONLY(T) || (acquisition == kValueNoCopy),',
            'static_assert(XNU_NO_TYPE_BUILTINS || KALLOC_TYPE_IS_DATA_ONLY(T) || (acquisition == kValueNoCopy),')
    replace('iokit/IOKit/IOUserServer.h',
            '\tOSDeclareDefaultStructorsWithDispatch(IOUserServer);',
            '\tOSDeclareDefaultStructorsWithDispatch(IOUserServer);\n'
            '\t// Compatibility declaration absent from the cached generated header.\n'
            '\tkern_return_t Panic_Impl(const char * reason);')
    replace('iokit/Kernel/IOUserServer.cpp',
            'IOUserServer::Panic_Impl(const char * reason)\n{',
            'IOUserServer::Panic_Impl(const char * reason)\n{\n'
            '\tconstexpr size_t kIOUserServrMaxPanicReasonLength = 1024;')
    replace('iokit/conf/files',
            './Tests/TestServices/TestDextRematch.iig.cpp optional iokitcpp\n'
            'iokit/Tests/TestServices/TestDextRematchKernel.cpp optional iokitcpp\n',
            '# Linux experimental: omit the DriverKit rematch self-test requiring real IIG.\n')
    patch = ''.join(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        fromfile='a/' + name, tofile='b/' + name)) for name, before, after in changes)
    (work / 'linux-iig-compat.patch').write_text(patch)
    save(work / 'experimental-build.json', {
        'base_touch_work': str(BASE), 'compatibility_cache': str(CACHE),
        'touch_source': json.loads((work / 'touch-port.json').read_text()),
        'compatibility_changes': [name for name, _, _ in changes],
        'compatibility_patch_sha256': sha(work / 'linux-iig-compat.patch'),
        'cached_iig_files': {p.name: sha(p) for p in impl.iterdir() if p.is_file()},
        'limitations': [
            'Uses cached generated DriverKit headers rather than genuine IIG output.',
            'Kernel methods with matching implementations use direct forwarding wrappers.',
            'Other generated user-driver RPC methods return kIOReturnUnsupported.',
            'DriverKit rematch self-tests omitted; Apple allocation type builtin unavailable.',
            'Experimental Linux SDK compatibility build; hardware validation pending.'
        ], 'kernel_linked': False, 'hardware_tested': False})
    print('Prepared isolated experimental tree:', tree, flush=True)


def build(work):
    if not (work / 'support-build.json').exists():
        subprocess.run([sys.executable, ROOT / 'tools/dada/check_touch_kernel_linux.py',
                        '--work', work, '--cache', CACHE], check=True)
        subprocess.run([sys.executable, ROOT / 'tools/dada/build_touch_support_linux.py',
                        '--work', work], check=True)
    report = json.loads((work / 'experimental-build.json').read_text())
    report['ath12k_object'] = {'linked': False, 'reason': 'Cached object is arm64e; never mix into arm64 kernel.'}
    if 'External native Wi-Fi object omitted from this touch experiment (cached arm64e object incompatible).' not in report['limitations']:
        report['limitations'].append('External native Wi-Fi object omitted from this touch experiment (cached arm64e object incompatible).')
    if (work / 'support-build.json').exists():
        report['rebuilt_support_libraries'] = json.loads((work / 'support-build.json').read_text())
    save(work / 'experimental-build.json', report)
    subprocess.run([sys.executable, ROOT / 'tools/dada/check_touch_kernel_linux.py',
                    '--work', work, '--cache', CACHE, '--attempt-kernel',
                    '--verbose'], check=True)
    report = json.loads((work / 'experimental-build.json').read_text())
    report['compile_result'] = json.loads((work / 'linux-touch-compile.json').read_text())
    report['kernel_linked'] = report['compile_result']['kernel_linked']
    save(work / 'experimental-build.json', report)


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('stage', choices=['prepare', 'build'])
    ap.add_argument('--work', type=Path, default=DEFAULT)
    args = ap.parse_args()
    (prepare if args.stage == 'prepare' else build)(args.work)


if __name__ == '__main__':
    main()
