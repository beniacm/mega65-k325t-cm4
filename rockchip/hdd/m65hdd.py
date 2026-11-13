#!/usr/bin/env python3
"""m65hdd.py -- the AExp hard disk image for m65hdd.service (Radxa CM3 rig: hddsrv on /dev/spidev0.0).

The image is stored on the module (/root/m65/hdd/aexp.hdf, sparse) and served from RAM
(/dev/shm/aexp.hdf): the module's eMMC sees no writes while the Amiga uses the disk.

    m65hdd.py load      service start: copy the stored image into RAM (its data extents only; the
                        holes stay holes), unless a RAM copy is already there (a restarted server:
                        the RAM copy is the newer one)
    m65hdd.py save      write back what changed: the RAM copy's data extents are compared with the
                        stored image in 64 KB blocks; only blocks that differ are written, then one
                        fdatasync; nothing at all when nothing changed.  Runs when the service stops
                        (a clean shutdown: Ctrl+Alt+End / m65ctl poweroff) and on demand:
                        systemctl reload m65hdd   (or this command)
    m65hdd.py status    what a save would write now (nothing is written)
A power cut without a clean shutdown loses what the Amiga wrote since the last save; the stored image
stays as it was at that save (blocks are only written by save, never half a session).
"""
import os
import sys
import time

STORED = os.environ.get("M65_HDD_IMAGE", "/root/m65/hdd/aexp.hdf")
RAM = os.environ.get("M65_HDD_RAM", "/dev/shm/aexp.hdf")
BLOCK = 64 << 10


def extents(fd, size):
    """(start, end) of the data extents of a sparse file (SEEK_DATA / SEEK_HOLE)"""
    pos = 0
    while pos < size:
        try:
            start = os.lseek(fd, pos, os.SEEK_DATA)
        except OSError:                 # ENXIO: no data after pos
            return
        end = os.lseek(fd, start, os.SEEK_HOLE)
        yield start, min(end, size)
        pos = end


def log(msg):
    print("m65hdd: " + msg, flush=True)


def load():
    if os.path.exists(RAM):
        log("%s already in RAM (kept: newer than the stored image)" % RAM)
        return
    t0 = time.monotonic()
    tmp = RAM + ".load"
    src = os.open(STORED, os.O_RDONLY)
    dst = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    size = os.fstat(src).st_size
    os.ftruncate(dst, size)
    n = 0
    for start, end in extents(src, size):
        pos = start
        while pos < end:
            data = os.pread(src, min(BLOCK, end - pos), pos)
            if data.count(0) != len(data):         # zero blocks stay holes
                os.pwrite(dst, data, pos)
                n += len(data)
            pos += len(data)
    os.close(src)
    os.close(dst)
    os.rename(tmp, RAM)
    log("loaded %s into RAM: %d MB, %.1f MB of data, %.1f s" % (STORED, size >> 20, n / 1048576.0,
                                                               time.monotonic() - t0))


def diff_blocks(ram, sto, size):
    for start, end in extents(ram, size):
        pos = start - start % BLOCK
        while pos < end:
            a = os.pread(ram, BLOCK, pos)
            if a != os.pread(sto, BLOCK, pos):
                yield pos, a
            pos += BLOCK


def save(write=True):
    if not os.path.exists(RAM):
        log("nothing in RAM to save")
        return
    t0 = time.monotonic()
    ram = os.open(RAM, os.O_RDONLY)
    sto = os.open(STORED, os.O_RDWR if write else os.O_RDONLY)
    size = os.fstat(ram).st_size
    if size != os.fstat(sto).st_size:
        log("size differs (RAM %d, stored %d): not saved" % (size, os.fstat(sto).st_size))
        return
    n = 0
    for pos, data in diff_blocks(ram, sto, size):
        if write:
            os.pwrite(sto, data, pos)
        n += 1
    if write and n:
        os.fdatasync(sto)
    os.close(ram)
    os.close(sto)
    if not write:
        log("%d changed 64 KB blocks would be written" % n)
    elif n:
        log("saved %d changed 64 KB blocks (%d KB) to %s in %.1f s" % (n, n * 64, STORED, time.monotonic() - t0))
    else:
        log("no changes since the last save: nothing written")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "load":
        load()
    elif cmd == "save":
        save()
    elif cmd == "status":
        save(write=False)
    else:
        sys.exit(__doc__)
