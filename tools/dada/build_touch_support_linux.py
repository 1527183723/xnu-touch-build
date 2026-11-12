#!/usr/bin/env python3
"""Build real arm64 firehose/pthread support from XNU26's matching OSS releases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import urllib.request

DEFAULT = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--work', type=Path, default=DEFAULT)
    args = ap.parse_args()
    work = args.work
    tree, deps = work / 'xnu12377', work / 'deps'
    source, output = work / 'support-src', work / 'support-build'
    source.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    metadata = {}
    for name, version in [('libdispatch', '1542.100.32'), ('libpthread', '539.100.4')]:
        tag = name + '-' + version
        url = f'https://codeload.github.com/apple-oss-distributions/{name}/tar.gz/refs/tags/{tag}'
        archive = source / (tag + '.tar.gz')
        if not archive.exists():
            urllib.request.urlretrieve(url, archive)
        folder = source / name
        if not folder.exists():
            folder.mkdir()
            with tarfile.open(archive) as bundle:
                for member in bundle.getmembers():
                    parts = Path(member.name).parts[1:]
                    if not parts:
                        continue
                    assert '..' not in parts and not Path(*parts).is_absolute()
                    member.name = str(Path(*parts))
                    bundle.extract(member, folder, filter='data')
        metadata[name] = {'tag': tag, 'url': url, 'archive_sha256': sha(archive)}
        print('Source ready:', tag, flush=True)
    sdk = deps / 'sdk'
    headers = sdk / 'usr/local/include/kernel/os'
    command = [deps / 'host/bin/unifdef', '-DOS_FIREHOSE_SPI=1', '-DKERNEL=1',
               source / 'libdispatch/os/firehose_buffer_private.h']
    result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, check=False)
    assert result.returncode in (0, 1)
    private = headers / 'firehose_buffer_private.h'
    if private.read_bytes() != result.stdout:
        private.write_bytes(result.stdout)
    types = headers / 'firehose_types.defs'
    fresh = (source / 'libdispatch/src/firehose/firehose_types.defs').read_bytes()
    if types.read_bytes() != fresh:
        types.write_bytes(fresh)
    exported = tree / 'BUILD/obj/EXPORT_HDRS'
    kfw = tree / 'BUILD/dst/System/Library/Frameworks/Kernel.framework'
    inc = output / 'inc'
    inc.mkdir(exist_ok=True)
    shutil.copy2(Path('/home/LZY/hakios-arm64e-dada-kernel-20261031/repo/base/xnu-deps/build/pthread/inc/TargetConditionals.h'), inc)
    cc = [deps / 'host/bin/xnu-clang', '-arch', 'arm64', '-c', '-Os', '-mkernel',
          '-mgeneral-regs-only', '-nostdinc', '-fno-builtin', '-fno-common',
          '-DKERNEL=1', '-DKERNEL_PRIVATE=1', '-DPRIVATE=1', '-I' + str(inc)]
    env = dict(os.environ, XNU_SDK_VERSION='16.5')
    commands = []

    def run(command, log):
        command = [str(x) for x in command]
        commands.append(command)
        with (output / log).open('wb') as dest:
            subprocess.run(command, stdout=dest, stderr=dest, env=env, check=True)

    dispatch = source / 'libdispatch'
    firehose = output / 'firehose_buffer.o'
    run([*cc, '-Wno-packed', '-DDISPATCH_USE_DTRACE=0', '-DOS_ATOMIC_CONFIG_MEMORY_ORDER_DEPENDENCY=1',
         '-DOS_ATOMIC_CONFIG_STARVATION_FREE_ONLY=0', '-DOS_FIREHOSE_SPI=1',
         '-DARM64_BOARD_CONFIG_BCM2837', '-I' + str(dispatch), '-I' + str(dispatch / 'src/firehose'),
         '-I' + str(kfw / 'PrivateHeaders'), '-I' + str(kfw / 'Headers'),
         '-I' + str(tree / 'libkern/firehose'), '-I' + str(sdk / 'usr/local/include/kernel'),
         '-I' + str(tree / 'EXTERNAL_HEADERS'),
         *[flag for sub in ('osfmk', 'bsd', 'libkern') for flag in ('-idirafter', exported / sub)],
         '-o', firehose, dispatch / 'src/firehose/firehose_buffer.c'], 'firehose.log')
    pthread = source / 'libpthread'
    objects = []
    for part in ('kern_init', 'kern_support', 'kern_synch'):
        obj = output / ('pthread_' + part + '.o')
        objects.append(obj)
        run([*cc, '-Wno-int-conversion', '-D__PTHREAD_EXPOSE_INTERNALS__=1', '-D__DARWIN_64_BIT_INO_T=1',
             '-DARM64_BOARD_CONFIG_BCM2837', '-Dpthread_kern=pthread_kext_kern',
             '-Dcurrent_uthread=pthread_kext_current_uthread', '-I' + str(pthread),
             *['-I' + str(exported / sub) for sub in ('osfmk', 'bsd', 'libkern', 'pexpert', 'iokit', 'security')],
             '-I' + str(tree / 'EXTERNAL_HEADERS'), '-I' + str(pthread / 'include'),
             '-I' + str(pthread / 'private'), '-o', obj, pthread / 'kern' / (part + '.c')], part + '.log')
    for obj in [firehose, *objects]:
        assert struct.unpack_from('<3I', obj.read_bytes()) == (0xfeedfacf, 0x100000c, 0), obj
    lib = sdk / 'usr/local/lib/kernel'
    for name, members in [('libfirehose_kernel.a', [firehose]), ('libpthread_kext.a', objects)]:
        run(['llvm-ar-21', '--format=darwin', 'rcs', output / name, *members], name + '.log')
        shutil.copy2(output / name, lib / name)
    metadata.update(objects={p.name: sha(p) for p in [firehose, *objects]},
                    libraries={name: sha(lib / name) for name in ('libfirehose_kernel.a', 'libpthread_kext.a')},
                    commands=commands, architecture='arm64', rebuilt_against_current_xnu_headers=True)
    (work / 'support-build.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('Built real arm64 firehose and pthread support libraries.', flush=True)


if __name__ == '__main__':
    main()
