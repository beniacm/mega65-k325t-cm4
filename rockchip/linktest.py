#!/usr/bin/env python3
"""linktest.py -- raw rk_bridge SPI link integrity at several clock rates.
   linktest.py [n] [hz ...]      default n=3000, hz = 10/16.7/25/33/40/50 MHz
For each rate: n STATUS reads (magic 'Me' + version: MISO path) and n INPUT writes of
random ctrl bits 6..7 (unused; 4/5 are cursor up/left) read back via STATUS byte 15
(MOSI -> register -> MISO).
Stop m65d first (it also writes INPUT); m65io may keep running."""
import random, sys, time, spidev
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
rates = [int(float(x)) for x in sys.argv[2:]] or [10e6, 16.7e6, 25e6, 33.4e6, 40e6, 50e6]
spi = spidev.SpiDev(); spi.open(3, 0); spi.mode = 0
spi.max_speed_hz = 10000000
VER = spi.xfer2([0x01, 0] + [0] * 16)[4]            # bridge version, read at a safe rate
for hz in rates:
    spi.max_speed_hz = int(hz)
    bad_magic = bad_rt = 0
    t0 = time.time()
    for i in range(n):
        v = random.randrange(4) << 6
        spi.xfer2([0x30, v, 0, 0] + [0] * 9)
        r = spi.xfer2([0x01, 0] + [0] * 16)[2:]
        if r[0] != 0x4D or r[1] != 0x65 or r[2] != VER:
            bad_magic += 1
        elif r[15] != v:
            bad_rt += 1
    spi.xfer2([0x30, 0, 0, 0] + [0] * 9)
    print("%5.1f MHz: %d x (INPUT write + STATUS read): magic/version errors %d, ctrl round-trip errors %d  (%.0f us/pair)"
          % (hz / 1e6, n, bad_magic, bad_rt, (time.time() - t0) / n * 1e6), flush=True)
