#!/usr/bin/env python3
# make_boot.py — assemble a new p3 boot image with a replacement kernel DTB.
# Inputs (same dir or via args): fdt_new.dtb, plus from the PRISTINE image:
#   boot_now.img (stock p3 dump), resource.img (stock resource, exactly
#   3049 blocks from offset 0x1615400), fit_hdr.dts (decompiled stock header).
# u-boot loads the DTB from the RSCE resource entry rk-kernel.dtb (NOT the FIT
# fdt node); we append the new DTB at the resource tail and point BOTH at it.
#   usage: make_boot.py [fdt.dtb] [out.img]
import struct, hashlib, re, shutil, subprocess, sys, os

fdt_path = sys.argv[1] if len(sys.argv) > 1 else 'fdt_new.dtb'
out_path = sys.argv[2] if len(sys.argv) > 2 else 'boot_new.img'
RES_POS, NEW_BLK = 0x1615400, 3049

fdt = open(fdt_path, 'rb').read()
res = bytearray(open('resource.img', 'rb').read())
assert len(res) == NEW_BLK * 512, "resource.img must be the pristine 3049-block dump"
res += fdt + b'\0' * ((512 - len(fdt) % 512) % 512)
e = 512
assert res[e:e+4] == b'ENTR' and res[e+4:e+17] == b'rk-kernel.dtb'
res[e+224:e+244] = hashlib.sha1(fdt).digest()
struct.pack_into('<III', res, e+256, 20, NEW_BLK, len(fdt))

fdt_pos = RES_POS + NEW_BLK * 512
total = (RES_POS + len(res) + 0x3ff) & ~0x3ff
def words(h): return ' '.join('0x%x' % int(h[i:i+8], 16) for i in range(0, 64, 8))
src = open('fit_hdr.dts').read()
for pat, rep in [
    (r'totalsize = <0x1792a00>;', f'totalsize = <0x{total:x}>;'),
    (r'data-size = <0x23914>;', f'data-size = <0x{len(fdt):x}>;'),
    (r'data-position = <0x800>;', f'data-position = <0x{fdt_pos:x}>;'),
    (r'value = <0x6cd79228 0x668c55f2 0x8fc0b06a 0x598f86b3 0x8cd35e95 0x2d45a52e 0xcfd53fcb 0x8b6d438>;',
     f'value = <{words(hashlib.sha256(fdt).hexdigest())}>;'),
    (r'data-size = <0x17d200>;', f'data-size = <0x{len(res):x}>;'),
    (r'value = <0xc6726e8a 0xc5c72b93 0x122ba0a0 0xa75d353a 0x750f1e04 0xc6ca19cd 0x93ae9d45 0x646924ae>;',
     f'value = <{words(hashlib.sha256(bytes(res)).hexdigest())}>;'),
]:
    src, n = re.subn(pat, rep, src)
    assert n == 1, pat
open('fit_hdr_out.dts', 'w').write(src)
subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', 'fit_hdr_out.dts', '-o', 'fit_hdr_out.dtb'],
               check=True, stderr=subprocess.DEVNULL)
hdr = open('fit_hdr_out.dtb', 'rb').read()
assert len(hdr) <= 0x800

shutil.copyfile('boot_now.img', out_path)
with open(out_path, 'r+b') as img:
    img.write(hdr + b'\0' * (0x800 - len(hdr)))
    img.seek(RES_POS)
    img.write(res)
    img.truncate(total)
print(f"{out_path}: fdt={len(fdt)} bytes @0x{fdt_pos:x}, resource={len(res):#x}, total={total:#x}")
