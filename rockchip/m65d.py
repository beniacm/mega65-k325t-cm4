#!/usr/bin/env python3
"""m65d.py -- Rockchip-side service daemon for the MEGA65 on the QMTech K325T.

Talks to rk_bridge.vhdl over spi3-m1 (/dev/spidev3.0) and provides:
  * SD card: every sector the MEGA65 reads/writes is served from/to a raw
    image file (default /root/m65/sd.img, sparse).
  * Serial monitor: TCP port 4510 is a transparent byte pipe to the MEGA65
    UART monitor (like the FTDI port of a real MEGA65).  From the x86 host:
        socat pty,link=/tmp/m65tty,raw,echo=0 tcp:rock:4510   (then m65 -l /tmp/m65tty)
  * Control: TCP port 4511, one text command per line, reply "ok ..."/"err ...":
        status                     bridge + SD statistics
        reset                      pulse the CPU reset (200 ms)
        hold_reset 0|1
        type <text>                type text via the keyboard matrix (\\n = RETURN)
        key <NAME> [ms]            tap one key (RETURN, SPACE, F1, RUNSTOP, ...)
        keys <c0> .. <c8>          raw matrix state, 9 hex bytes (bit = row, 1 = down)
        joy <a> <b>                joystick bits (b0 up b1 down b2 left b3 right b4 fire)
        restore 0|1  capslock 0|1  sd emu|phys
        log [n]                    last n bytes of monitor output (as text)
Run as root (spidev3.0 is root-only).
"""
import argparse
import os
import select
import socket
import sys
import time

import spidev

# ---------------------------------------------------------------- key matrix
# MEGA65/C64 matrix: column = CIA PA line (0..8), row = PB bit.
MATRIX = [
    ["INST", "RETURN", "RIGHT", "F7", "F1", "F3", "F5", "DOWN"],
    ["3", "W", "A", "4", "Z", "S", "E", "LSHIFT"],
    ["5", "R", "D", "6", "C", "F", "T", "X"],
    ["7", "Y", "G", "8", "B", "H", "U", "V"],
    ["9", "I", "J", "0", "M", "K", "O", "N"],
    ["+", "P", "L", "-", ".", ":", "@", ","],
    ["POUND", "*", ";", "HOME", "RSHIFT", "=", "UPARROW", "/"],
    ["1", "LEFTARROW", "CTRL", "2", "SPACE", "MEGA", "Q", "RUNSTOP"],
    ["NOSCRL", "TAB", "ALT", "HELP", "F9", "F11", "F13", "ESC"],
]
KEYPOS = {name: (c, r) for c, col in enumerate(MATRIX) for r, name in enumerate(col)}
KEYPOS.update({"DEL": KEYPOS["INST"], "STOP": KEYPOS["RUNSTOP"], " ": KEYPOS["SPACE"],
               "C=": KEYPOS["MEGA"], "SHIFT": KEYPOS["LSHIFT"]})
SHIFTED = {"!": "1", '"': "2", "#": "3", "$": "4", "%": "5", "&": "6", "'": "7",
           "(": "8", ")": "9", "[": ":", "]": ";", "<": ",", ">": ".", "?": "/",
           "UP": "DOWN", "LEFT": "RIGHT", "CLR": "HOME", "F2": "F1", "F4": "F3",
           "F6": "F5", "F8": "F7"}


def key_combo(name):
    """name or single character -> list of (col,row) to hold together."""
    if len(name) == 1:
        ch = name.upper()
        if ch == "\n":
            return [KEYPOS["RETURN"]]
    else:
        ch = name.upper()
    if ch in KEYPOS:
        return [KEYPOS[ch]]
    if ch in SHIFTED:
        return [KEYPOS["LSHIFT"], KEYPOS[SHIFTED[ch]]]
    raise KeyError(name)


