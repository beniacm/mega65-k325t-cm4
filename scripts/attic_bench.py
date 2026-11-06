#!/usr/bin/env python3
"""attic_bench.py -- time the MEGA65 CPU reading the 8 MB attic RAM sequentially.
Switches to C64 mode (GO64), pokes a 45GS02 loop to $C000 that reads $8000000-$87FFFFF
with LDA [$FB],Z (32-bit pointer), starts it with the monitor's 'g', and times it until the
loop sets $C102 = $42.  Afterwards the machine is reset back to BASIC 65."""
import os, re, socket, subprocess, sys, time
HOST = os.environ.get("M65_HOST", "192.168.1.143")
CTL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m65ctl.py")

def mon(cmd, wait=0.3):
    with socket.create_connection((HOST, 4510), timeout=5) as s:
        s.sendall((cmd + "\r").encode()); s.settimeout(0.05); out, t0 = b"", time.time()
        while time.time() - t0 < wait:
            try: out += s.recv(65536)
            except socket.timeout: pass
    return out.decode("latin-1")

def peek1(addr):
    m = re.search(r":0?%07X:([0-9A-F]{2})" % addr, mon("m%07x" % addr, 0.25))
    return int(m.group(1), 16) if m else None

MB = 8
BLOCKS = MB * 16                               # 64 KB blocks
code = bytes([
    0xA9, 0x41, 0x85, 0x00,                           # LDA #65 : STA $00 -> 40 MHz (C64 mode is 1 MHz)
    0xA9, 0x00, 0x85, 0xFB, 0x85, 0xFC, 0x85, 0xFD,   # pointer $FB-$FE = $08000000
    0xA9, 0x08, 0x85, 0xFE,
    0xA2, BLOCKS,                                     # LDX #blocks
    0xA3, 0x00,                                       # LDZ #0
    0xEA, 0xB2, 0xFB,                                 # loop: LDA [$FB],Z
    0x1B,                                             # INZ
    0xD0, 0xFA,                                       # BNE loop
    0xE6, 0xFC,                                       # INC $FC (next page)
    0xD0, 0xF6,                                       # BNE loop
    0xE6, 0xFD,                                       # INC $FD (next 64 KB)
    0xCA,                                             # DEX
    0xD0, 0xF1,                                       # BNE loop
    0xA9, 0x42, 0x8D, 0x02, 0xC1,                     # LDA #$42 : STA $C102
    0x4C, 0x23, 0xC0,                                 # JMP * (self)
])
# fix the JMP target to its own address
jmp_at = len(code) - 3
code = code[:jmp_at + 1] + bytes([jmp_at & 0xFF, 0xC0]) + b""
# deterministic start: reset -> intro menu autoboots -> '/' exits to BASIC 65 -> GO64
subprocess.run([CTL, "reset"], capture_output=True); time.sleep(12)
subprocess.run([CTL, "key", "/", "150"], capture_output=True); time.sleep(4)
subprocess.run([CTL, "type", "GO64\\n"], capture_output=True); time.sleep(3)
subprocess.run([CTL, "type", "Y\\n"], capture_output=True); time.sleep(4)
# C64 mode check: KERNAL/BASIC 2 banner "64K RAM SYSTEM" leaves $C000 as plain RAM;
# verify by writing and reading back through the CPU's view ('@' = dump from CPU perspective)
mon("sc0ff 5a")
if "5A" not in mon("@c0ff", 0.4).upper():
    sys.exit("not in C64 mode / $C000 not RAM -- aborting")
mon("sc102 00")
for i in range(0, len(code), 16):
    mon("s%07x %s" % (0xC000 + i, " ".join("%02x" % b for b in code[i:i + 16])))
def run(d7fe, label):
    mon("sffd37fe %02x" % d7fe)
    mon("sc102 00")
    t0 = time.time()
    mon("g c000", 0.1)
    while time.time() - t0 < 180:
        if peek1(0xC102) == 0x42:
            break
    dt = time.time() - t0
    print("  $D7FE=$%02X %-28s %6.2f s  %6.0f KB/s" % (d7fe, label, dt, MB * 1024 / dt))

print("%d MB sequential CPU read of attic RAM at 40 MHz (host-timed, ~0.3 s monitor overhead):" % MB)
run(0x00, "(slow cache off)")
run(0x04, "(slow cache on, default)")
run(0x0C, "(cache + advance prefetch)")
subprocess.run([CTL, "reset"], capture_output=True)
