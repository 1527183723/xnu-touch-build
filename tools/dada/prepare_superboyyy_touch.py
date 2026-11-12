#!/usr/bin/env python3
"""Prepare a fresh Warsaw tree and freeze the touch-only superboyyy integration."""
import argparse
import difflib
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLE = 'ac9718fb1af618d5ce8678d0dc6e8a58f252216f'
COMMIT = 'f1ae906fcc43ba8f763663257724ba938fb820ab'
DONOR_SHA = '381ede889f0746ea81a28bca072ceb23b83274cc6aecb80d7dd969bb799109b3'
PORT = ROOT / 'xnu26/dada-touch-superboyyy'
DRIVER = 'bsd/dev/hakios_dada_touch.c'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apple-cache', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--repair-package', type=Path,
                    default=ROOT / 'out/dada-touch-package-review-20261110')
    args = ap.parse_args()
    work, tree = args.work, args.work / 'xnu12377'
    package = args.repair_package
    entries = json.loads((package / 'archive-entries.json').read_text(encoding='utf-8'))
    expected = json.loads((package / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    for entry in entries:
        if entry['archive_name'] in expected:
            assert digest((package / entry['saved_name']).read_bytes()) == expected[entry['archive_name']]
    donor = package / 'source/base/xnu/files' / DRIVER
    primary_sha = digest(donor.read_bytes().replace(b'\r\n', b'\n'))
    work.mkdir(parents=True, exist_ok=False)
    tree.mkdir()
    archive = work / 'apple-source.tar'
    with archive.open('wb') as output:
        subprocess.run(['git', '-C', str(args.apple_cache), 'archive', APPLE], stdout=output, check=True)
    subprocess.run(['tar', '-xf', str(archive), '-C', str(tree)], check=True)
    subprocess.run(['git', 'init', '-q', str(tree)], check=True)
    objects = subprocess.check_output(['git', '-C', str(args.apple_cache), 'rev-parse',
                                       '--git-path', 'objects'], text=True).strip()
    (tree / '.git/objects/info/alternates').write_text(str((args.apple_cache / objects).resolve()) + '\n')
    subprocess.run(['git', '-C', str(tree), 'update-ref', 'HEAD', APPLE], check=True)
    pieces = (ROOT / 'xnu26/patches/series').read_text().splitlines()
    for piece in pieces:
        if piece.strip():
            subprocess.run(['git', '-C', str(tree), 'apply', '--whitespace=nowarn',
                            str(ROOT / 'xnu26/patches' / piece)], check=True)
    skip = set((ROOT / 'xnu26/files.skip').read_text().splitlines())
    for folder in ('xnu/files', 'xnu26/files'):
        base = ROOT / folder
        for source in base.rglob('*'):
            if not source.is_file():
                continue
            relative = source.relative_to(base)
            if folder == 'xnu/files' and relative.as_posix() in skip:
                continue
            dest = tree / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
            dest.chmod(source.stat().st_mode)
    before = {}
    after = {}

    def edit(name, original, replacement):
        path = tree / name
        text = path.read_text()
        assert text.count(original) == 1, (name, original)
        before.setdefault(name, text)
        after[name] = text.replace(original, replacement)
        write(path, after[name])

    edit('bsd/conf/files', 'bsd/dev/redwood_goodix.c\tstandard',
         'bsd/dev/redwood_goodix.c\tstandard\n' + DRIVER + '\tstandard')
    edit('bsd/sys/redwood_input.h', 'void redwood_goodix_init(void);',
         'void hakios_dada_touch_init(void);\n'
         'bool hakios_dada_touch_stop(void);\nvoid redwood_goodix_init(void);')
    edit('bsd/dev/redwood_input.c', '\tredwood_goodix_init();',
         '\tuint32_t dada_tcm = 0;\n'
         '\tif (PE_parse_boot_argn("dadatouch", &dada_tcm, sizeof(dada_tcm)) && dada_tcm) {\n'
         '\t\t/* One physical producer: dada uses Synaptics, never probe Goodix. */\n'
         '\t\thakios_dada_touch_init();\n'
         '\t} else {\n\t\tredwood_goodix_init();\n\t}')
    edit('bsd/dev/redwood_input.c', '\tredwood_adreno_stop();\n\tloader_return();',
         '\tif (!hakios_dada_touch_stop()) {\n\t\treturn;\n\t}\n'
         '\tredwood_adreno_stop();\n\tloader_return();')
    edit('bsd/dev/redwood_input.c', '\texit_call = thread_call_allocate(exit_hold_elapsed, NULL);',
         '\tuint32_t dada_no_return = 0;\n'
         '\t/* No five-finger return during normal dada use. */\n'
         '\tPE_parse_boot_argn("dadatouch", &dada_no_return, sizeof(dada_no_return));\n'
         '\tif (!dada_no_return) {\n'
         '\t\texit_call = thread_call_allocate(exit_hold_elapsed, NULL);\n\t}')
    edit('osfmk/arm64/machine_routines.c', 'extern void redwood_adreno_stop(void);',
         'extern void redwood_adreno_stop(void);\nextern bool hakios_dada_touch_stop(void);')
    edit('osfmk/arm64/machine_routines.c', '\tredwood_var_save_on_return();',
         '\tif (!hakios_dada_touch_stop()) {\n'
         '\t\tpanic("dada TouchCom did not quiesce before halt");\n\t}\n'
         '\tredwood_var_save_on_return();')
    patch = ''.join(''.join(difflib.unified_diff(before[name].splitlines(True), after[name].splitlines(True),
                    fromfile='a/' + name, tofile='b/' + name)) for name in before)
    write(PORT / 'integration.patch', patch)
    port_source = (PORT / 'files' / DRIVER).read_bytes().replace(b'\r\n', b'\n')
    dest = tree / DRIVER
    dest.write_bytes(port_source)
    imported = [{'path': DRIVER, 'frozen_sha256': primary_sha, 'port_sha256': digest(port_source)}]
    meta = {
        'name': 'dada TouchCom repair package 20261110 (superboyyy origin)', 'apple_base': APPLE,
        'primary_donor': str(package / 'source/base/xnu/files' / DRIVER),
        'primary_package_sha256': (package / 'archive-sha256.txt').read_text().strip(),
        'upstream_driver_sha256': DONOR_SHA,
        'donor_repository': 'https://github.com/superboyyy/hakios-arm64e-dada',
        'donor_commit': COMMIT, 'license': 'APSL-2.0', 'imported_files': imported,
        'integration_changes': {name: {'before_sha256': digest(before[name].encode()),
                               'after_sha256': digest(after[name].encode())} for name in before},
        'integration_patch_sha256': digest(patch.encode()),
        'boot_arguments': 'dadatouch=1 touch_poll_us=4000',
        'synthetic_autoswipe_included': False, 'five_finger_return_on_dada': False,
        'input_queue_abi_unchanged': True, 'warsaw_gpu_sources_unchanged': True,
        'kernel_linked': False, 'hardware_tested': False,
    }
    write(PORT / 'manifest.json', json.dumps(meta, indent=2) + '\n')
    write(work / 'touch-port.json', json.dumps(meta, indent=2) + '\n')
    print('Prepared fresh Warsaw + superboyyy TouchCom:', tree)


if __name__ == '__main__':
    main()
