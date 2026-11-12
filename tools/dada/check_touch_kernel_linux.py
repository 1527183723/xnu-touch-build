#!/usr/bin/env python3
"""Export current XNU26 headers and compile the touch objects in an isolated Linux tree.

This is a compile check, not a replacement for a fully linked and tested kernel.
Cached SDK archives are not used to manufacture a distributable kernel.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--cache', type=Path, required=True)
    ap.add_argument('--attempt-kernel', action='store_true',
                    help='Also attempt a complete kernel build; logs and failures are retained')
    ap.add_argument('--external-object', type=Path,
                    help='Explicit compatible external driver object to link')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--reconfigure-bsd', action='store_true',
                    help='Regenerate the BSD object list after adding driver source files')
    args = ap.parse_args()
    work, cache = args.work, args.cache
    tree, deps = work / 'xnu12377', work / 'deps'
    meta = json.loads((work / 'touch-port.json').read_text())
    object_names = [Path(entry['path']).stem + '.o' for entry in meta['imported_files']
                    if entry['path'].endswith('.c')]
    assert object_names, 'No driver objects in source manifest'
    deps.mkdir(exist_ok=True)
    for name in ('host', 'hostinc'):
        if not (deps / name).exists():
            shutil.copytree(cache / 'repo/base/xnu-deps' / name, deps / name, symlinks=True)
    sdk = deps / 'sdk'
    if not sdk.exists():
        shutil.copytree(cache / 'repo/base/xnu-deps/sdk/iPhoneOS16.5.xnu.sdk', sdk, symlinks=True)
    for name in ('MacOSX26.0.sdk', 'iig-impl'):
        if not (deps / name).exists():
            (deps / name).symlink_to(cache / 'repo/base/xnu-deps' / name)
    # Public private-kernel headers previously fetched into the local cache.
    # Their provenance and hashes are recorded separately from the Apple base.
    copied = []
    import hashlib
    for name in ('TrustCache', 'CodeSignature', 'CoreEntitlements/V2'):
        src = cache / 'xnu12377/EXTERNAL_HEADERS' / name
        dest = sdk / 'usr/local/include/kernel' / name
        if not dest.exists():
            shutil.copytree(src, dest, symlinks=True)
        copied.extend({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in dest.rglob('*') if p.is_file())
    wrapper = deps / 'host/bin/xnu-clang'
    wrapper.write_text(re.sub(r'/usr/bin/\$drv(?:-21)*', '/usr/bin/$drv-21', wrapper.read_text()))
    env = dict(os.environ, PATH=str(deps / 'host/bin') + ':' + os.environ['PATH'],
               XNUDEPS=str(deps), XNU_SDK_PATH=str(sdk), XNU_SDK_VERSION='16.5',
               XNU_PLATFORM_PATH=str(deps / 'Platforms/iPhoneOS.platform'),
               RC_DARWIN_KERNEL_VERSION='25.6.0')
    hostcc = ('clang-21 -include ' + str(deps / 'hostinc/hostcompat.h') +
              ' -I' + str(deps / 'hostinc') + ' -Wno-unused-command-line-argument'
              ' -Wno-error=implicit-function-declaration')
    make = ['make', 'SDKROOT=iphoneos', 'ARCH_CONFIGS=ARM64', 'MACHINE_CONFIGS=BCM2837',
            'KERNEL_CONFIGS=DEVELOPMENT', 'ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG=arm64',
            'BUILD_WERROR=0', 'BUILD_LTO=0', 'DO_CTFMERGE=0', 'BUILD_DSYM=0',
            'HOST_CC=' + hostcc, 'HOST_FLEX=flex', 'HOST_BISON=bison', 'HOST_GM4=m4',
            'CC=xnu-clang', 'CXX=xnu-clang++', 'LOGCOLORS=n', 'MAKEJOBS=--jobs=3',
            'KEXT_CREATE_SYMBOL_SET=/usr/bin/true']
    if args.external_object:
        assert args.external_object.is_file(), args.external_object
        make.append('REDWOOD_OBJECTS=' + str(args.external_object.resolve()))
    if args.verbose:
        make.append('VERBOSE=YES')
    with (work / 'headers-build.log').open('wb') as log:
        subprocess.run([*make, 'exporthdrs', 'installhdrs'], cwd=tree, env=env,
                       stdout=log, stderr=log, check=True)
    # Ask make to show the real BSD compile command rather than guessing the
    # XNU12377 include order, generated configuration and type definitions.
    obj = tree / 'BUILD/obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT'
    if args.reconfigure_bsd or not (obj / 'Makefile').exists():
        obj.mkdir(parents=True, exist_ok=True)
        conf_env = dict(env, SRCROOT=str(tree), OBJROOT=str(tree / 'BUILD/obj'),
                        SOURCE=str(tree / 'bsd/conf') + '/')
        with (work / 'bsd-config.log').open('wb') as log:
            subprocess.run([str(tree / 'SETUP/config/doconf'), '-c', '-cpu', 'arm64',
                            '-soc', 'bcm2837', '-platform', 'BCM2837', '-d', str(obj),
                            '-s', str(tree / 'bsd/conf'), '-m', str(tree / 'config'),
                            'DEVELOPMENT'], cwd=obj, env=conf_env,
                           stdout=log, stderr=log, check=True)
    env.update(SRCROOT=str(tree), OBJROOT=str(tree / 'BUILD/obj'),
               DSTROOT=str(tree / 'BUILD/dst'), SYMROOT=str(tree / 'BUILD/sym'),
               CURRENT_KERNEL_CONFIG='DEVELOPMENT', CURRENT_ARCH_CONFIG='ARM64',
               CURRENT_MACHINE_CONFIG='BCM2837', COMPONENT='bsd',
               SOURCE=str(tree / 'bsd/conf') + '/')
    with (work / 'touch-object-build.log').open('wb') as log:
        subprocess.run([*make, '-C', str(obj), 'COMPONENT=bsd',
                        *object_names],
                       cwd=tree, env=env, stdout=log, stderr=log, check=True)
    report = {
        'headers_and_objects_compiled': True, 'kernel_linked': False,
        'cached_sdk_for_compile_check_only': str(sdk), 'private_headers': copied,
        'driver_objects': object_names, 'hardware_tested': False}
    if args.attempt_kernel:
        with (work / 'full-kernel-build.log').open('wb') as log:
            result = subprocess.run([*make, '-j6', '-k', 'all'], cwd=tree, env=env,
                                    stdout=log, stderr=log)
        report['full_kernel_build_returncode'] = result.returncode
        report['full_kernel_log'] = str(work / 'full-kernel-build.log')
        kernel = tree / 'BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837'
        report['kernel_file_exists'] = kernel.is_file()
        report['kernel_linked'] = kernel.is_file()
        # The cached SDK/IIG fallback is not a release toolchain even if it links.
        report['release_kernel_produced'] = False
        report['external_object'] = str(args.external_object) if args.external_object else None
    (work / 'linux-touch-compile.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'private_headers'}, indent=2))


if __name__ == '__main__':
    main()
