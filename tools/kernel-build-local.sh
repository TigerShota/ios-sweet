#!/bin/sh
# Local XNU build on a real Mac. Replicates .github/workflows/xnu.yml exactly
# (same DXB repo/SHA, same patches, same ARCHS fix, same env), for when CI is
# unavailable. Requires macOS 13.5+ with Xcode 15.x (Xcode 16's iig emits
# IORPC.kernelContent which xnu-8796 lacks - do NOT use it) and Homebrew.
#
#   tools/kernel-build-local.sh          # build in ./dxb, kernel lands in ./out
#
# Output: out/kernel.development.sweet  (feed to gauguin-shim: mkpayload.py)
set -eu

cd "$(dirname "$0")/.."

DXB_REPO=https://github.com/blacktop/darwin-xnu-build
DXB_SHA=663906528d1cd5ea8fde7de6f16de73db9511549
export MACOS_VERSION=13.5
export KERNEL_CONFIG=DEVELOPMENT
export MACHINE_CONFIG=SWEET
export MAKEFLAGS=-k

# --- Xcode 15 check -------------------------------------------------------
XC=$(ls -d /Applications/Xcode_15*.app 2>/dev/null | sort -V | head -1 || true)
[ -n "$XC" ] || XC=$(ls -d /Applications/Xcode*.app | sort -V | head -1)
export DEVELOPER_DIR="$XC/Contents/Developer"
XV=$(xcodebuild -version | awk 'NR==1{print $2}')
case "$XV" in
15.*) ;;
*) echo "ERROR: Xcode $XV found, need 15.x (16+ iig breaks xnu-8796 IORPC ABI)" >&2
    echo "  Xcode 15.2 = last one that runs on macOS Ventura 13" >&2
    exit 1 ;;
esac
xcodebuild -version
xcrun --find iig

# --- deps -----------------------------------------------------------------
brew install jq gum bash cmake ninja 2>/dev/null || true
if [ ! -x /usr/local/bin/xcodes ] && [ ! -x /opt/homebrew/bin/xcodes ]; then
	printf '#!/bin/sh\nexit 0\n' | sudo tee /usr/local/bin/xcodes >/dev/null
	sudo chmod +x /usr/local/bin/xcodes
fi

# --- DXB + our patches (same as CI) ---------------------------------------
if [ ! -d dxb/.git ]; then
	git init -q dxb
	git -C dxb fetch -q --depth 1 "$DXB_REPO" "$DXB_SHA"
	git -C dxb checkout -q FETCH_HEAD
fi
cp xnu/patches/*.patch xnu/patches/*.sh dxb/patches/
# libfirehose_kernel is arm64e-only; the SWEET kernel is plain arm64
sed -i '' 's/ARCHS="x86_64 arm64e" VALID_ARCHS="x86_64 arm64e"/ARCHS="x86_64 arm64e arm64" VALID_ARCHS="x86_64 arm64e arm64"/' dxb/build.sh
grep -c 'VALID_ARCHS="x86_64 arm64e arm64"' dxb/build.sh

# --- build ----------------------------------------------------------------
( cd dxb && bash build.sh )

# --- collect ---------------------------------------------------------------
mkdir -p out
cp -R dxb/fakeroot/System/Library/Kernels/. out/ 2>/dev/null || true
ls -la out
( cd out && shasum -a 256 kernel.* > SHA256SUMS ) || true
echo "DONE: out/kernel.development.sweet"
