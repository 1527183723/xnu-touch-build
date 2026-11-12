#!/usr/bin/env python3
"""Compare donor Liquid Glass components against the sources used by touch r3.

This is a source/provenance audit, not a GPU pixel correctness test. Run in WSL.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT.parent / 'ios-dodge-dada/source/ios27/gpu'
DONOR = Path('/home/LZY/warsaw-superboyyy-gpu-r1-20261111/source/gpu')
ROLLBACK_ROOT = Path('/home/LZY/warsaw-dada-touch-experimental-r3-20261110/root')
OUTPUT = ROOT / 'out/dada-liquid-glass-audit-20261111'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'out/dada-native-metal-ios26-r4/source-manifest.json').read_text())
    checks = {
        'large_runtime_indexed_constants': (
            'metal2vulkan/crates/metal2vulkan/src/memory/plan.rs',
            ['PRIVATE_CONSTANT_BYTES', 'let big = u.indexed', 'ConstGlobalForm::Blob']),
        'constant_blob_pointer_gep': (
            'metal2vulkan/crates/metal2vulkan/src/lower/access/blob.rs',
            ['fn blob_const_offset(', 'ConstExpr::Gep', 'gep_displacement']),
        'single_linked_custom_effect_function': (
            'metal2vulkan/crates/metal2vulkan-air/src/canon/link.rs',
            ['custom_fn', 'let only_one =', '!by_name && only_one']),
        'custom_effect_linked_module_and_decode_cache': (
            'metal2vulkan/crates/metal2vulkan/src/pipeline/compile.rs',
            ['custom_fn', 'resolving.len() == 1', 'fn decode_kept(', 'KEPT_DECODES']),
        'metal_bridge_stage_links_and_multisample_resolve': (
            'nativemetal/bridge/NativeMetalObjects.rs',
            ['vertex_linked_group', 'fragment_linked_group', 'descriptor.linked.groups',
             'slot.resolve_texture', 'slot.resolve_level']),
    }
    rows = {}
    for feature, (relative, anchors) in checks.items():
        base, donor = BASE / relative, DONOR / relative
        a, b = base.read_text(), donor.read_text()
        row = {
            'path': relative,
            'baseline_sha256': digest(base), 'donor_sha256': digest(donor),
            'baseline_matches_build_source_manifest': digest(base) == manifest['gpu_files'][relative],
            'identical_bytes': base.read_bytes() == donor.read_bytes(),
            'identical_after_removing_whitespace': re.sub(r'\s+', '', a) == re.sub(r'\s+', '', b),
            'anchors_in_baseline': {anchor: anchor in a for anchor in anchors},
            'anchors_in_donor': {anchor: anchor in b for anchor in anchors},
        }
        assert row['baseline_matches_build_source_manifest'], relative
        assert row['identical_after_removing_whitespace'], relative
        assert all(row['anchors_in_baseline'].values()) and all(row['anchors_in_donor'].values()), row
        rows[feature] = row

    # Verify the executable in the actual rollback root is the recorded build,
    # rather than inferring its identity from its filename.
    built = ROOT / 'out/dada-native-metal-ios26-r4/RedwoodMetal'
    actual = ROLLBACK_ROOT / 'lib/RedwoodMetal.bundle/RedwoodMetal'
    build = json.loads((ROOT / 'out/dada-native-metal-ios26-r4/build-manifest.json').read_text())
    assert digest(actual) == digest(built) == build['files']['RedwoodMetal']['sha256']
    report = {
        'donor_repository': 'https://github.com/superboyyy/hakios-arm64e-dada',
        'donor_revision': 'f1ae906fcc43ba8f763663257724ba938fb820ab',
        'baseline_repository': manifest['repository'],
        'baseline_revision': manifest['revision'],
        'rollback_payload': '../dada-touch-experimental-r3-20261110/ios-payload.bin',
        'rollback_metal_binary_sha256': digest(actual),
        'components': rows,
        'missing_donor_features_in_checked_components': [],
        'conclusion': 'Checked Liquid Glass translator/bridge code is already included in the rollback build.',
        'new_driver_or_payload_built': False,
        'runtime_glass_pixels_or_performance_verified': False,
        'limits': [
            'Whitespace comparison is a source check, not a proof of GPU shader semantics.',
            'The donor README reports remaining Liquid Glass issues; Mac tests do not qualify dada rendering.',
            'The alternate legacy redwoodmetal AIR-to-NIR library is not loaded by this NativeMetal root.',
            'No experimental translator upgrade or failed full donor GPU package was substituted.',
        ],
    }
    (OUTPUT / 'source-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS source audit: 5 Liquid Glass components already present; no new port claimed.')


if __name__ == '__main__':
    main()
