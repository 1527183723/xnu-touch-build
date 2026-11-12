#!/bin/bash
# Build the real XNU26 kernel with the optional dada native TouchCom overlay.
# This does not upload anything, change a phone, or package an old kernel as a new one.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
if [ "$(uname -s)" != Darwin ]; then
    echo 'This complete build requires macOS/Xcode with the real IIG generator.' >&2
    echo 'Linux check_touch_kernel_linux.py only checks the two driver objects.' >&2
    exit 2
fi
for tool in clang iig mig; do
    p=$(xcrun --sdk iphoneos -find "$tool")
    [ -x "$p" ] || { echo "Missing Xcode tool: $tool" >&2; exit 2; }
done
export MACHINE=BCM2837 KERNEL_CONFIG=DEVELOPMENT
export DADA_NATIVE_TOUCH=${DADA_NATIVE_TOUCH:-superboyyy}
case "$DADA_NATIVE_TOUCH" in
superboyyy)
    PORT="$ROOT/xnu26/dada-touch-superboyyy"
    TOUCH_INIT=hakios_dada_touch_init TOUCH_STOP=hakios_dada_touch_stop
    SUFFIX=superboyyy ;;
1)
    PORT="$ROOT/xnu26/dada-touch"
    TOUCH_INIT=dada_touch_init TOUCH_STOP=dada_touch_stop
    SUFFIX=legacy ;;
*) echo "Unsupported DADA_NATIVE_TOUCH=$DADA_NATIVE_TOUCH" >&2; exit 2 ;;
esac
export XNU26_SRC=${XNU26_SRC:-$ROOT/xnu-src/xnu-12377-dada-touch-$SUFFIX}
export XNU26_DEPS=${XNU26_DEPS:-$ROOT/xnu-deps/26-dada-touch-$SUFFIX}
if [ ! -d "$XNU26_SRC" ]; then
    bash xnu26/apply.sh "$XNU26_SRC"
else
    python3 tools/dada/apply_touch_port.py --tree "$XNU26_SRC" --port "$PORT"
fi
bash xnu26/deps-macos.sh --stage1
# Official BSD header export also asks the host SDK for availability.pl.
HOST_SDK=$(xcrun --sdk macosx --show-sdk-path)
if [ ! -x "$HOST_SDK/usr/local/libexec/availability.pl" ]; then
    echo 'The host SDK lacks usr/local/libexec/availability.pl.' >&2
    echo "Copy it from $XNU26_DEPS/sdk/iPhoneOS.sdk/usr/local/libexec/availability.pl" >&2
    echo 'to that host SDK, then run this script again.' >&2
    exit 2
fi
bash xnu26/build-macos.sh exporthdrs installhdrs
bash xnu26/deps-macos.sh --stage2
# Preserve the original ath12k functionality and rebuild it for this kernel ABI.
bash tools/mk-ath12k.sh
bash xnu26/build-macos.sh
bash tools/mk-wlfw-ath12k.sh
touch "$XNU26_SRC/bsd/dev/mhi_pcie_wlfw.c"
bash xnu26/build-macos.sh
KERNEL="$XNU26_SRC/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837"
[ -s "$KERNEL" ]
# A checked object alone must never be accepted as the resulting kernel.
SYMBOLS="$XNU26_SRC/BUILD/dada-touch-symbols.txt"
xcrun --sdk iphoneos nm "$KERNEL" > "$SYMBOLS"
grep -q " _$TOUCH_INIT\$" "$SYMBOLS"
grep -q " _$TOUCH_STOP\$" "$SYMBOLS"
echo "Linked kernel: $KERNEL"
shasum -a 256 "$KERNEL"
echo "Not hardware tested. Package this new kernel with boot arguments in $PORT/manifest.json."
