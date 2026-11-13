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
    core [mega65|slot1|c64|amiga|toggle]   show / switch the FPGA core (flash slot 0 / 1; slot 1
                               holds the C64 or the Amiga core: M65_SLOT1=c64|amiga in m65d.env,
                               default c64); USB keyboard: Ctrl+Alt+F1 / F2 / F12, Ctrl+Alt+Del = reset
    mouse <dx> <dy> [btn]      move the mouse (1351 on port 2 / Amiga mouse on port 1; counts,
                               dy>0 = up; btn b0 L b1 R b2 M)
    mouse off                  no mouse (POTs read $FF)
    pots <p1x> <p1y> <p2x> <p2y>   raw SID POT values in hex (paddles); "pots off" = back to mouse
    amiga [status|import]      the Amiga core's Linux shell (bitstreams with a host master: the
                               M2M menu runs here instead of QNICE); import = copy the Kickstart
                               files (*.rom), aexp_screen.cfg and the ADFs from card.img's /amiga
                               to the module's Amiga directory (never overwriting)
    restore 0|1  capslock 0|1  sd emu|phys
    rtc                        set the MEGA65's clock now (also on core start and hourly)
    poweroff                   shut the Rockchip down safely (USB keyboard: Ctrl+Alt+End)
    view linux|mega65|toggle   HDMI picture + keyboard: Linux (linux_fb) or the MEGA65 (Ctrl+Alt+Insert)
    inputs                     live USB input state: joystick bits per port, POTs, keys, devices
    joyswap                    swap joystick ports 1 <-> 2 (pads and mouse; USB keyboard: Ctrl+Alt+J)
    latency [reset]            USB input event (kernel timestamp) -> INPUT register written:
                               p50/p90/p99/max per device kind, last 4000 updates
