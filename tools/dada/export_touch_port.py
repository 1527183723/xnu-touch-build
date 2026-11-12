#!/usr/bin/env python3
"""Freeze the checked native TouchCom port as a reviewable, optional source overlay."""
import argparse
import difflib
import hashlib
import json
import shutil
from pathlib import Path

from prepare_touch_kernel import FILES, ROOT, TESTS


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--donor', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    tree, out = args.work / 'xnu12377', args.output
    compile_report = json.loads((args.work / 'linux-touch-compile.json').read_text())
    assert compile_report['headers_and_objects_compiled']
    out.mkdir(parents=True, exist_ok=False)
    port = json.loads((args.work / 'touch-port.json').read_text())
    for entry in port['imported_files']:
        source = tree / entry['path']
        dest = out / 'files' / entry['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        entry['port_sha256'] = digest(source.read_bytes())

    replacements = {
        'bsd/conf/files': [('bsd/dev/dada_touch.c\tstandard\n'
                            'bsd/dev/dada_touch_tcm.c\tstandard\n', '')],
        'bsd/dev/redwood_input.c': [
            ('#include <sys/dada_touch.h>\n', ''),
            ('\tuint32_t dada_mode = 0;\n'
             '\tif (PE_parse_boot_argn("dada_touch", &dada_mode, sizeof(dada_mode)) && dada_mode) {\n'
             '\t\t/* Select one physical controller; never probe Goodix on TouchCom. */\n'
             '\t\tdada_touch_init();\n'
             '\t} else {\n\t\tredwood_goodix_init();\n\t}', '\tredwood_goodix_init();')],
        'osfmk/arm64/machine_routines.c': [
            ('extern void dada_touch_stop(void);\n', ''),
            ('\tdada_touch_stop();\n', '')],
    }
    patch = []
    integration = {}
    for name, edits in replacements.items():
        after = (tree / name).read_text()
        before = after
        for added, original in edits:
            assert before.count(added) == 1, name
            before = before.replace(added, original)
        if name == 'bsd/dev/redwood_input.c':
            assert before == (ROOT / 'xnu/files' / name).read_text()
        patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile='a/' + name, tofile='b/' + name))
        integration[name] = {'before_sha256': digest(before.encode()),
                             'after_sha256': digest(after.encode())}
    (out / 'integration.patch').write_text(''.join(patch))
    port['integration_changes'] = integration
    port['integration_patch_sha256'] = digest((out / 'integration.patch').read_bytes())
    port['compiled_objects_only'] = True
    port['kernel_linked'] = False
    port['compile_sdk_limitation'] = 'Cached 16.5 SDK; current XNU12377 headers; not a release kernel'
    port['boot_arguments'] = 'dada_touch=4 dada_touch_mmio=1 dada_touch_power=1 dada_touch_reset=1'
    (out / 'manifest.json').write_text(json.dumps(port, indent=2) + '\n')
    tests = out / 'tests'
    tests.mkdir()
    for name in TESTS:
        shutil.copy2(args.donor / (name + '.c'), tests / (name + '.c'))
    for name in ('touch-tests.log', 'touch-object-build.log', 'linux-touch-compile.json'):
        shutil.copy2(args.work / name, out / name)
    print('Exported optional TouchCom source overlay:', out)


if __name__ == '__main__':
    main()
