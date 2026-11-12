#!/usr/bin/env python3
"""Build an isolated shared-buffer fix; do not edit the running root or GPU tree."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
WORK = Path('/home/LZY/warsaw-dada-native-metal-ios26-20261110')


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = WORK / 'source/gpu/nativemetal/NativeBridgeMetalFrontend.mm'
    original = source.read_text()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == '3fbf4fd563c41424170dbf49dca6f8fb8756ca87c8ceb8260a5d160c5fbf71a7'
    text = original

    def replace(before, after):
        nonlocal text
        assert text.count(before) == 1, before
        text = text.replace(before, after)

    replace('@property(nonatomic) void *mirror;',
            '@property(nonatomic) void *mirror;\n@property(nonatomic) BOOL mirrorUnavailable;')
    replace('@property(nonatomic,strong) NSMutableSet *resourceOwners;',
            '@property(nonatomic,strong) NSMutableSet *resourceOwners;\n'
            '@property(atomic,strong) NSArray<NBBuffer *> *mirrorOwners;')
    replace('@implementation NBEncoder\n',
            'static void waitForMirrorBuffer(NBBuffer *buffer) {\n'
            ' NSArray *pending;@synchronized(gInFlight){pending=gInFlight.allObjects;}\n'
            ' for(NBCommandBuffer *cb in pending) {\n'
            '  // GPU-done is insufficient: finish must publish before the next upload.\n'
            '  if([cb.mirrorOwners containsObject:buffer])[(id<MTLCommandBuffer>)cb waitUntilCompleted];\n'
            ' }\n}\n@implementation NBEncoder\n')
    replace('static void syncMirrors() {' + text.split('static void syncMirrors() {', 1)[1].split('\n}\n', 1)[0] + '\n}',
            'static std::atomic<uint64_t> gMirrorBytesPublished,gMirrorBuffersPublished;\n' +
            (ROOT / 'tools/dada/native_metal_mirror_coherence.inc').read_text())
    replace(' syncMirrors(); // Preserve commit-time coherence for all mirrored imports.',
            ' self.mirrorOwners=mirrorBuffers(_resourceOwners);\n'
            ' prepareMirrors(_resourceOwners); // Wait and refresh only this submission\'s mirrors.')
    replace(' flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too\n if(NBTexture *target=',
            ' flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too\n'
            ' if(!self.failedLocally && !self.encodeFailure)publishMirrors(_resourceOwners);\n'
            ' self.mirrorOwners=nil;\n'
            ' if(NBTexture *target=')
    replace('- (void *)virtualAddress {return nmb_buffer_contents(_handle);}',
            '- (void *)virtualAddress {return [self contents];}')
    replace(' if(!nmb_texture_replace(_handle,region,level,slice,bytes,row,image,length))NBLOG(@"native-metal: replaceRegion refused: %@",lastError());',
            ' NSSet *owners=_buffer.mirror?[NSSet setWithObject:self]:nil;\n'
            ' if(owners){waitForResource(self);prepareMirrors(owners);}\n'
            ' if(!nmb_texture_replace(_handle,region,level,slice,bytes,row,image,length))\n'
            '  NBLOG(@"native-metal: replaceRegion refused: %@",lastError());\n'
            ' else if(owners)publishMirrors(owners);')
    patched = out / source.name
    patched.write_text(text)
    (out / 'mirror-coherence.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile='a/nativemetal/NativeBridgeMetalFrontend.mm',
        tofile='b/nativemetal/NativeBridgeMetalFrontend.mm')))
    commands = []

    def run(name, command):
        command = list(map(str, command))
        commands.append(command)
        with (out / (name + '.log')).open('w') as log:
            r = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        if r.returncode:
            print((out / (name + '.log')).read_text(errors='replace')[-12000:], flush=True)
            raise SystemExit(r.returncode)
        print('PASS', name, flush=True)

    flags = json.loads((WORK / 'frontend-flags.json').read_text())
    obj = out / 'NativeBridgeMetalFrontend.o'
    run('compile', [WORK / 'clang++', *flags, '-c', patched, '-o', obj])
    command = json.loads((ROOT / 'out/dada-native-metal-ios26-r4/native-metal-link.command.json').read_text())
    command = [str(obj) if x == str(WORK / 'NativeBridgeMetalFrontend.o') else x for x in command]
    library = out / 'RedwoodMetal'
    command[command.index('-o') + 1] = str(library)
    run('link', command)
    run('strip', ['llvm-strip-21', '-x', library])
    run('install-name', ['llvm-install-name-tool-21', '-change',
        '/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit',
        '/System/Library/Frameworks/IOKit.framework/IOKit', library])
    run('sign', ['/home/LZY/dada-gpu-build-r1/theos/toolchain/linux/iphone/bin/ldid', '-S', '-Cadhoc', library])
    (out / 'build.json').write_text(json.dumps({
        'source': str(source), 'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'patched_sha256': hashlib.sha256(patched.read_bytes()).hexdigest(),
        'commands': commands, 'library': inspect(library),
        'hardware_tested': False, 'desktop_icons_fixed': False,
        'original_source_unchanged': source.read_text() == original,
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