Run as root (spidev3.0 is root-only).
"""
import argparse
import subprocess
import glob
import os
import select
import socket
import sys
import time
import traceback


import m65input

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lxshell"))


def ntp_synced():
    """True once systemd-timesyncd (or another NTP client) has set the clock.  systemd-timesyncd
    (>= 239) keeps /run/systemd/timesync/synchronized while it is synchronised: read that, so that
    no systemd-timedated is started (timedatectl did: its PrivateTmp directories in /var/tmp are
    written to the module's flash each time)."""
    if os.path.isdir("/run/systemd/timesync"):
        return os.path.exists("/run/systemd/timesync/synchronized")
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return True                   # cannot tell: behave as before


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
        import spidev                     # here, so the PC can import m65d (KEYPOS, tests)
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
        """STATUS byte 52: 1 = MEGA65, 2 = C64, 3 = Amiga (0 = older MEGA65 bitstream)"""
        r = self.spi.xfer2([0x01, 0] + [0] * 53)[2:]
        if bytes(r[0:2]) != b"Me":
            return None
        return r[52] or 1

    def iprog(self, addr):
        self.spi.xfer2([0x60, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF,
                        addr & 0xFF, 0xA5])

    def set_video(self, sel):
        """0x70 VIDEO: b0 = 1 the Rockchip's own picture (linux_fb), b1 = BT1120 rising-edge capture"""
        self.spi.xfer2([0x70, sel & 0xFF])

    def video_diag(self):
        """STATUS 59..63: VIDEO register + linux_fb diagnostics (older bitstreams: zeros)"""
        r = self.spi.xfer2([0x01, 0] + [0] * 72)[2:]
        d, w = r[60:64], r[64:72]
        return {"sel": r[59], "frames": d[0], "lines": d[1] << 4 | d[2] >> 4,
                "rise": d[2] >> 3 & 1, "de_seen": d[2] >> 2 & 1, "yc_swapped": d[2] >> 1 & 1,
                "locked": d[2] & 1, "px_per_line": d[3] << 4,
                # raw bus per 2^20-clock window: lines that were high / low at least once
                "seen_hi": "%04x" % (w[0] << 8 | w[1]), "seen_lo": "%04x" % (w[2] << 8 | w[3]),
                "ff_lo": w[4], "ff_hi": w[5], "clk64k": w[6] << 8 | w[7]}

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
        try:
            self.inp.maintain(time.monotonic())
        except Exception:                 # the main loop retries
            traceback.print_exc()
        self.core = None
        self.next_core_check = 0.0
        self.rtc_due = 0
        self.rtc_repeat = False
        self.last_down = None
        self.sent = None
        self.lat = []           # (device kind, event -> SPI write seconds), last 4000
        self.lx = None          # the Amiga core's Linux shell (lxshell/shell.py), when attached
        self.lx_retry = 0.0
        self.lx_kbd = None
        self.lx_core = None
        self.lx_probed = False
        self.lx_settle = 0.0
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
            ctrl = self.ctrl | self.inp.ctrl_bits()
            cols = [a | b for a, b in zip(self.cols, pcols)]
            if self.lx is not None:
                # the Amiga menu owns the keyboard while it is open; the open key never reaches
                # the core (mouse and joysticks: unchanged, the core's CSR gates port 2)
                cols, kctrl = self.lx.filter_input(cols, ctrl)
                ctrl = (ctrl & ~0x30) | (kctrl & 0x30)
            state = (ctrl, self.ja | pj1, self.jb | pj2, tuple(cols),
                     self.pots_override or self.inp.pots())
            pend = self.inp.take_pending()
            if state != self.sent:
                self.br.set_input(state[0], state[1], state[2], list(state[3]), state[4])
                self.sent = state
                if pend:      # USB event (kernel timestamp) -> INPUT register written
                    self.lat.append((pend[1], time.monotonic() - pend[0]))
                    del self.lat[:-4000]
            self.input_dirty = False

    def latency(self, arg):
        if arg == "reset":
            self.lat = []
            return "ok"
        out = []
        for kind in ("all", "kbd", "pad", "mouse"):
            v = sorted(l for k, l in self.lat if kind in ("all", k))
            if v:
                q = lambda f: v[min(len(v) - 1, int(f * len(v)))] * 1000
                out.append("%s n=%d p50=%.2f p90=%.2f p99=%.2f max=%.2f ms"
                           % (kind, len(v), q(.5), q(.9), q(.99), v[-1] * 1000))
        return "ok " + ("; ".join(out) or "no samples") + "; syn_dropped=%d" % self.inp.dropped

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

    # ---- cores (flash slots: 0 = MEGA65 at 0x000000, 1 = C64 or Amiga at 0x800000)
    CORE_IDS = {"mega65": 1, "c64": 2, "amiga": 3}
    CORE_NAMES = {1: "mega65", 2: "c64", 3: "amiga"}

    @property
    def slot1(self):
        """name of the core in flash slot 1 (M65_SLOT1 in m65d.env)"""
        n = os.environ.get("M65_SLOT1", "c64").strip().lower()
        return n if n in ("c64", "amiga") else "c64"

    def monitor(self, line, wait=True):
        with socket.create_connection(("127.0.0.1", self.args.mon_port), timeout=2) as s:
            s.sendall(line.encode() + b"\r")
            if wait:
                time.sleep(0.3)
            # (closing at once is fine: m65io queues what it received for the monitor)

    def monitor_read(self, addr, n):
        """read n <= 256 bytes through the monitor ('M' dumps 256 from addr)"""
        with socket.create_connection(("127.0.0.1", self.args.mon_port), timeout=2) as s:
            s.settimeout(1.0)
            s.sendall(b"M%x\r" % addr)
            out, t0 = b"", time.time()
            while not out.rstrip(b"\r\n ").endswith(b"\n.") and time.time() - t0 < 2:
                try:
                    out += s.recv(65536)
                except socket.timeout:
                    break
        data = b""
        for line in out.decode(errors="replace").splitlines():
            if line.startswith(":") and line.count(":") >= 2:
                data += bytes.fromhex(line.split(":")[2].strip())
        return data[:n]

    # ---- real-time clock: soft_rtc in the MEGA65 bitstream ($FFD7110..), no battery: set it
    # from the Rockchip's NTP-synchronised local time, at a second boundary, in one monitor
    # command starting with the seconds (which restarts the clock's sub-second counter)
    def rtc_schedule(self, delay=0.0):
        self.rtc_due = int(time.time() + delay) + 1

    def bridge_down(self):
        """m65io's count of status polls without the bridge (FPGA configuring, link problems)"""
        try:
            for f in open(self.args.stats).read().split():
                if f.startswith("bridge_down="):
                    return int(f.split("=")[1])
        except (OSError, ValueError):
            pass
        return None

    def rtc_write(self, immediate=False):
        # immediate: right now, unaligned (a core just appeared: BASIC prints its banner from the
        # RTC about 4 s after the reconfiguration, so the first write must not wait for a second
        # boundary; the aligned repeat 2 s later restores the sub-second accuracy)
        t = time.time() if immediate else self.rtc_due
        if not immediate:
            self.rtc_due = int(t) + 3600              # and again every hour
        if self.core != 1:
            return
        if not ntp_synced():
            # no battery clock: until NTP has synchronised, the Rockchip's time is stale
            # (2026-11-07 boot: set the MEGA65 to March) -- try again shortly
            self.rtc_due = time.time() + 20
            return
        if not immediate and abs(time.time() - t) > 1.0:
            # the due second was computed on a clock that has been stepped since (NTP's first
            # sync after a cold boot: +40 s on the Radxa CM3, 2026-11-10 -- it wrote the stale
            # second): write the current time now and an aligned copy 2 s later
            t = time.time()
            self.rtc_schedule(2.0)
        lt = time.localtime(t)
        bcd = lambda v: (v // 10) << 4 | v % 10
        regs = [bcd(lt.tm_sec), bcd(lt.tm_min), bcd(lt.tm_hour) | 0x80, bcd(lt.tm_mday),
                bcd(lt.tm_mon), bcd(lt.tm_year % 100), (lt.tm_wday + 1) % 7]   # 0 = Sunday
        try:
            self.monitor("sffd7110 " + " ".join("%02x" % v for v in regs), wait=False)
            print("m65d: RTC set to %s" % time.strftime("%Y-%m-%d %H:%M:%S", lt), flush=True)
            if self.rtc_repeat:                       # a core start: set it again shortly, in
                self.rtc_repeat = False               # case the first write met the boot
                self.rtc_schedule(5.0)
        except OSError as e:
            print("m65d: RTC set failed: %s" % e, flush=True)

    # ---- safe power off (Ctrl+Alt+End): MEGA65 screen says "shutting down", the Rockchip shuts
    # down; /lib/systemd/system-shutdown/m65-safe.shutdown replays /run/m65-shutdown-msg ("safe
    # to switch off", green border) once all file systems are read-only, just before the halt.
    # With plugd running on the PC that drives the smart plug (--plug-host), the hook also
    # sends it UDP "halted" (/run/m65-plug-host) and plugd cuts the power.
    def screen_text(self, text, border):
        """monitor commands writing text into the top row of the current text screen"""
        r = self.monitor_read(0xFFD3000, 0x80)
        if len(r) < 0x80:
            return []
        scr = r[0x60] | r[0x61] << 8 | r[0x62] << 16
        col = 0xFF80000 + (r[0x64] | r[0x65] << 8)
        chr16 = r[0x54] & 1
        code = lambda c: ord(c) - 64 if "A" <= c <= "Z" else ord(c)
        cells = [code(c) for c in text]
        if chr16:
            scr_b = sum(([c, 0] for c in cells), [])
            col_b = sum(([0, 1] for c in cells), [])
        else:
            scr_b, col_b = cells, [1] * len(cells)
        cmds = ["sffd3020 %02x %02x" % (border, border)]
        for base, data in ((scr, scr_b), (col, col_b)):
            for i in range(0, len(data), 16):        # the monitor takes short lines only
                cmds.append("s%x %s" % (base + i, " ".join("%02x" % v for v in data[i:i + 16])))
        return cmds

    def request_plug(self):
        """ask plugd (on the PC with the smart plug) to cut the power after the halt"""
        if not self.args.plug_host:
            return False
        host, _, port = self.args.plug_host.partition(":")
        port = int(port or 4530)
        try:
            with socket.create_connection((host, port), timeout=2) as c:
                c.sendall(b"poweroff\n")
                ok = c.recv(16).startswith(b"ok")
        except OSError as e:
            print("m65d: plugd at %s: %s" % (self.args.plug_host, e), flush=True)
            return False
        if ok:
            with open("/run/m65-plug-host", "w") as f:
                f.write("%s %d\n" % (host, port))
        return ok

    def poweroff(self):
        if self.inp.linux_focus:                # the halt message is on the MEGA65 picture
            self.view("mega65")
        plug = self.request_plug()
        print("m65d: power off (Ctrl+Alt+End)%s" % (", plugd cuts the power" if plug else ""), flush=True)
        if self.core == 1:
            if plug:
                red, green = "SHUTTING DOWN - POWER OFF FOLLOWS", "ROCKCHIP HALTED - POWER OFF NOW"
            else:
                red, green = "SHUTTING DOWN - WAIT FOR: SAFE TO SWITCH OFF", "ROCKCHIP HALTED - SAFE TO SWITCH OFF"
            try:
                # build BOTH messages first: screen_text reads the VIC registers through the
                # monitor, and a read right after the red commands collided with their replies
                # (empty green message -> the screen stayed red)
                red_cmds = self.screen_text(("  " + red).ljust(50), 2)
                cmds = self.screen_text(("  " + green).ljust(50), 5)
                with open("/run/m65-shutdown-msg", "w") as f:
                    # leading CRs: end whatever half line the monitor still holds, or the
                    # first command (the green border) merges with it and is lost
                    f.write("\r\r" + "".join(c + "\r" for c in cmds))
                print("m65d: halted message: %d monitor commands" % len(cmds), flush=True)
                for c in red_cmds:
                    self.monitor(c, wait=False)
            except OSError as e:
                print("m65d: shutdown message: %s" % e, flush=True)
        subprocess.Popen(["systemctl", "poweroff"])

    def switch_core(self, target):
        cur = self.br.core_id()
        if cur is None:
            return "err no bridge answer"
        names = self.CORE_NAMES
        if target == "toggle":
            target = "slot1" if cur == 1 else "mega65"
        if target == "slot1":
            target = self.slot1
        if target not in self.CORE_IDS:
            return "err core mega65|slot1|c64|amiga|toggle"
        if target != "mega65" and target != self.slot1:
            return "err flash slot 1 holds the %s core (M65_SLOT1 in m65d.env)" % self.slot1
        want, addr = self.CORE_IDS[target], (0x000000 if target == "mega65" else 0x800000)
        if want == cur:
            return "ok already %s" % target
        print("m65d: core %s -> %s (flash 0x%06x)" % (names.get(cur, cur), target, addr), flush=True)
        if self.inp.linux_focus:                # the reload resets the picture to the core's
            self.inp.set_linux_focus(False)
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
        self.next_core_check = time.time() + 3.0   # re-read the core id once the new one runs
        return "ok switching to %s" % target

    def system_action(self, act):
        if act == "poweroff":
            self.poweroff()
            return
        if act.startswith("view "):
            print("m65d: " + self.view(act.split()[1]), flush=True)
            return
        if act == "reset":
            self.command("reset")
        elif act.startswith("core "):
            print("m65d: " + self.switch_core(act.split()[1]), flush=True)

    # ---- one HDMI output, two pictures: the core's, or the Rockchip's own (linux_fb in the
    # K325T bitstream, fed over BT1120).  The keyboard follows the picture.
    def view(self, target):
        if target in ("rise", "fall"):          # BT1120 capture edge (bring-up / diagnosis)
            self.vid_edge = 2 if target == "rise" else 0
            self.br.set_video((1 if self.inp.linux_focus else 0) | self.vid_edge)
            return "ok view capture edge " + target
        if target == "diag":
            a = self.br.video_diag()
            time.sleep(0.2)
            b = self.br.video_diag()
            b["frames_per_s"] = ((b["frames"] - a["frames"]) & 0xFF) * 5
            b["bt_clk_mhz"] = round(((b["clk64k"] - a["clk64k"]) & 0xFFFF) * 65536 * 5 / 1e6, 1)
            return "ok " + " ".join("%s=%s" % kv for kv in b.items())
        if target == "toggle":
            target = "mega65" if self.inp.linux_focus else "linux"
        if target not in ("linux", "mega65"):
            return "err view linux|mega65|toggle|rise|fall|diag"
        if target == "linux":
            # the keyboard is released: the console sees the hotkey too.  Ctrl+Alt+Insert
            # would type ESC [2~ there (vim: insert mode) -- void it in the console keymap
            try:
                subprocess.run(["loadkeys"], input=b"control alt keycode 110 = VoidSymbol\n",
                               capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        self.br.set_video((1 if target == "linux" else 0) | getattr(self, "vid_edge", 0))
        self.inp.set_linux_focus(target == "linux")
        return "ok view " + target

    # ---- the Amiga core's Linux shell (host-master bitstreams; QNICE bitstreams: nothing changes)
    def lx_check(self, core, reconfigured):
        # Any core other than the Amiga (id 3): nothing at all, not one extra SPI transfer.  The
        # Amiga: one STATUS read per FPGA configuration tells a host-master build (byte 72 != 0)
        # from a QNICE build; a QNICE build is then left alone until the next configuration.
        if reconfigured or core != self.lx_core:
            self.lx_core = core
            self.lx_probed = False
            self.lx_settle = time.time() + 1.0   # m65io's reconfiguration count lags (0.5 s stats)
        if self.lx is not None and (core != 3 or reconfigured):
            self.lx.detach()
            self.lx = None
            print("m65d: lxshell detached (%s)" % ("FPGA reconfigured" if reconfigured else "other core"),
                  flush=True)
        if core != 3 or self.lx is not None or self.lx_probed or time.time() < self.lx_retry \
                or time.time() < self.lx_settle:
            return
        self.lx_retry = time.time() + 2.0
        try:
            from lxcore.bus import SpiLink
            from lxamiga import m2m
            from lxamiga.shell import AmigaShell
            link = SpiLink(self.br.spi)
            if link.status().version == 0:
                self.lx_probed = True   # a QNICE bitstream: the firmware serves the menu
                return
            lx = AmigaShell(m2m.M2MBus(link), self.args.amiga_menus, self.args.amiga_dir,
                                    card_img=self.args.card, state_path=self.args.amiga_state,
                                    log=lambda m: print("m65d: " + m, flush=True))
            lx.attach()
            self.lx = lx
        except Exception:
            traceback.print_exc()
            sys.stdout.flush()

    def lx_step(self):
        # keys to the shell and its poll; returns the seconds it wants until the next call
        if self.lx is None:
            return 1.0
        try:
            cols = [a | b for a, b in zip(self.cols, self.inp.matrix())]
            ctrl = self.ctrl | self.inp.ctrl_bits()
            self.lx.keys(cols, ctrl, time.monotonic())
            wait = self.lx.poll(time.monotonic())
            if self.lx.kbd_to_core != self.lx_kbd:     # menu opened / closed: re-push the keyboard
                self.lx_kbd = self.lx.kbd_to_core
                self.input_dirty = True
            return wait
        except Exception:
            traceback.print_exc()
            sys.stdout.flush()
            self.lx.detach()
            self.lx = None
            return 0.5

    def amiga(self, arg):
        if arg in ("", "status"):
            if self.lx is None:
                return "ok lxshell=off (core %s)" % self.CORE_NAMES.get(self.core, self.core)
            x = self.lx
            return "ok lxshell=on task=%s menu=%s kbd_to_core=%s adf=%s rtc=%s bus=%s" % (
                x.task_kind, x.menu_open, x.kbd_to_core, x.adf, x.rtc_on(),
                ",".join("%s=%d" % kv for kv in x.bus.stats.items()))
        if arg == "import":
            from lxcore import fatimg
            dst = self.args.amiga_dir
            os.makedirs(dst, exist_ok=True)
            img = fatimg.FatImage(self.args.card)
            done = []
            try:
                for name, is_dir, size in img.list("/amiga"):
                    low = name.lower()
                    if is_dir or not (low.endswith((".adf", ".rom")) or low == "aexp_screen.cfg"):
                        continue
                    target = os.path.join(dst, name)
                    if os.path.exists(target):
                        continue          # never overwrite the module's own copy
                    with open(target + ".tmp", "wb") as f:
                        f.write(img.read("/amiga/" + name))
                    os.replace(target + ".tmp", target)
                    done.append(name)
            finally:
                img.close()
            return "ok imported %d file(s): %s" % (len(done), " ".join(done) or "-")
        return "err amiga [status|import]"

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
        if cmd == "amiga":
            return self.amiga(rest.strip())
        if cmd == "latency":
            return self.latency(rest.strip())
        if cmd == "view":
            # view linux|mega65|toggle: HDMI picture + keyboard focus (also Ctrl+Alt+Insert)
            # view rise|fall: BT1120 capture edge; view diag: linux_fb receiver state
            return self.view(a[0] if a else "toggle")
        if cmd == "rtc":
            self.rtc_schedule()
            return "ok RTC set at the next full second"
        if cmd == "poweroff":
            self.poweroff()
            return "ok shutting down"
        if cmd == "inputs":
            # live view of the USB input state, e.g. while pressing pad buttons
            j1, j2 = self.inp.joysticks()
            bits = lambda j: "".join(n if j >> i & 1 else "-" for i, n in enumerate("UDLRF"))
            pads = " ".join("pad@port%d[hat=%d,%d stick=%d,%d dpad=%x fire=%s keys=%s]" % (
                p.port, p.hat[0], p.hat[1], p.stick[0], p.stick[1], p.dpad,
                ",".join("%x" % c for c in sorted(p.fire)) or "-",
                ",".join("%x" % c for c in sorted(p.keys)) or "-") for p in self.inp.pads)
            return "ok port1=%s port2=%s %s pots=%s keys=%s ports=%s devices=%s" % (
                bits(j1), bits(j2), pads, ",".join("%02x" % p for p in self.inp.pots()),
                ",".join(str(k) for k in sorted(self.inp.kbd_keys)) or "-",
                "swapped" if self.inp.joy_swap else "normal", self.inp.describe())
        if cmd == "joyswap":
            self.inp.swap_ports()
            self.input_dirty = True
            return "ok ports %s" % ("swapped" if self.inp.joy_swap else "normal")
        if cmd == "reset":
            self.ctrl |= 1
            self.input_dirty = True
            self.reset_until = time.time() + 0.2
            return "ok"
        if cmd == "keylog":
            # last USB keyboard events "code:value" (1 press, 0 release, 2 repeat) + matrix
            return "ok %s | matrix %s" % (" ".join(self.inp.keylog),
                                           " ".join("%02x" % c for c in self.inp.matrix()))
        if cmd == "core":
            if not a:
                c = self.br.core_id()
                return "ok core=%s slot1=%s" % (self.CORE_NAMES.get(c, c), self.slot1)
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
            # mouse <dx> <dy> [buttons]: counts (dy > 0 = up), buttons b0 left b1 right b2 middle;
            # "mouse off" = no mouse (pots read $FF again)
            if a and a[0] == "off":
                for ax in (self.inp.ax, self.inp.ay):   # in place: keeps the core's rate
                    ax.target = ax.out = 0.0
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
            try:
                self.step()
            except Exception:                     # keep the keyboard alive; log and go on
                traceback.print_exc()
                sys.stdout.flush()
                time.sleep(0.1)

    def step(self):
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
        if now >= self.next_core_check:        # which core runs: cursor key encoding
            self.next_core_check = now + 0.25
            c = self.br.core_id()
            down = self.bridge_down()
            if c == 1 and down is not None and self.last_down is not None and down > self.last_down:
                self.rtc_write(immediate=True)        # the FPGA was away (reconfigured): set it again
                self.rtc_schedule(2.0)
                self.rtc_repeat = True
            reconf = down is not None and self.last_down is not None and down > self.last_down
            if down is not None:
                self.last_down = down
            self.lx_check(c, reconf)
            if c is not None and c != self.core:
                self.core = c
                if c == 1:
                    # picture = keyboard focus: the VIDEO register survives an m65d restart or a
                    # Rockchip reboot (the FPGA is not reconfigured), m65d starts with the keyboard
                    # on the MEGA65 -- a Linux picture left on would show Linux to a MEGA65 keyboard
                    self.br.set_video((1 if self.inp.linux_focus else 0) | getattr(self, "vid_edge", 0))
                    self.rtc_write(immediate=True)    # MEGA65 (re)configured: its RTC is unset;
                    self.rtc_schedule(2.0)            # write now, aligned again in 2 s and 5 s
                    self.rtc_repeat = True            # later (BASIC's banner reads the RTC once,
                                                      # ~4 s after the reconfiguration)
                self.inp.set_core(c)        # C64 / Amiga: cursor up/left dedicated keys only;
                self.input_dirty = True     # Amiga: its key map and quadrature mouse
                print("m65d: core %s, cursor up/left %s%s" % (
                    self.CORE_NAMES.get(c, c),
                    "as dedicated keys + RSHIFT/cursor matrix" if self.inp.matrix_cursor
                    else "as dedicated keys only",
                    ", Amiga key map + mouse" if self.inp.amiga else ""), flush=True)
        if self.rtc_due and time.time() >= self.rtc_due:
            self.rtc_write()
        changed, wait = self.inp.maintain(time.monotonic())   # hot-plug, mouse rate limit
        wait = min(wait, self.lx_step())
        if self.rtc_due:
            wait = min(wait, max(0.0, self.rtc_due - time.time()))
        if changed:
            self.input_dirty = True
        self.push_input()
        evfds = self.inp.fds()
        socks = [self.ctl_srv] + list(self.ctl_clients) + evfds
        lead = self.inp.lead_pending()
        if lead:
            self.input_dirty = True          # modifier-first combo: key part follows
        r, _, _ = select.select(socks, [], [], min(wait, 0.005 if self.keyq or lead else 0.05))
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
    try:      # input latency: run before ordinary processes (the service sets this too)
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=int, default=10000000)
    ap.add_argument("--ctl-port", type=int, default=4511)
    ap.add_argument("--mon-port", type=int, default=4510, help="m65io monitor pipe (for temps)")
    ap.add_argument("--stats", default="/run/m65io.stats")   # m65io writes it twice a second: RAM
    ap.add_argument("--amiga-dir", default=os.environ.get("M65_AMIGA_DIR", "/root/m65/amiga"),
                    help="the Amiga core's files: kick.rom, amiga.settings, ADFs")
    ap.add_argument("--amiga-menus", default=os.environ.get("M65_AMIGA_MENUS", "/root/m65/menus"),
                    help="directory of *.menu files (menu_gen.py); the one matching the FPGA's id is used")
    ap.add_argument("--amiga-state", default="/run/m65/amiga.state",
                    help="the mounted ADF across an m65d restart (RAM: /run)")
    ap.add_argument("--card", default=os.environ.get("M65_CARD", "/root/m65/card.img"),
                    help="card image read (never written) to take over kick.rom / settings / ADFs")
    ap.add_argument("--plug-host", default=os.environ.get("M65_PLUG_HOST", ""),
                    help="plugd (scripts/plugd.py) on the PC with the smart plug; '' = none")
    Daemon(ap.parse_args()).loop()


if __name__ == "__main__":
    main()
