#!/bin/bash
# build_llvm_mos.sh -- run ON THE PC (x86_64 Linux): cross-build llvm-mos for the Rockchip
# (aarch64, Debian 10 buster, glibc 2.28) and install it to /opt/llvm-mos on the board.
#
#   rockchip/deploy/build_llvm_mos.sh [SDK_TAG] [root@HOST]      default: v23.4.0, root@192.168.1.143
#
# The llvm-mos SDK is released only for x86_64/macOS/Windows.  Its MOS parts (runtime, headers,
# platform libraries such as mos-platform/mega65, the mos-*-clang links and .cfg files) are
# host-independent, so: take the official x86_64 SDK, build clang/lld/llvm tools for aarch64 from
# exactly the compiler commit that SDK was built from, and swap the binaries.
# Needs on the PC: clang, lld, cmake, ninja, git, curl, dpkg-deb, ~15 GB in $WORK (tmpfs is fine:
# the default is /tmp, a RAM disk here), ~20 min with 32 cores.
# Sysroot = the board's own headers/libraries (read over SSH) + libstdc++-8-dev (arm64, from the
# Debian archive; only unpacked on the PC, nothing is installed on the board).
set -euo pipefail
TAG=${1:-v23.4.0}
HOST=${2:-root@192.168.1.143}
WORK=${WORK:-/tmp/llvm-mos-cross}
TOOLS="llvm-addr2line;llvm-ar;llvm-cxxfilt;llvm-dwarfdump;llvm-mc;llvm-mlb;llvm-nm;llvm-objcopy;llvm-objdump;llvm-ranlib;llvm-readelf;llvm-readobj;llvm-size;llvm-strings;llvm-strip;llvm-symbolizer"
mkdir -p "$WORK"; cd "$WORK"

echo "== 1. official SDK $TAG (x86_64) and the compiler commit it was built from"
[ -d sdk-x86/llvm-mos ] || { curl -sSL -o sdk-x86.tar.xz \
    "https://github.com/llvm-mos/llvm-mos-sdk/releases/download/$TAG/llvm-mos-linux.tar.xz"
  mkdir -p sdk-x86 && tar -C sdk-x86 -xJf sdk-x86.tar.xz; }
COMMIT=$(sdk-x86/llvm-mos/bin/clang --version | sed -n 's/.*llvm-mos[^ ]* \([0-9a-f]\{40\}\).*/\1/p')
echo "compiler commit $COMMIT"

echo "== 2. sysroot from the board (+ libstdc++-8-dev headers)"
if [ ! -d sysroot/usr/include/c++ ]; then
  mkdir -p sysroot
  ssh -o BatchMode=yes "$HOST" 'cd / && tar -cf - usr/include lib/aarch64-linux-gnu usr/lib/gcc/aarch64-linux-gnu/8 $(ls -d usr/lib/aarch64-linux-gnu/lib{c,m,pthread,dl,rt,z,util,mvec,stdc++,gcc_s,resolv}.* usr/lib/aarch64-linux-gnu/lib{c,m,pthread,dl,rt}_nonshared.a usr/lib/aarch64-linux-gnu/*crt*.o 2>/dev/null)' \
    | tar -C sysroot -xf -
  curl -sSL -o libstdcxx8.deb \
    http://archive.debian.org/debian/pool/main/g/gcc-8/libstdc++-8-dev_8.3.0-6_arm64.deb
  dpkg-deb -x libstdcxx8.deb sysroot
  python3 - sysroot <<'EOF'
import os, sys                      # absolute symlinks -> relative (inside the sysroot)
root = sys.argv[1]
for d, dirs, files in os.walk(root):
    for n in dirs + files:
        p = os.path.join(d, n)
        if os.path.islink(p) and os.readlink(p).startswith("/"):
            t = os.readlink(p); os.unlink(p); os.symlink(os.path.relpath(root + t, d), p)
EOF
fi
cat > aarch64.cmake <<EOF
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_SYSROOT $WORK/sysroot)
set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_C_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_CXX_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
EOF

echo "== 3. llvm-mos source at $COMMIT"
if [ ! -d src/llvm ]; then
  mkdir -p src && git -C src init -q && git -C src remote add origin https://github.com/llvm-mos/llvm-mos.git
  git -C src fetch -q --depth 1 origin "$COMMIT" && git -C src checkout -q FETCH_HEAD
fi

echo "== 4. cross build: clang, lld, llvm tools for aarch64 (no MOS runtimes: they come from the SDK)"
cmake -G Ninja -S src/llvm -B build -C src/clang/cmake/caches/MOS.cmake \
  -DCMAKE_TOOLCHAIN_FILE="$WORK/aarch64.cmake" -DLLVM_ENABLE_PROJECTS="clang;lld" \
  -DLLVM_ENABLE_RUNTIMES="" -DLLVM_HOST_TRIPLE=aarch64-unknown-linux-gnu \
  -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF \
  -DCLANG_INCLUDE_TESTS=OFF -DLLVM_TOOLCHAIN_TOOLS="$TOOLS" \
  -DLLVM_DISTRIBUTION_COMPONENTS="clang;lld;clang-resource-headers;$TOOLS" \
  -DCMAKE_INSTALL_PREFIX="$WORK/install" > configure.log
ninja -C build distribution > build.log
ninja -C build install-distribution > install.log

echo "== 5. SDK with the aarch64 binaries"
rm -rf pkg && cp -a sdk-x86/llvm-mos pkg
for f in pkg/bin/*; do
  [ -L "$f" ] && continue
  file -b "$f" | grep -q "ELF 64-bit.*x86-64" || continue
  b=$(basename "$f")
  if [ -f "install/bin/$b" ] && [ ! -L "install/bin/$b" ]; then cp "install/bin/$b" "$f"
  else rm "$f"; echo "  dropped x86-only $b (no aarch64 build)"; fi
done
readelf -V pkg/bin/clang-* | grep -o -E "GLIBC_[0-9.]+" | sort -V | tail -1 | sed 's/^/  needs /'

echo "== 6. install to $HOST:/opt/llvm-mos"
tar -C pkg -cf - . | ssh -o BatchMode=yes "$HOST" 'rm -rf /opt/llvm-mos.new && mkdir -p /opt/llvm-mos.new &&
  tar -C /opt/llvm-mos.new -xf - && rm -rf /opt/llvm-mos && mv /opt/llvm-mos.new /opt/llvm-mos &&
  echo "export PATH=/opt/llvm-mos/bin:\$PATH" > /etc/profile.d/llvm-mos.sh &&
  /opt/llvm-mos/bin/mos-mega65-clang --version | head -1'
