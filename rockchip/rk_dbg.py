#!/usr/bin/env python3
"""rk_dbg.py -- read the C64 (K325T) debug word from rk_bridge's STATUS reply (bytes 18/19).
   python3 rk_dbg.py [count] [interval_s]
byte 19: b0 MIG calibrated, b1 MIG ui reset, b2 core paused (QNICE), b3 core reset,
         b4 M2M reset, b5 frame counter bit (toggles every 4 frames), b6 core memory
         request stuck > 1.3 ms, b7 bridge request / read data stuck > 1.3 ms
byte 18: outstanding core read beats (saturated at 15)
"loads" (3rd argument): the last 4 cartridge bank loads (word address, word sum, words read)
"probe" (3rd argument): bytes 20..51 = writes to / reads from DDR3 word 0x200000 (CRT/REU area),
         seen at the core's port and at avm_mig's input
Safe next to m65io (spidev serialises transfers)."""
import sys
import time

import spidev

NAMES = ["calib", "ui_rst", "PAUSE", "core_rst", "m2m_rst", "frame", "CORE_STUCK", "MEM_STUCK"]

spi = spidev.SpiDev()
spi.open(3, 0)
spi.mode = 0
spi.max_speed_hz = 1000000
n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
dt = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
for _ in range(n):
    r = spi.xfer2([0x01, 0] + [0] * 52)[2:]
    a, b = r[19], r[18] & 0x0F
    bits = " ".join(nm for i, nm in enumerate(NAMES) if a >> i & 1)
    print(f"magic={bytes(r[0:2])!r} ver={r[2]} dbg={a:02x} pend={b:2d}  {bits}")
    if len(sys.argv) > 3 and sys.argv[3] == "probe":
        e = bytes(r[20:52])
        w = lambda o: int.from_bytes(e[o:o + 2], "little")
        words = lambda o, n: " ".join("%04x" % w(o + 2 * i) for i in range(n))
        print(f"  core port: writes@0x200000={w(0)}  bank0 sum={w(2):04x}  read@0x200028: {words(4, 8)}")
        txt = bytes(x for i in range(8) for x in (w(4 + 2 * i) & 0xFF, w(4 + 2 * i) >> 8))
        print(f"      as text: {txt!r}")
        print(f"  avm_mig in  @0x200000: writes={w(20)} reads={w(22)} read: {words(24, 2)}")
        f = e[29]
        fl = ["DMA(cpu held)", "reu_dma", "crt_bank_wait", "crt_loading", "core_reset", "reu_irq"]
        mode = ["hw-slot", "REU", "CRT", "?"][f >> 6]
        print(f"  C64: addr=${w(30):04x} mode={mode} " + " ".join(n for i, n in enumerate(fl) if f >> i & 1))
    if len(sys.argv) > 3 and sys.argv[3] == "loads":
        v = int.from_bytes(bytes(r[20:52]), "little")
        print(f"  bank loads total={(v >> 212) & 0xFFFF}  C64 flags={(v >> 232) & 0xFF:02x} addr=${(v >> 240) & 0xFFFF:04x}")
        for i in range(4):
            e = (v >> (53 * i)) & ((1 << 53) - 1)
            print(f"    load[-{i}] word=0x{e >> 29:06x} sum={(e >> 13) & 0xFFFF:04x} words={e & 0x1FFF}")
    if len(sys.argv) > 3 and sys.argv[3] == "traffic":
        v = int.from_bytes(bytes(r[20:52]), "little")
        f = lambda lo, n: (v >> lo) & ((1 << n) - 1)
        print(f"  scaler area: write beats={f(106, 32)} read bursts={f(138, 32)}   core area: read bursts={f(170, 32)} "
              f"write beats(mod 1024)={f(202, 10)}  bank loads={f(212, 16)}  C64 addr=${f(240, 16):04x}")
    time.sleep(dt)