# ---------------------------------------------------------------- SPI bridge
class Bridge:
    def __init__(self, hz):
        self.spi = spidev.SpiDev()
        self.spi.open(3, 0)
        self.spi.mode = 0
        self.spi.bits_per_word = 8
        self.spi.max_speed_hz = hz

    def x(self, data):
        return self.spi.xfer2(list(data))

    def status(self):
        r = self.x([0x01, 0] + [0] * 16)[2:]
        return {
            "magic": bytes(r[0:2]), "ver": r[2], "flags": r[3], "seq": r[4],
            "lba": int.from_bytes(bytes(r[5:9]), "big"),
            "rxcnt": (r[9] << 8) | r[10], "txfree": (r[11] << 8) | r[12],
            "sdcmds": r[13], "lastcmd": r[14], "ctrl": r[15],
        }

    def sd_put(self, data):
        self.x(b"\x10" + data)

    def sd_get(self):
        return bytes(self.x([0x11, 0] + [0] * 512)[2:])

    def sd_wack(self):
        self.x([0x12])

    def uart_put(self, data):
        self.x(b"\x20" + data)

    def uart_get(self, n):
        return bytes(self.x([0x21, 0] + [0] * n)[2:])

    def set_input(self, ctrl, ja, jb, cols):
        self.x([0x30, ctrl & 0xFF, ja & 0x1F, jb & 0x1F] + [c & 0xFF for c in cols])


