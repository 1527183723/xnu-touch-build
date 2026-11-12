#!/usr/bin/env python3
"""Prepare exact warsaw XNU 12377 plus the verified dada TouchCom producer."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLE = 'ac9718fb1af618d5ce8678d0dc6e8a58f252216f'
FILES = ('bsd/dev/dada_touch.c', 'bsd/dev/dada_touch_tcm.c',
         'bsd/sys/dada_touch.h', 'bsd/sys/dada_input_board.h',
         'bsd/sys/dada_power_probe.h', 'bsd/sys/dada_rpmh_vote.h',
         'bsd/sys/dada_spi_wire.h', 'bsd/sys/dada_touch_power_r6.h')
TESTS = ('test_input', 'test_touch_reports_r7', 'test_touch_config_r9',
         'test_touch_framing_r11', 'test_multitouch_r11')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apple-cache', type=Path, required=True)
    ap.add_argument('--donor', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    args = ap.parse_args()
    work, tree = args.work, args.work / 'xnu12377'
    work.mkdir(parents=True, exist_ok=False)
    tree.mkdir()
    archive = work / 'apple-source.tar'
    with archive.open('wb') as f:
        subprocess.run(['git', '-C', str(args.apple_cache), 'archive', APPLE], stdout=f, check=True)
    subprocess.run(['tar', '-xf', str(archive), '-C', str(tree)], check=True)
    subprocess.run(['git', 'init', '-q', str(tree)], check=True)
    # Archive the clean base without copying any previous local kernel patches.
    patches = (ROOT / 'xnu26/patches/series').read_text().splitlines()
    for patch in patches:
        if patch.strip():
            subprocess.run(['git', '-C', str(tree), 'apply', '--whitespace=nowarn',
                            str(ROOT / 'xnu26/patches' / patch)], check=True)
    skip = set((ROOT / 'xnu26/files.skip').read_text().splitlines())
    for directory in ('xnu/files', 'xnu26/files'):
        base = ROOT / directory
        for src in base.rglob('*'):
            if not src.is_file():
                continue
            relative = src.relative_to(base)
            if directory == 'xnu/files' and relative.as_posix() in skip:
                continue
            dest = tree / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes().replace(b'\r\n', b'\n'))
            dest.chmod(src.stat().st_mode)
    frozen = args.donor / 'out/r11-stock-framing'
    manifest = {x['path']: x['overlay_sha256']
                for x in json.loads((frozen / 'kernel-source-manifest.json').read_text())}
    imported = []
    for name in FILES:
        data = (frozen / 'build-source-snapshot' / name).read_bytes().replace(b'\r\n', b'\n')
        assert sha(data) == manifest[name], 'Frozen donor changed: ' + name
        original = data
        if name == 'bsd/dev/dada_touch.c':
            text = data.decode()
            # The port uses the native, physically verified mode 4. The old
            # unused firmware-ring mode referenced an unpublished ABI header.
            # Remove that mode rather than guessing its memory layout.
            text = text.replace('#if defined(XNU_TARGET_DADA)', '#if defined(__arm64__)')
            # XNU26 declares the preemption helpers in kern/cpu_data.h.
            # Keep the donor include; SPI transfers need those real helpers.
            text = text.replace('#include <sys/dada_touch_shared.h>\n', '')
            text = text.replace('extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned int);\n', '')
            begin = text.index('static volatile struct dada_touch_shared *firmware_touch;')
            end = text.index('/* Only registers which this driver changes', begin)
            text = text[:begin] + text[end:]
            begin = text.index('    if(mode==3) {', text.index('\ndada_touch_init(void)'))
            end = text.index('    if (mode > 6)', begin)
            text = text[:begin] + ('    if (mode == 3) {\n'
                '        printf("dada-touch: UEFI ring mode is not part of this native XNU26 port\\n");\n'
                '        return;\n    }\n') + text[end:]
            text = text.replace('    if(firmware_touch) {firmware_touch->stop=1;__asm__ volatile("dsb sy" ::: "memory");}\n', '')
            text = text.replace(' * dada_touch=3: consume UEFI\'s shared event ring; never access hardware MMIO.\n',
                                ' * Native-only XNU26 port: the unused UEFI ring ABI is not included.\n')
            text = text.replace('native 1/2/4, UEFI 3, read-only power 5, supply-only 6',
                                'native 1/2/4, read-only power 5, supply-only 6')
            data = text.encode()
        dest = tree / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        imported.append({'path': name, 'frozen_sha256': sha(original), 'port_sha256': sha(data)})
    changes = {}

    def edit(name, before, after):
        dest = tree / name
        text = dest.read_text()
        assert text.count(before) == 1, name
        dest.write_text(text.replace(before, after))
        changes[name] = {'before_sha256': sha(text.encode()), 'after_sha256': sha(dest.read_bytes())}

    edit('bsd/conf/files', 'bsd/dev/redwood_goodix.c\tstandard',
         'bsd/dev/redwood_goodix.c\tstandard\n'
         'bsd/dev/dada_touch.c\tstandard\nbsd/dev/dada_touch_tcm.c\tstandard')
    edit('bsd/dev/redwood_input.c', '#include <sys/redwood_input.h>',
         '#include <sys/redwood_input.h>\n#include <sys/dada_touch.h>')
    edit('bsd/dev/redwood_input.c', '\tredwood_goodix_init();',
         '\tuint32_t dada_mode = 0;\n'
         '\tif (PE_parse_boot_argn("dada_touch", &dada_mode, sizeof(dada_mode)) && dada_mode) {\n'
         '\t\t/* Select one physical controller; never probe Goodix on TouchCom. */\n'
         '\t\tdada_touch_init();\n'
         '\t} else {\n\t\tredwood_goodix_init();\n\t}')
    edit('osfmk/arm64/machine_routines.c', 'extern void redwood_adreno_stop(void);',
         'extern void redwood_adreno_stop(void);\nextern void dada_touch_stop(void);')
    edit('osfmk/arm64/machine_routines.c', '\tredwood_var_save_on_return();',
         '\tdada_touch_stop();\n\tredwood_var_save_on_return();')
    tests = work / 'tests'
    tests.mkdir()
    # Host tests must not search the complete kernel's bsd/sys directory: its
    # cdefs.h would override glibc's sys/cdefs.h while including stdint.h.
    (tests / 'include/sys').mkdir(parents=True)
    for name in FILES:
        if name.startswith('bsd/sys/'):
            shutil.copy2(tree / name, tests / 'include/sys' / Path(name).name)
    with (work / 'touch-tests.log').open('wb') as log:
        for name in TESTS:
            source = args.donor / (name + '.c')
            shutil.copy2(source, tests / source.name)
            binary = tests / name
            subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g', '-I', str(tests / 'include'),
                            str(tests / source.name), str(tree / 'bsd/dev/dada_touch_tcm.c'),
                            '-o', str(binary)], stdout=log, stderr=log, check=True)
            subprocess.run([str(binary)], stdout=log, stderr=log, check=True)
    (work / 'touch-port.json').write_text(json.dumps({
        'apple_base': APPLE, 'warsaw_patches': patches, 'donor_frozen': str(frozen),
        'imported_files': imported, 'integration_changes': changes,
        'native_mode': 4, 'uefi_ring_mode_included': False,
        'input_queue_abi_unchanged': True, 'ios26_hid_service_unchanged': True,
        'warsaw_gpu_sources_unchanged': True, 'tests': list(TESTS),
        'hardware_tested_with_xnu26': False,
    }, indent=2) + '\n')
    print((work / 'touch-tests.log').read_text(), flush=True)
    print('Touch-only XNU26 source prepared at', tree, flush=True)


if __name__ == '__main__':
    main()
