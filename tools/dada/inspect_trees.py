#!/usr/bin/env python3
"""Inspect stock trees without reading device registers or changing a phone."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def prop(path, node, name, kind="s"):
    result = subprocess.run(["fdtget", "-t", kind, str(path), node, name],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def main():
    directory = Path(sys.argv[1])
    rows = []
    for path in sorted(directory.glob("*.dtb")) + sorted(directory.glob("*.dtbo")):
        rows.append({"file": path.name, "bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "model": prop(path, "/", "model"),
                     "compatible": prop(path, "/", "compatible"),
                     "msm_id": prop(path, "/", "qcom,msm-id", "x"),
                     "board_id": prop(path, "/", "qcom,board-id", "x")})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