# ---------------------------------------------------------------- daemon
class Daemon:
    def __init__(self, args):
        self.args = args
        self.br = Bridge(args.hz)
        st = self.br.status()
        if st["magic"] != b"\x4d\x65":
            sys.exit("m65d: no rk_bridge on spidev3.0 (magic %r) - wrong bitstream?" % st["magic"])
        print("m65d: rk_bridge v%d found" % st["ver"], flush=True)
        self.img = open(args.image, "r+b")
        self.ctrl = 0
        self.ja = self.jb = 0
        self.cols = [0] * 9
        self.input_dirty = True
        self.keyq = []          # list of (deadline-relative ms, cols)
        self.key_until = 0.0
        self.reset_until = 0.0
        self.mon_clients = []
        self.ctl_clients = {}
        self.txq = bytearray()
        self.log = bytearray()
        self.stats = {"rd": 0, "wr": 0, "mon_in": 0, "mon_out": 0}
        self.mon_srv = self.listen(args.mon_port)
        self.ctl_srv = self.listen(args.ctl_port)

    @staticmethod
    def listen(port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(4)
        s.setblocking(False)
        return s

    # ---- SD
    def serve_sd(self, st):
        f = st["flags"]
        if f & 0x01 and not f & 0x02:
            self.img.seek(st["lba"] * 512)
            data = self.img.read(512)
            data += b"\x00" * (512 - len(data))
            self.br.sd_put(data)
            self.stats["rd"] += 1
            if self.args.verbose:
                print("rd %d" % st["lba"], flush=True)
            return True
        if f & 0x04:
            data = self.br.sd_get()
            self.img.seek(st["lba"] * 512)
            self.img.write(data)
            self.img.flush()
            self.br.sd_wack()
            self.stats["wr"] += 1
            if self.args.verbose:
                print("wr %d" % st["lba"], flush=True)
            return True
        return False

    # ---- keyboard / input
    def push_input(self):
        if self.input_dirty:
            self.br.set_input(self.ctrl, self.ja, self.jb, self.cols)
            self.input_dirty = False

    def queue_keys(self, combos, hold=0.06, gap=0.04):
        for combo in combos:
            cols = [0] * 9
            for c, r in combo:
                cols[c] |= 1 << r
            self.keyq.append((hold, cols))
            self.keyq.append((gap, [0] * 9))

    def run_keys(self, now):
        if self.keyq and now >= self.key_until:
            dur, cols = self.keyq.pop(0)
            self.cols = cols
            self.input_dirty = True
            self.key_until = now + dur

    # ---- control commands
    def command(self, line):
        p = line.strip().split(" ", 1)
        if not p or not p[0]:
            return "ok"
        cmd, rest = p[0].lower(), (p[1] if len(p) > 1 else "")
        a = rest.split()
        if cmd == "status":
            st = self.br.status()
            return "ok " + " ".join("%s=%s" % (k, v) for k, v in list(st.items()) + list(self.stats.items()))
        if cmd == "reset":
            self.ctrl |= 1
            self.input_dirty = True
            self.reset_until = time.time() + 0.2
            return "ok"
        if cmd == "hold_reset":
            self.ctrl = (self.ctrl | 1) if a and a[0] == "1" else (self.ctrl & ~1)
            self.input_dirty = True
            return "ok"
        if cmd in ("restore", "capslock"):
            bit = 2 if cmd == "restore" else 4
            self.ctrl = (self.ctrl | bit) if a and a[0] == "1" else (self.ctrl & ~bit)
            self.input_dirty = True
            return "ok"
        if cmd == "sd":
            self.ctrl = (self.ctrl | 8) if a and a[0] == "phys" else (self.ctrl & ~8)
            self.input_dirty = True
            return "ok"
        if cmd == "joy":
            self.ja, self.jb = int(a[0], 0), int(a[1], 0)
            self.input_dirty = True
            return "ok"
        if cmd == "keys":
            self.cols = [int(v, 16) for v in a[:9]] + [0] * (9 - len(a[:9]))
            self.input_dirty = True
            return "ok"
        if cmd == "key":
            hold = int(a[1]) / 1000.0 if len(a) > 1 else 0.06
            self.queue_keys([key_combo(a[0])], hold=hold)
            return "ok"
        if cmd == "type":
            text = rest.replace("\\n", "\n")
            try:
                self.queue_keys([key_combo(ch) for ch in text])
            except KeyError as e:
                return "err no key for %r" % e.args[0]
            return "ok %d keys" % len(text)
        if cmd == "log":
            n = int(a[0]) if a else 2000
            return "ok " + self.log[-n:].decode("latin-1").replace("\r", "").replace("\n", "\\n")
        return "err unknown command"

    # ---- main loop
    def loop(self):
        while True:
            now = time.time()
            busy = False
            if self.reset_until and now >= self.reset_until:
                self.ctrl &= ~1
                self.input_dirty = True
                self.reset_until = 0.0
            self.run_keys(now)
            self.push_input()

            st = self.br.status()
            busy |= self.serve_sd(st)

            # monitor output
            if st["rxcnt"]:
                data = self.br.uart_get(min(st["rxcnt"], 4000))
                self.stats["mon_out"] += len(data)
                self.log += data
                del self.log[:-65536]
                for c in list(self.mon_clients):
                    try:
                        c.sendall(data)
                    except OSError:
                        self.mon_clients.remove(c)
                busy = True
            # monitor input
            if self.txq and st["txfree"]:
                n = min(len(self.txq), st["txfree"], 1000)
                self.br.uart_put(bytes(self.txq[:n]))
                del self.txq[:n]
                self.stats["mon_in"] += n
                busy = True

            socks = [self.mon_srv, self.ctl_srv] + self.mon_clients + list(self.ctl_clients)
            r, _, _ = select.select(socks, [], [], 0 if busy or self.keyq else self.args.idle)
            for s in r:
                if s is self.mon_srv:
                    c, _ = s.accept()
                    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.mon_clients.append(c)
                elif s is self.ctl_srv:
                    c, _ = s.accept()
                    self.ctl_clients[c] = b""
                elif s in self.mon_clients:
                    try:
                        d = s.recv(4096)
                    except OSError:
                        d = b""
                    if not d:
                        self.mon_clients.remove(s)
                        s.close()
                    else:
                        self.txq += d
                else:
                    try:
                        d = s.recv(4096)
                    except OSError:
                        d = b""
                    if not d:
                        del self.ctl_clients[s]
                        s.close()
                        continue
                    buf = self.ctl_clients[s] + d
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            reply = self.command(line.decode("utf-8", "replace"))
                        except Exception as e:  # keep serving
                            reply = "err %s" % e
                        try:
                            s.sendall((reply + "\n").encode())
                        except OSError:
                            pass
                    self.ctl_clients[s] = buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="/root/m65/sd.img")
    ap.add_argument("--hz", type=int, default=4000000)
    ap.add_argument("--mon-port", type=int, default=4510)
    ap.add_argument("--ctl-port", type=int, default=4511)
    ap.add_argument("--idle", type=float, default=0.0005, help="poll sleep when idle (s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    Daemon(args).loop()


if __name__ == "__main__":
    main()
