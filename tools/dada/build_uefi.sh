#!/usr/bin/env bash
# Compile the complete README UEFI, changing only display configuration and DT data.
# Required: MU_SOURCE=<fresh loader-v17 checkout>, PYTHON_TOOLS=<stuart virtualenv>.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MU="${MU_SOURCE:?Set MU_SOURCE to a fresh loader-v17 Mu-Silicium checkout}"
OUT="${DADA_OUTPUT:-$ROOT/out/dada-display-only-r1}"
PYTOOLS="${PYTHON_TOOLS:?Set PYTHON_TOOLS to the edk2 Python virtualenv}"
export PATH="$PYTOOLS/bin:/usr/lib/llvm-21/bin:$PATH"
mkdir -p "$OUT"
exec > >(tee "$OUT/build-console.log") 2>&1
test ! -e "$OUT/warsaw-dada-display-only-r1.img"
test "$(git -C "$MU" rev-parse HEAD)" = 33dcffa431c0af815b239a11bc3660890c765f7c
git -C "$MU" config diff.ignoreSubmodules all

python3 "$ROOT/tools/dada/prepare_uefi.py" --mu "$MU" \
    --dtb "$OUT/inputs/dada-merged.dtb" --output "$OUT"

# Host-tool substitution from the upstream build guide; no runtime driver replacement.
python3 - "$MU" <<'PY'
from pathlib import Path
import shutil, sys
mu = Path(sys.argv[1])
p = mu / 'Mu_Basecore/BaseTools/Bin/nasm_ext_dep.yaml'
print('Using installed NASM for the upstream build dependency')
# The standard fixup removes this NuGet declaration and adds the host NASM directory.
if p.exists():
    p.rename(p.with_suffix('.yaml.orig'))
local = mu / 'Mu_Basecore/BaseTools/Bin/LinuxNasm'
local.mkdir(exist_ok=True)
shutil.copy2(shutil.which('nasm'), local / 'nasm')
PY
export PATH="$MU/Mu_Basecore/BaseTools/Bin/LinuxNasm:$PATH"
git -C "$MU" config submodule.Silicon/Silicium/OpensslPkg/Library/OpensslLib/openssl.update none
cd "$MU"
python3 Platforms/Xiaomi/annibalePkg/DeviceBuild.py --setup -t RELEASE
python3 Platforms/Xiaomi/annibalePkg/DeviceBuild.py --update -t RELEASE
python3 build_uefi.py -d annibale
cp Build/annibalePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd "$OUT/"
cp Build/BUILDLOG_annibalePkg.txt "$OUT/edk2-build.log"

cd "$ROOT"
# Do not label a dirty display-only source tree with the unmodified upstream's build ID.
unset BUILD_ID
EXTRA_CFLAGS="-DDIRECT_PHONE_PLAT -DWARSAW -DDADA_DISPLAY_ONLY" bash xnuloader/build.sh
cp xnuloader/loader-phone.efi "$OUT/loader-dada-display.efi"
python3 xnuloader/mkpayload.py -o "$OUT/loader-usb.xnup" \
    "loader.efi=$OUT/loader-dada-display.efi" --identity v57 --usb

# The strict BootShim and its destination window are the README's original contract.
make -C "$MU/BootShim" -B REQUIRES_KERNEL_HEADER=1 FD_BASE=0xA7100000 FD_SIZE=0x300000 \
    'CFLAGS=--target=aarch64-linux-gnu -DPAYLOAD_BASE=0xF0000000 -DPAYLOAD_CAP=0x03700000'
cp "$MU/BootShim/BootShim.bin" "$OUT/"
export MKXNUIMG="$ROOT/mkxnuimg.py"
python3 docs/tools/assemble-xnu-img.py --shim "$OUT/BootShim.bin" \
    --fd "$OUT/SILICIUM_UEFI.fd" --payload "$OUT/loader-usb.xnup" \
    --dtb "$OUT/inputs/dada-merged.dtb" --ramdisk "$MU/Resources/ramdisk" \
    --mkbootimg "$MU/Resources/Scripts/mkbootimg.py" \
    --out "$OUT/warsaw-dada-display-only-r1.img"
git -C "$MU" diff --ignore-submodules=all --binary > "$OUT/mu-after-build.patch"
git -C "$MU/Mu_Basecore" diff --ignore-submodules=all --binary > "$OUT/mu-basecore-build.patch"
python3 tools/dada/verify_uefi.py --mu "$MU" --output "$OUT"
echo "Complete display-only UEFI image built: $OUT/warsaw-dada-display-only-r1.img"
