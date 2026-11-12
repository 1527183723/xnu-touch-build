#!/usr/bin/env python3
"""Build an isolated SpringBoard XPC lifetime fix for the dada Wi-Fi test."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from macho_verify import inspect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'out/dada-app-return-wifi-r2-20261111'
BASE = ROOT / 'out/dada-language-native-r3-20261111/source'
R1 = ROOT / 'out/dada-app-return-wifi-r1-20261111'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_source(text):
    logger_start = text.index('static void\nsb26_crash_logger(')
    logger_end = text.index('/* ---------------------------------------------------------------------------------------', logger_start)
    text = text[:logger_start] + '#include "sb26_crash_record.inc"\n\n' + text[logger_end:]

    cache_start = text.index('#define SB26_XPC_CACHE_MAX 64')
    key_start = text.index('static void\nsb26_xpc_cache_key(', cache_start)
    key_end = text.index('\nstatic xpc_object_t\nsb26_xpc_cache_get(', key_start)
    dead_comment = text.index('/* ---------------------------------------------------------------------------------------\n * v84:', key_end)
    key_function = text[key_start:key_end]
    text = text[:cache_start] + '#include "inject_sb26_xpc_compat.inc"\n\n' + key_function + '\n\n' + text[dead_comment:]

    hook_start = text.index('sb26_hook_xpc_sync(')
    icons_start = text.index('    if (is_icons) {', hook_start)
    services_else = text.index('    } else {\n        sb26_xpc_depth++;', icons_start)
    services_else_end = services_else + len('    } else {')
    icons_branch = '''    if (is_icons) {
        char key[72];
        sb26_xpc_cache_key(message, key, sizeof(key));
        reply = sb26_xpc_cache_get(key);
        if (reply) {
            fprintf(stderr, "sb26 xpc sync #%u CACHEHIT name=%s key=%.24s\\n", n, name, key);
            goto done_icons;
        }

        struct sb26_xpc_reply_wait *wait = sb26_xpc_reply_wait_create();
        if (!wait) {
            int cached = 0;
            reply = sb26_xpc_cache_fallback(key, &cached);
            fprintf(stderr, "sb26 xpc sync #%u PENDINGLIMIT name=%s key=%.24s cached=%d\\n",
                n, name, key, cached);
            goto done_icons;
        }
        xpc_connection_send_message_with_reply(connection, message,
            dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0),
            ^(xpc_object_t rep) {
                sb26_xpc_reply_wait_complete(wait, rep);
            });
        if (dispatch_semaphore_wait(wait->done,
                dispatch_time(DISPATCH_TIME_NOW, 300LL * NSEC_PER_MSEC)) == 0) {
            reply = sb26_xpc_reply_wait_take(wait);
            if (reply) {
                sb26_xpc_cache_put(key, reply);
            } else {
                int cached = 0;
                reply = sb26_xpc_cache_fallback(key, &cached);
                fprintf(stderr, "sb26 xpc sync #%u NULLREPLY name=%s key=%.24s cached=%d\\n",
                    n, name, key, cached);
            }
        } else {
            int cached = 0;
            reply = sb26_xpc_cache_fallback(key, &cached);
            fprintf(stderr, "sb26 xpc sync #%u TIMEOUT name=%s key=%.24s cached=%d\\n",
                n, name, key, cached);
        }
        sb26_xpc_reply_wait_release(wait);
done_icons:
        ;
    } else {'''
    text = text[:icons_start] + icons_branch + text[services_else_end:]
    return text


def main():
    out = OUT / 'sb-xpc-r2'
    source = out / 'source'
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(BASE, source, symlinks=True)
    inject = source / 'inject_sb26.c'
    original = inject.read_text()
    updated = patch_source(original)
    inject.write_text(updated)
    shutil.copy2(ROOT / 'tools/dada/sb26_crash_record.inc', source)
    shutil.copy2(ROOT / 'userland/src/inject_sb26_xpc_compat.inc', source)
    (out / 'inject_sb26.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), updated.splitlines(True),
        fromfile='a/inject_sb26.c', tofile='b/inject_sb26.c')))

    command_file = ROOT / 'out/dada-native-glass-r1-20261111/sb26-compile.command.json'
    command = json.loads(command_file.read_text())
    old_source = '/home/LZY/warsaw-native-glass-r1-20261111/source/'
    command = [str(source / Path(arg).name) if arg.startswith(old_source) else arg
        for arg in command]
    command[command.index('-o') + 1] = str(out / 'libredwood-sb26.dylib')
    (out / 'compile.command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'compile.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)

    clang = '/home/LZY/warsaw-dada-native-metal-ios26-20261110/clang'
    helper = out / 'session-icon-library'
    subprocess.run([clang, '-target', 'arm64e-apple-ios26.0', '-isysroot',
        '/home/LZY/dada-ios26-k90auto-20261106/sdk/iPhoneOS27.1.sdk', '-O2',
        '-Wl,-adhoc_codesign', str(ROOT / 'tools/dada/session_icon_library.c'),
        '-o', str(helper)], check=True)
    (out / 'build.json').write_text(json.dumps({
        'library': inspect(out / 'libredwood-sb26.dylib'),
        'helper': inspect(helper),
        'source_base': str(BASE),
        'cache_reads_owned': True,
        'async_callback_owns_wait_state': True,
        'pending_requests_bounded': 64,
        'chinese_preserved': True,
        'gpu_changed': False,
        'touch_changed': False,
        'wifi_hardware_tested': False,
    }, indent=2) + '\n')
    print('PASS XPC lifetime-fix SpringBoard library and session helper', flush=True)


if __name__ == '__main__':
    main()
