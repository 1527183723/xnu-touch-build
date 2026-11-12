#!/usr/bin/env python3
"""Change only panel configuration and device-tree data in the pinned K90 UEFI."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

MU_COMMIT = "33dcffa431c0af815b239a11bc3660890c765f7c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(path, old, new):
    text = path.read_text()
    if old not in text and text.count(new) == 1:
        return
    if text.count(old) != 1:
        raise ValueError(f"unexpected source in {path.name}: {old!r}")
    path.write_text(text.replace(old, new))


def node(text, name):
    found = list(re.finditer(r"^\s*" + re.escape(name) + r"\s*\{", text, re.M))
    if len(found) != 1:
        raise ValueError(f"expected one {name}, found {len(found)}")
    start = text.index("{", found[0].start())
    depth = 1
    for end in range(start + 1, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if not depth:
            return text[found[0].start():end + 1]
    raise ValueError("unterminated device-tree node")


def panel_geometry(dtb):
    result = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(dtb)],
                            check=True, capture_output=True, text=True)
    panel = node(result.stdout, "qcom,mdss_dsi_o3_42_0d_0a_dsc_cmd")
    timing = node(panel, "timing@normal_60hz_index_01")

    def number(name):
        match = re.search(re.escape(name) + r"\s*=\s*<(0x[0-9a-f]+)>;", timing)
        if not match:
            raise ValueError(f"missing panel timing: {name}")
        return int(match[1], 16)

    width = number("qcom,mdss-dsi-panel-width")
    height = number("qcom,mdss-dsi-panel-height")
    fps = number("qcom,mdss-dsi-panel-framerate")
    sw, sh = number("qcom,mdss-dsc-slice-width"), number("qcom,mdss-dsc-slice-height")
    if (width, height, fps, sw, sh) != (1200, 2670, 60, 600, 30):
        raise ValueError("input does not have the expected dada o3 timing")
    match = re.search(r"qcom,mdss-dsi-on-command\s*=\s*\[([^]]+)\];", timing)
    data = bytes.fromhex(match[1])
    count = 0
    pos = 0
    while pos < len(data):
        if pos + 7 > len(data):
            raise ValueError("truncated DSI command header")
        high, low = data[pos + 5:pos + 7]
        size = high << 8 | low
        end = pos + 7 + size
        if end > len(data):
            raise ValueError("truncated DSI command data")
        count += 1
        pos = end
    if not count:
        raise ValueError("empty DSI initialization")
    return {"width": width, "height": height, "hz": fps,
            "dsc_slice": [sw, sh], "dsi_command_count": count}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mu", type=Path, required=True)
    ap.add_argument("--dtb", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    mu, out = args.mu.resolve(), args.output.resolve()
    revision = subprocess.check_output(["git", "-C", str(mu), "rev-parse", "HEAD"], text=True).strip()
    if revision != MU_COMMIT:
        raise ValueError("UEFI dependency must be the README's loader-v17 tag")
    geometry = panel_geometry(args.dtb)
    pkg = mu / "Platforms/Xiaomi/annibalePkg"
    if "USE_CUSTOM_DISPLAY_DRIVER      = 0" not in (pkg / "annibale.dsc").read_text():
        raise ValueError("this experiment requires the original SimpleFramebuffer display path")
    replace_once(pkg / "annibale.dsc", "PcdFrameBufferWidth|1156", "PcdFrameBufferWidth|1200")
    replace_once(pkg / "annibale.dsc", "PcdFrameBufferHeight|2510", "PcdFrameBufferHeight|2670")
    for target in (pkg / "FdtBlob/sm8750-xiaomi-annibale.dtb", mu / "Resources/DTBs/annibale.dtb"):
        shutil.copyfile(args.dtb, target)
    out.mkdir(parents=True, exist_ok=True)
    report = {"mu_commit": revision, "scope": "display and device-tree data only",
              "geometry": geometry, "merged_dtb_sha256": sha(args.dtb),
              "platform_driver_code_changed": False, "xnu_kernel_changed": False,
              "custom_display_driver": False, "original_panel_xml_preserved": True,
              "hardware_tested": False}
    (out / "display-inputs.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "mu-display.patch").write_bytes(subprocess.check_output(
        ["git", "-C", str(mu), "diff", "--ignore-submodules=all", "--binary"]))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
