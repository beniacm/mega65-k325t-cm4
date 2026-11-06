#!/usr/bin/env python3
"""m65d.py -- control side of the Rockchip MEGA65 service (QMTech K325T board).

The hot paths (SD sector serving, serial-monitor pipe on TCP 4510) live in the
C daemon m65io; this process only drives the INPUT register of rk_bridge.vhdl
(keyboard matrix, joysticks, reset, RESTORE, CAPS LOCK, SD source) over the
same /dev/spidev3.0 -- each ioctl is one complete CS transaction, the kernel
serialises the two processes.

USB keyboards and gamepads on the module's USB ports are forwarded too
(m65input.py: hot-plug, exclusive grab, positional C64/MEGA65 key mapping,
first pad -> joystick port 2, second -> port 1); they are OR'd with the
scripted input below.

Control: TCP port 4511, one text command per line, reply "ok ..."/"err ...":
    status                     bridge status + m65io counters
    temps                      Rockchip thermal zones + FPGA die temperature (XADC)
    reset                      pulse the CPU reset (200 ms)
    hold_reset 0|1
    type <text>                type text via the keyboard matrix (\\n = RETURN); letters unshifted
                               (uppercase on the C64/C65 BASIC screen)
    typecase <text>            case-sensitive: capitals typed with SHIFT (ASCII apps, e.g. FDISK)
    key <NAME> [ms]            tap one key (RETURN, SPACE, F1, RUNSTOP, ...)
    keys <c0> .. <c8>          raw matrix state, 9 hex bytes (bit = row, 1 = down)
    joy <a> <b>                joystick bits (b0 up b1 down b2 left b3 right b4 fire)
    core [mega65|c64|toggle]   show / switch the FPGA core (flash slot 0 / 1); USB keyboard:
                               Ctrl+Alt+F1 / F2 / F12, Ctrl+Alt+Del = reset
    mouse <dx> <dy> [btn]      move the 1351 mouse on port 2 (counts, dy>0 = up; btn b0 L b1 R)
    mouse off                  no mouse (POTs read $FF)
    pots <p1x> <p1y> <p2x> <p2y>   raw SID POT values in hex (paddles); "pots off" = back to mouse
    restore 0|1  capslock 0|1  sd emu|phys
Run as root (spidev3.0 is root-only).
"""
import argparse
import glob
import select
import socket
import sys
import time

import spidev

import m65input

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
MODIFIERS = {KEYPOS[k] for k in ("LSHIFT", "RSHIFT", "CTRL", "MEGA", "ALT")}


def key_combo(name):
    """name or single character -> list of (col,row) to hold together."""
    ch = name.upper()
    if name == "\n":
        return [KEYPOS["RETURN"]]
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

    def status(self):
        r = self.spi.xfer2([0x01, 0] + [0] * 16)[2:]
        return {
            "magic": bytes(r[0:2]), "ver": r[2], "flags": r[3], "seq": r[4],
            "lba": int.from_bytes(bytes(r[5:9]), "big"),
            "rxcnt": (r[9] << 8) | r[10], "txfree": (r[11] << 8) | r[12],
            "sdcmds": r[13], "lastcmd": r[14], "ctrl": r[15],
        }

    def core_id(self):
        """STATUS byte 52: 1 = MEGA65, 2 = C64 (0 = older MEGA65 bitstream)"""
        r = self.spi.xfer2([0x01, 0] + [0] * 53)[2:]
        if bytes(r[0:2]) != b"Me":
            return None
        return r[52] or 1

    def iprog(self, addr):
        self.spi.xfer2([0x60, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF,
                        addr & 0xFF, 0xA5])

    def set_input(self, ctrl, ja, jb, cols, pots=(0xFF, 0xFF, 0xFF, 0xFF)):
        # bytes 13..16 = pot1 x, pot1 y, pot2 x, pot2 y (SID values; older bitstreams ignore them)
        self.spi.xfer2([0x30, ctrl & 0xFF, ja & 0x1F, jb & 0x1F] + [c & 0xFF for c in cols]
                       + [p & 0xFF for p in pots])


