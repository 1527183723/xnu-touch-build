#!/usr/bin/env python3
"""Verify the delivered boot image against its actual firmware/build inputs."""
import argparse
import hashlib
import importlib.util
import json
import lzma
import re
import struct
import subprocess
import tempfile
import uuid
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FDT_GUID = "25462CDA-221F-47DF-AC1D-259CFAA4E326"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffs_files(volume):
    """Read normal-size FFS files from this build's FV, respecting its alignment."""
    offset = align(struct.unpack_from("<H", volume, 48)[0], 8)
    while offset + 24 <= len(volume) and volume[offset:offset + 24] != b"\xff" * 24:
        size = int.from_bytes(volume[offset + 20:offset + 23], "little")
        require(24 <= size < 0xffffff and offset + size <= len(volume), "invalid FFS bounds")
        yield volume[offset:offset + size]
        offset = align(offset + size, 8)


def sections(data):
    offset = 0
    while offset + 4 <= len(data):
        size = int.from_bytes(data[offset:offset + 3], "little")
        require(4 <= size < 0xffffff and offset + size <= len(data), "invalid section bounds")
        yield data[offset:offset + size]
        offset = align(offset + size, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "out/dada-display-only-r1")
    ap.add_argument("--mu", required=True, type=Path)
    ap.add_argument("--image-name", default="warsaw-dada-display-only-r1.img")
    ap.add_argument("--loader-name", default="loader-dada-display.efi")
    args = ap.parse_args()
    out, mu = args.output.resolve(), args.mu.resolve()
    checks = []

    def check(condition, message):
        require(condition, message)
        checks.append(message)

    image = (out / args.image_name).read_bytes()
    check(image[:8] == b"ANDROID!", "Android boot magic")
    ks, ka, rs, ra, ss, sa, tags, page, version, osver = struct.unpack_from("<10I", image, 8)
    recovery_size, recovery_offset, header_size = struct.unpack_from("<IQI", image, 1632)
    check((page, version, header_size, ss, recovery_size, recovery_offset) ==
          (2048, 1, 1648, 0, 0, 0), "boot v1 header and section sizes")
    kernel_offset, ramdisk_offset = page, page + align(ks, page)
    check(len(image) == ramdisk_offset + align(rs, page), "section offsets and complete image length")
    kernel = image[kernel_offset:kernel_offset + ks]
    ramdisk = image[ramdisk_offset:ramdisk_offset + rs]
    check(ramdisk == (mu / "Resources/ramdisk").read_bytes(), "original placeholder ramdisk")
    digest = hashlib.sha1()
    for part in (kernel, ramdisk, b"", b""):
        digest.update(part)
        digest.update(struct.pack("<I", len(part)))
    check(image[576:608] == digest.digest().ljust(32, b"\0"), "Android boot image ID checksum")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    blob = inflater.decompress(kernel) + inflater.flush()
    check(inflater.eof, "complete gzip stream including its checksum")
    dtb = (out / "inputs/dada-merged.dtb").read_bytes()
    check(inflater.unused_data == dtb, "appended DTB exactly matches the dada input")
    check(dtb[:4] == b"\xd0\x0d\xfe\xed" and struct.unpack_from(">I", dtb, 4)[0] == len(dtb),
          "complete FDT header and declared size")

    shim = (out / "BootShim.bin").read_bytes()
    fd = (out / "SILICIUM_UEFI.fd").read_bytes()
    payload = (out / "loader-usb.xnup").read_bytes()
    check(len(shim) == 368 and shim[56:60] == b"ARM\x64" and
          struct.unpack_from("<QQ", shim, 8) == (0xA7100000, 0x300000),
          "strict BootShim ARM64 header, FD base and FD size")
    with tempfile.TemporaryDirectory(prefix="dada-shim-verify-") as temporary:
        obj, raw = Path(temporary) / "shim.o", Path(temporary) / "shim.bin"
        subprocess.run(["clang", "--target=aarch64-linux-gnu", "-DPAYLOAD_BASE=0xF0000000",
                        "-DPAYLOAD_CAP=0x03700000", "-DREQUIRES_KERNEL_HEADER=1",
                        "-DFD_BASE=0xA7100000", "-DFD_SIZE=0x300000", "-c",
                        str(mu / "BootShim/BootShim.S"), "-o", str(obj)], check=True)
        subprocess.run(["llvm-objcopy", "-O", "binary", str(obj), str(raw)], check=True)
        check(raw.read_bytes() == shim,
              "BootShim rebuilt byte-for-byte with strict payload base/cap and FD parameters")
    fv_length, = struct.unpack_from("<Q", fd, 32)
    fv_header_size, = struct.unpack_from("<H", fd, 48)
    check(len(fd) == fv_length == 0x300000 and fd[40:44] == b"_FVH",
          "complete 3 MiB UEFI firmware volume")
    check(sum(struct.unpack_from(f"<{fv_header_size // 2}H", fd)) & 0xffff == 0,
          "firmware volume header checksum")
    check(fd == (mu / "Build/annibalePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd").read_bytes(),
          "delivered FD matches the completed EDK2 build")
    mk = load("dada_mkxnuimg", ROOT / "mkxnuimg.py")
    body_size, copy_size = mk.validate_container(payload, 0x03700000)
    check(blob == mk.build_blob(shim, fd, payload, 0x03700000),
          "decompressed boot data exactly matches BootShim, FD and padded XNUP")
    entries = {}
    pos = 8
    while pos + 24 < len(payload):
        raw_name, size, reserved = struct.unpack_from("<16sII", payload, pos)
        name = raw_name.split(b"\0", 1)[0].decode("ascii")
        require(reserved == 0, "nonzero XNUP reserved field")
        entries[name] = payload[pos + 24:pos + 24 + size]
        pos += 24 + align(size, 16)
    check(set(entries) == {"loader.efi", "identity", "usb"} and
          payload[pos:] == bytes(24), "loader-only XNUP entries and zero terminator")
    check(entries["identity"] == b"v57\0" and entries["usb"] == b"1\0",
          "original USB loader entry options")
    efi = (out / args.loader_name).read_bytes()
    check(entries["loader.efi"] == efi, "XNUP contains the delivered EFI loader")
    pe, = struct.unpack_from("<I", efi, 0x3c)
    check(efi[:2] == b"MZ" and efi[pe:pe + 4] == b"PE\0\0" and
          struct.unpack_from("<H", efi, pe + 4)[0] == 0xaa64 and
          struct.unpack_from("<H", efi, pe + 24)[0] == 0x20b and
          struct.unpack_from("<H", efi, pe + 24 + 68)[0] == 10,
          "AArch64 PE32+ EFI application")

    ffs_dir = mu / f"Build/annibalePkg/RELEASE_CLANGPDB/FV/Ffs/{FDT_GUID}FVMAIN"
    raw = (ffs_dir / f"{FDT_GUID}SEC1.raw").read_bytes()
    check(raw[3] == 0x19 and int.from_bytes(raw[:3], "little") == len(raw) and raw[4:] == dtb,
          "EDK2 FDT RAW section exactly matches the dada input")
    fvmain = (mu / "Build/annibalePkg/RELEASE_CLANGPDB/FV/FVMAIN.Fv").read_bytes()
    lzma_guid = uuid.UUID("ee4e5898-3914-4259-9d6e-dc7bd79403cf").bytes_le
    extracted = []
    for file in ffs_files(fd):
        if file[18] != 0x0b:
            continue
        for section in sections(file[24:]):
            if section[3] != 2 or section[4:20] != lzma_guid:
                continue
            data_offset, = struct.unpack_from("<H", section, 20)
            expanded = lzma.decompress(section[data_offset:], format=lzma.FORMAT_ALONE)
            extracted.extend(s[4:] for s in sections(expanded) if s[3] == 0x17)
    check(extracted == [fvmain], "inner firmware volume extracted exactly from final FD LZMA section")
    embedded_ffs = [file for file in ffs_files(extracted[0])
                    if file[:16] == uuid.UUID(FDT_GUID).bytes_le]
    # GenFv changes the FFS state bits according to the FV erase polarity.
    reference_ffs = (ffs_dir / f"{FDT_GUID}.ffs").read_bytes()
    check(len(embedded_ffs) == 1 and embedded_ffs[0][:23] == reference_ffs[:23] and
          embedded_ffs[0][24:] == reference_ffs[24:] and raw in embedded_ffs[0][24:],
          "dada FDT RAW section extracted from the delivered compressed firmware")
    for target in ("Platforms/Xiaomi/annibalePkg/FdtBlob/sm8750-xiaomi-annibale.dtb",
                   "Resources/DTBs/annibale.dtb"):
        check((mu / target).read_bytes() == dtb, f"dada DTB replacement: {target}")
    dsc = (mu / "Platforms/Xiaomi/annibalePkg/annibale.dsc").read_text()
    check("PcdFrameBufferWidth|1200" in dsc and "PcdFrameBufferHeight|2670" in dsc and
          "USE_CUSTOM_DISPLAY_DRIVER      = 0" in dsc,
          "1200x2670 SimpleFramebuffer with original display driver selection")
    for extra, expected in (([], (1280, 2772)), (["-DDADA_DISPLAY_ONLY"], (1200, 2670))):
        macros = subprocess.check_output(["clang", "-E", "-dM", "-x", "c", "-DWARSAW",
                                         *extra, "-include", str(ROOT / "xnuloader/platform_private.h"),
                                         "/dev/null"], text=True)
        actual = tuple(int(re.search(r"^#define " + key + r" (\d+)$", macros, re.M)[1])
                       for key in ("PLAT_PANEL_W", "PLAT_PANEL_H"))
        check(actual == expected and "#define PLAT_PHONE (&platform_warsaw)" in macros,
              f"preprocessed display {expected}, original warsaw platform")
    geometry = load("dada_prepare", ROOT / "tools/dada/prepare_uefi.py").panel_geometry(out / "inputs/dada-merged.dtb")
    source_changes = subprocess.check_output(["git", "-C", str(mu), "diff", "--name-only",
                                              "--ignore-submodules=all"], text=True).splitlines()
    check(set(source_changes) == {"Platforms/Xiaomi/annibalePkg/annibale.dsc",
                                  "Platforms/Xiaomi/annibalePkg/FdtBlob/sm8750-xiaomi-annibale.dtb",
                                  "Resources/DTBs/annibale.dtb"},
          "Mu platform modifications limited to two DTB files and display DSC")
    report = {"status": "PASS", "hardware_tested": False, "checks": checks,
              "image_bytes": len(image), "image_sha256": sha(image), "geometry": geometry,
              "framebuffer_stride_bytes": 1200 * 4,
              "xnup_body_bytes": body_size, "xnup_copy_bytes": copy_size,
              "xnup_entries": {key: len(value) for key, value in entries.items()},
              "dtb_sha256": sha(dtb), "efi_sha256": sha(efi), "fd_sha256": sha(fd)}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: {len(checks)} artifact checks; {len(image):,} bytes; SHA256 {sha(image)}")


if __name__ == "__main__":
    main()
