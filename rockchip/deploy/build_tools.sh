#!/bin/sh
# build_tools.sh -- run ON the Rockchip: install the MEGA65 cross-development tools.
#   cc65 (ca65 assembler, ld65 linker, cc65 C compiler; --cpu 45GS02) from the official
#   repository github.com/cc65/cc65 (Debian buster's package predates the 45GS02).
#   Installs to /usr/local (bin + share/cc65).  Takes ~10 min on the Core3566.
set -e
W=/root/src; mkdir -p $W; cd $W
if [ ! -x /usr/local/bin/ca65 ]; then
  rm -rf cc65-master
  wget -q -O cc65.tgz https://github.com/cc65/cc65/archive/refs/heads/master.tar.gz
  tar -xzf cc65.tgz
  make -C cc65-master -j4 >/dev/null 2>&1 || make -C cc65-master -j1
  make -C cc65-master PREFIX=/usr/local install >/dev/null
fi
ca65 --version 2>&1 | head -1
# the MEGA65 CPU (45GS02) must be known
cd /tmp
printf '.setcpu "45GS02"\n ldz #0\n neg\n' > m65test.s
ca65 -o m65test.o m65test.s && echo "ca65: 45GS02 ok"