# ---------------------------------------------------------------- daemon
class Daemon:
    def __init__(self, args):
        self.args = args
        self.br = Bridge(args.hz)
        st = self.br.status()
        print("m65d: rk_bridge %s v%d" % ("found" if st["magic"] == b"Me" else "NOT found", st["ver"]),
              flush=True)
        self.ctrl = 0
        self.ja = self.jb = 0
        self.cols = [0] * 9
        self.input_dirty = True
        self.pots_override = None
        self.keyq = []          # list of (duration s, cols)
        self.key_until = 0.0
        self.reset_until = 0.0
        self.resend_at = 0.0    # after a core switch: send the input state to the new core
        self.ctl_clients = {}
        self.inp = m65input.InputHub(KEYPOS, log=lambda m: print(m, flush=True))
        self.inp.scan()
        self.next_scan = time.time() + 2.0
        self.sent = None
        self.ctl_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ctl_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ctl_srv.bind(("0.0.0.0", args.ctl_port))
        self.ctl_srv.listen(4)

    # ---- keyboard / input
    def push_input(self):
        # scripted state OR physical USB keyboards/gamepads
        if self.input_dirty:
            pcols = self.inp.matrix()
            pj1, pj2 = self.inp.joysticks()
            state = (self.ctrl | self.inp.ctrl_bits(), self.ja | pj1, self.jb | pj2,
                     tuple(a | b for a, b in zip(self.cols, pcols)),
                     self.pots_override or self.inp.pots())
            if state != self.sent:
                self.br.set_input(state[0], state[1], state[2], list(state[3]), state[4])
                self.sent = state
            self.input_dirty = False

    def queue_keys(self, combos, hold=0.06, gap=0.04):
        for combo in combos:
            mods = [k for k in combo if k in MODIFIERS]
            cols, mcols = [0] * 9, [0] * 9
            for c, r in combo:
                cols[c] |= 1 << r
            for c, r in mods:
                mcols[c] |= 1 << r
            if mods and len(mods) < len(combo):
                # Like a typist: modifier down first, released last, so the key
                # scanner never sees the key without its modifier.
                self.keyq += [(0.04, mcols), (hold, cols), (0.04, mcols)]
            else:
                self.keyq.append((hold, cols))
            self.keyq.append((gap, [0] * 9))

    def run_keys(self, now):
        if self.keyq and now >= self.key_until:
            dur, cols = self.keyq.pop(0)
            self.cols = cols
            self.input_dirty = True
            self.key_until = now + dur

    # ---- helpers
    def io_stats(self):
        try:
            return open(self.args.stats).read().strip()
        except OSError:
            return "m65io=not-running"

    def temps(self):
        out = []
        for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
            try:
                out.append("%s=%.1fC" % (open(z + "/type").read().strip(),
                                         int(open(z + "/temp").read()) / 1000.0))
            except OSError:
                pass
        # FPGA die temperature: $D6DF:$D6DE (12-bit XADC) via the serial monitor
        try:
            with socket.create_connection(("127.0.0.1", self.args.mon_port), timeout=2) as s:
                s.sendall(b"mffd36de\r")
                buf, t0 = b"", time.time()
                s.settimeout(0.2)
                while b":0FFD36DE:" not in buf or len(buf.split(b":0FFD36DE:")[1]) < 4:
                    if time.time() - t0 > 2:
                        raise TimeoutError
                    try:
                        buf += s.recv(4096)
                    except socket.timeout:
                        pass
            h = buf.split(b":0FFD36DE:")[1][:4].decode()
            raw = (int(h[2:4], 16) << 4) | (int(h[0:2], 16) & 0x0F)
            out.append("fpga=%.1fC" % (raw * 503.975 / 4096 - 273.15))
        except Exception as e:     # monitor busy or m65io down
            out.append("fpga=n/a(%s)" % type(e).__name__)
        return " ".join(out)

    # ---- cores (flash slots: 0 = MEGA65 at 0x000000, 1 = C64 at 0x800000)
    CORES = {"mega65": (1, 0x000000), "c64": (2, 0x800000)}

    def monitor(self, line):
        with socket.create_connection(("127.0.0.1", self.args.mon_port), timeout=2) as s:
            s.sendall(line.encode() + b"\r")
            time.sleep(0.3)

    def switch_core(self, target):
        cur = self.br.core_id()
        if cur is None:
            return "err no bridge answer"
        names = {1: "mega65", 2: "c64"}
        if target == "toggle":
            target = "c64" if cur == 1 else "mega65"
        if target not in self.CORES:
            return "err core mega65|c64|toggle"
        want, addr = self.CORES[target]
        if want == cur:
            return "ok already %s" % target
        print("m65d: core %s -> %s (flash 0x%06x)" % (names.get(cur, cur), target, addr), flush=True)
        if cur == 1:
            # MEGA65: its own reconfig ($D6C8..$D6CB boot address, $D6CF <- $42 triggers)
            # WBSTAR takes the SPI address in 256-byte units ("shifted by 8 bits")
            w = addr >> 8
            self.monitor("sffd36c8 %02x %02x %02x %02x" % (w & 0xFF, (w >> 8) & 0xFF,
                                                           (w >> 16) & 0xFF, w >> 24))
            self.monitor("sffd36cf 42")
        else:
            self.br.iprog(addr)
        self.sent = None
        self.resend_at = time.time() + 3.0
        return "ok switching to %s" % target

    def system_action(self, act):
        if act == "reset":
            self.command("reset")
        elif act.startswith("core "):
            print("m65d: " + self.switch_core(act.split()[1]), flush=True)

    # ---- control commands
    def command(self, line):
        p = line.strip().split(" ", 1)
        if not p or not p[0]:
            return "ok"
        cmd, rest = p[0].lower(), (p[1] if len(p) > 1 else "")
        a = rest.split()
        if cmd == "status":
            st = self.br.status()
            return ("ok " + " ".join("%s=%s" % kv for kv in st.items()) + " " + self.io_stats()
                    + " inputs=" + self.inp.describe())
        if cmd == "temps":
            return "ok " + self.temps()
        if cmd == "reset":
            self.ctrl |= 1
            self.input_dirty = True
            self.reset_until = time.time() + 0.2
            return "ok"
        if cmd == "core":
            if not a:
                c = self.br.core_id()
                return "ok core=%s" % {1: "mega65", 2: "c64"}.get(c, c)
            return self.switch_core(a[0].lower())
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
        if cmd == "pots":
            # pots <p1x> <p1y> <p2x> <p2y> (hex SID values) overrides mouse/paddles; "pots off"
            self.pots_override = None if a and a[0] == "off" else tuple(int(v, 16) for v in a[:4])
            self.input_dirty = True
            return "ok"
        if cmd == "mouse":
            # mouse <dx> <dy> [buttons]: 1351 counts (dy > 0 = up), buttons b0 left b1 right;
            # "mouse off" = no mouse (pots read $FF again)
            if a and a[0] == "off":
                self.inp.mx = self.inp.my = 0.0
                self.inp.script_buttons = 0
            else:
                self.inp.move_mouse(int(a[0]), int(a[1]))
                if len(a) > 2:
                    self.inp.script_buttons = int(a[2], 0)
            self.input_dirty = True
            return "ok pots %s" % " ".join("%02x" % p for p in self.inp.pots())
        if cmd == "keys":
            self.cols = [int(v, 16) for v in a[:9]] + [0] * (9 - len(a[:9]))
            self.input_dirty = True
            return "ok"
        if cmd == "key":
            hold = int(a[1]) / 1000.0 if len(a) > 1 else 0.06
            self.queue_keys([key_combo(a[0])], hold=hold)
            return "ok"
        if cmd in ("type", "typecase"):
            text = rest.replace("\\n", "\n")
            combos = []
            try:
                for ch in text:
                    c = key_combo(ch)
                    if cmd == "typecase" and ch.isalpha() and ch.isupper():
                        c = [KEYPOS["LSHIFT"]] + c
                    combos.append(c)
            except KeyError as e:
                return "err no key for %r" % e.args[0]
            self.queue_keys(combos)
            return "ok %d keys" % len(text)
        return "err unknown command"

    # ---- main loop
    def loop(self):
        while True:
            now = time.time()
            if self.reset_until and now >= self.reset_until:
                self.ctrl &= ~1
                self.input_dirty = True
                self.reset_until = 0.0
            self.run_keys(now)
            if self.resend_at and now >= self.resend_at:     # new core is up: resend inputs
                self.sent = None
                self.input_dirty = True
                self.resend_at = 0.0
            while self.inp.actions:
                self.system_action(self.inp.actions.pop(0))
            self.push_input()
            if now >= self.next_scan:              # hot-plug
                self.inp.scan()
                self.next_scan = now + 2.0
            evfds = self.inp.fds()
            socks = [self.ctl_srv] + list(self.ctl_clients) + evfds
            r, _, _ = select.select(socks, [], [], 0.005 if self.keyq else 0.05)
            for s in r:
                if isinstance(s, int):                # evdev device
                    if self.inp.handle(s):
                        self.input_dirty = True
                    continue
                if s is self.ctl_srv:
                    c, _ = s.accept()
                    self.ctl_clients[c] = b""
                    continue
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
    ap.add_argument("--hz", type=int, default=10000000)
    ap.add_argument("--ctl-port", type=int, default=4511)
    ap.add_argument("--mon-port", type=int, default=4510, help="m65io monitor pipe (for temps)")
    ap.add_argument("--stats", default="/root/m65/m65io.stats")
    Daemon(ap.parse_args()).loop()


if __name__ == "__main__":
    main()
