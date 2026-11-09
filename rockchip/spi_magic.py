#!/usr/bin/env python3
"""spi_magic.py -- is the rk_bridge alive?  Read-only check for a hung link.
   spi_magic.py [reads] [hz]        default 5 reads at 10 MHz, 0.5 s apart
Stop m65io (and m65d) first: this opens /dev/spidev3.0 itself.

Sends only STATUS (command $01), which has no side effects in the bridge, and prints:
  magic 'Me' + version     -> the bridge answers (FPGA configured, SPI slave alive)
  flags, seq, last LBA     -> the SD emulation side
  monitor FIFOs            -> rx = bytes the MEGA65 monitor sent that the Rockchip has not read
                              tx_free = room in the FIFO towards the monitor (1024 = empty)
  core id                  -> which core is running

How to read it at a hang:
  - no magic (all $00 / $FF or random): the bridge or the FPGA is dead -> power cycle
  - magic ok, tx_free < 1024 and not rising between reads: the monitor does not take the
    bytes the Rockchip sent -> the CPU / monitor is stuck, the bridge is fine
  - magic ok, FIFOs idle (rx 0, tx_free 1024): the FPGA side is idle -> look at m65io / m65d
"""
import sys, time, spidev

reads = int(sys.argv[1]) if len(sys.argv) > 1 else 5
hz = int(float(sys.argv[2])) if len(sys.argv) > 2 else 10000000

spi = spidev.SpiDev()
spi.open(3, 0)
spi.mode = 0
spi.max_speed_hz = hz

N = 60                                   # STATUS bytes 0..59 (52 = core id)
prev = None
for k in range(reads):
    if k:
        time.sleep(0.5)
    r = spi.xfer2([0x01, 0] + [0] * N)[2:]
    magic_ok = r[0] == 0x4D and r[1] == 0x65
    f = r[3]
    flags = " ".join(n for b, n in ((0, "rd_req"), (1, "rd_ready"), (2, "wr_req"), (3, "inited"),
                                    (4, "rx_ovf"), (5, "rd_err")) if f >> b & 1) or "-"
    lba = r[5] << 24 | r[6] << 16 | r[7] << 8 | r[8]
    rx = r[9] << 8 | r[10]
    txf = r[11] << 8 | r[12]
    print("read %d at %.1f MHz: magic %02X %02X (%s), version %d, flags [%s], seq %d, lba %d, "
          "monitor rx %d tx_free %d, core id $%02X"
          % (k + 1, hz / 1e6, r[0], r[1], "ok" if magic_ok else "BAD", r[2], flags, r[4], lba,
             rx, txf, r[52]), flush=True)
    if prev is not None and magic_ok and txf < 1024 and txf <= prev:
        print("  tx_free not rising: the monitor is not taking bytes (CPU/monitor stuck?)")
    prev = txf if magic_ok else None
spi.close()
