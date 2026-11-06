#!/usr/bin/env python3
"""sdlinktest.py -- end-to-end check of the SD read path at the current m65io SPI clock.

The MEGA65's own SD controller reads random sectors (monitor: $D681-$D684 = LBA, $D680 <- $02),
its sector buffer ($FFD6E00, with $D689.7 = SD buffer) is read back over the monitor and
compared with the same sectors read directly from card.img on the Rockchip.  This covers the
whole read path: m65io -> SPI (SD_PUT) -> rk_bridge -> sd_emu -> SdCardCtrl.  Read-only for the
card; $D689 is restored.  The MEGA65 should be idle (BASIC READY).

    scripts/sdlinktest.py [n] [--seq] [--host 192.168.1.143]
        --seq   n CONSECUTIVE sectors from a random start: the pattern the bridge's read cache
                (v4) prefetches, so these are mostly answered from the cache; the cache hits
                are reported from m65io.stats
"""
import hashlib
import random
import socket
import subprocess
import sys
import time

HOST = "192.168.1.143"
IMAGE = "/root/m65/card.img"


def ssh(host, cmd):
    """run a command on the Rockchip; retried (its sshd resets connections now and then)"""
    for attempt in range(6):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@" + host, cmd],
                           capture_output=True)
        if r.returncode != 255:
            r.check_returncode()
            return r.stdout.decode()
        time.sleep(2)
    raise OSError("ssh to %s keeps failing" % host)


def stat_field(host, name):
    for f in ssh(host, "cat /root/m65/m65io.stats").split():
        if f.startswith(name + "="):
            return int(f.split("=")[1])
    return None


class Monitor:
    def __init__(self, host):
        self.s = socket.create_connection((host, 4510), timeout=5)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def cmd(self, c):
        self.s.setblocking(False)
        try:
            while self.s.recv(65536):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        self.s.setblocking(True)
        self.s.sendall(c.encode() + b"\r")
        out, t0 = b"", time.time()
        self.s.settimeout(3)
        while not out.rstrip(b"\r\n ").endswith(b"\n.") and time.time() - t0 < 4:
            out += self.s.recv(65536)
        return out.decode(errors="replace")

    def read(self, addr, n):
        data = b""
        while len(data) < n:
            got = b""
            for line in self.cmd("M%x" % (addr + len(data))).splitlines():
                if line.startswith(":") and line.count(":") >= 2:
                    got += bytes.fromhex(line.split(":")[2].strip())
            if not got:
                raise OSError("monitor read failed")
            data += got
        return data[:n]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
    host = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else HOST
    sectors = int(ssh(host, "stat -c %%s %s" % IMAGE)) // 512
    if "--seq" in sys.argv:
        start = random.randrange(sectors - n)
        lbas = list(range(start, start + n))
    else:
        lbas = [random.randrange(sectors) for _ in range(n)]
    hits0 = stat_field(host, "pf_hits")
    ref = ssh(host, "python3 -c \"\nimport hashlib\nf=open('%s','rb')\n"
                    "for l in %r:\n f.seek(l*512); print(hashlib.md5(f.read(512)).hexdigest())\n\""
                    % (IMAGE, lbas)).split()
    m = Monitor(host)
    hz = ssh(host, "grep -o 'spi_hz=[0-9]*' /root/m65/m65io.stats").strip()
    d689 = m.read(0xFFD3689, 1)[0]
    m.cmd("sffd3689 %02x" % (d689 | 0x80))                    # sector buffer = SD card
    bad, t0 = 0, time.time()
    try:
        for i, lba in enumerate(lbas):
            m.cmd("sffd3681 %02x %02x %02x %02x" % (lba & 0xFF, lba >> 8 & 0xFF, lba >> 16 & 0xFF,
                                                     lba >> 24))
            m.cmd("sffd3680 02")                               # read sector
            for _ in range(200):
                if not m.read(0xFFD3680, 1)[0] & 0x03:         # SDIO / SDCARD busy
                    break
            else:
                print("lba %d: controller stays busy" % lba)
                bad += 1
                continue
            got = m.read(0xFFD6E00, 512)
            if hashlib.md5(got).hexdigest() != ref[i]:
                bad += 1
                print("lba %d: MISMATCH" % lba)
    finally:
        m.cmd("sffd3689 %02x" % d689)
    time.sleep(0.6)                                            # m65io.stats: every 0.5 s
    hits1 = stat_field(host, "pf_hits")
    print("%s: %d %s sectors read by the MEGA65's SD controller, %d mismatches (%.1f s)%s"
          % (hz, n, "consecutive" if "--seq" in sys.argv else "random", bad, time.time() - t0,
             ", read cache hits %d" % (hits1 - hits0) if hits0 is not None and hits1 is not None else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
