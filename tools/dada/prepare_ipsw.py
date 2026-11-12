#!/usr/bin/env python3
"""Extract this IPSW's three BuildManifest images without modifying prior builds."""
import argparse
import hashlib
import json
import plistlib
import subprocess
import time
from pathlib import Path
from zipfile import ZipFile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipsw", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--ipsw-tool", type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    record_path = out / "ipsw-inputs.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    with ZipFile(args.ipsw) as archive:
        manifest = plistlib.loads(archive.read("BuildManifest.plist"))
        if (manifest["ProductVersion"], manifest["ProductBuildVersion"]) != ("26.6.2", "23G90"):
            raise ValueError("This repository's recipe requires 26.6.2 / 23G90")
        identity = next(i for i in manifest["BuildIdentities"]
                        if i["Info"].get("DeviceClass") == "v57ap"
                        and i["Info"].get("RestoreBehavior") == "Erase")
        record.update(version=manifest["ProductVersion"], build=manifest["ProductBuildVersion"],
                      products=manifest["SupportedProductTypes"], ipsw_bytes=args.ipsw.stat().st_size,
                      fresh_local_extraction=True)
        (out / "BuildManifest.plist").write_bytes(archive.read("BuildManifest.plist"))
        (out / "SystemVersion.plist").write_bytes(archive.read("SystemVersion.plist"))
        for kind, name in (("OS", "os.dmg.aea"), ("Cryptex1,SystemOS", "systemos.dmg.aea"),
                           ("Cryptex1,AppOS", "appos.dmg")):
            member = identity["Manifest"][kind]["Info"]["Path"]
            info = archive.getinfo(member)
            target = out / name
            if kind in record.get("images", {}) and target.exists():
                assert target.stat().st_size == record["images"][kind]["bytes"]
                continue
            if target.exists():
                raise FileExistsError(target)
            partial = target.with_name(target.name + ".partial")
            if partial.exists():
                raise FileExistsError(partial)
            print(f"Extracting {member}: {info.file_size:,} bytes", flush=True)
            digest, count, last = hashlib.sha256(), 0, time.monotonic()
            with archive.open(member) as source, partial.open("xb") as dest:
                while data := source.read(8 << 20):
                    dest.write(data)
                    digest.update(data)
                    count += len(data)
                    if time.monotonic() - last > 20:
                        print(f"{name}: {count:,}/{info.file_size:,}", flush=True)
                        last = time.monotonic()
            assert count == info.file_size
            partial.rename(target)
            record.setdefault("images", {})[kind] = {
                "member": member, "file": name, "bytes": count,
                "sha256": digest.hexdigest(), "zip_crc32": f"{info.CRC:08x}"}
            record_path.write_text(json.dumps(record, indent=2) + "\n")
    for name in ("os", "systemos"):
        target, marker = out / f"{name}.dmg", out / f".{name}-decrypted"
        if marker.exists():
            assert target.stat().st_size == int(marker.read_text())
            continue
        if target.exists():
            raise FileExistsError(target)
        print(f"Decrypting {name}.dmg.aea", flush=True)
        with (out / f"decrypt-{name}.log").open("wb") as log:
            subprocess.run([str(args.ipsw_tool), "fw", "aea", str(out / f"{name}.dmg.aea"),
                            "-o", str(out)], stdout=log, stderr=log, check=True)
        assert target.stat().st_size > 1 << 20
        marker.write_text(str(target.stat().st_size))
        print(f"Decrypted {name}: {target.stat().st_size:,} bytes", flush=True)
    print("All three firmware inputs prepared from the local IPSW.", flush=True)


if __name__ == "__main__":
    main()
