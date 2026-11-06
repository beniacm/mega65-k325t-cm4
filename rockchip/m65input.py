#!/usr/bin/env python3
"""m65input.py -- USB keyboard + USB gamepad bridge for m65d (Rockchip side).

Reads Linux evdev devices (/dev/input/event*) directly (no python-evdev needed),
grabs them exclusively (so keystrokes don't also reach the Rockchip console),
and turns them into the MEGA65 keyboard matrix, joystick bits and the
RESTORE / CAPS LOCK lines that m65d writes to rk_bridge's INPUT register.
Devices are hot-plugged: /dev/input is rescanned every couple of seconds.

Keyboard: positional mapping like a C64/MEGA65 keyboard on a PC layout
(close to VICE's positional map).  Specials:
    Esc = RUN/STOP      PgUp = RESTORE     Home = CLR/HOME    Backspace/Del = INST/DEL
    Insert = INST       End = up-arrow     PgDn = pound       PrtSc = HELP
    Tab = TAB  Ctrl = CTRL  Alt = ALT  Win = MEGA  ScrollLock = NO SCROLL
    CapsLock = CAPS LOCK (toggles)     cursor keys incl. up/left (= SHIFT+down/right)
    F1/F3/F5/F7/F9/F11 direct, F2/F4/F6/F8/F10/F12 = SHIFT + the odd key
    ` = left-arrow  - = +  = = -  [ = @  ] = *  ; = :  ' = ;  \\ = =   keypad digits/operators
Gamepad: first pad -> joystick port 2 (most C64 games), second pad -> port 1.
    D-pad / hat / left stick = directions; A/B/X/Y, shoulders, triggers = fire;
    Start = SPACE, Select = RETURN, Mode/Home = RUN/STOP.
"""
import errno
import fcntl
import glob
import os
import struct

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
EVENT_FMT = "llHHi"                      # struct input_event (timeval + type/code/value)
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EVIOCGRAB = 0x40044590
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11


def EVIOCGABS(axis):
    return 0x80184540 + axis              # _IOR('E', 0x40 + axis, struct input_absinfo)


# Linux keycode -> MEGA65 matrix key names (combos for keys that need SHIFT)
S = "LSHIFT"
KEYMAP = {
    1: ["RUNSTOP"], 59: ["F1"], 60: [S, "F1"], 61: ["F3"], 62: [S, "F3"], 63: ["F5"], 64: [S, "F5"],
    65: ["F7"], 66: [S, "F7"], 67: ["F9"], 68: [S, "F9"], 87: ["F11"], 88: [S, "F11"],
    183: ["F13"], 184: [S, "F13"],                                   # F13/F14 if present
    41: ["LEFTARROW"], 12: ["+"], 13: ["-"], 14: ["INST"], 111: ["INST"], 110: [S, "INST"],
    15: ["TAB"], 26: ["@"], 27: ["*"], 43: ["="], 28: ["RETURN"], 96: ["RETURN"],
    39: [":"], 40: [";"], 51: [","], 52: ["."], 53: ["/"], 57: ["SPACE"],
    42: ["LSHIFT"], 54: ["RSHIFT"], 29: ["CTRL"], 97: ["CTRL"], 56: ["ALT"], 100: ["ALT"],
    125: ["MEGA"], 126: ["MEGA"], 70: ["NOSCRL"], 99: ["HELP"],
    102: ["HOME"], 107: ["UPARROW"], 109: ["POUND"],
    108: ["DOWN"], 106: ["RIGHT"], 103: [S, "DOWN"], 105: [S, "RIGHT"],
    55: ["*"], 74: ["-"], 78: ["+"], 98: ["/"], 83: ["."],
    82: ["0"], 79: ["1"], 80: ["2"], 81: ["3"], 75: ["4"], 76: ["5"], 77: ["6"],
    71: ["7"], 72: ["8"], 73: ["9"],
}
for i, ch in enumerate("1234567890"):
    KEYMAP[2 + i] = [ch]
for code, ch in zip([16, 17, 18, 19, 20, 21, 22, 23, 24, 25], "QWERTYUIOP"):
    KEYMAP[code] = [ch]
for code, ch in zip([30, 31, 32, 33, 34, 35, 36, 37, 38], "ASDFGHJKL"):
    KEYMAP[code] = [ch]
for code, ch in zip([44, 45, 46, 47, 48, 49, 50], "ZXCVBNM"):
    KEYMAP[code] = [ch]
KEY_CAPSLOCK, KEY_PAGEUP = 58, 104       # handled as lines, not matrix keys

# Gamepad buttons
BTN_FIRE = set(range(0x120, 0x12c)) | {0x130, 0x131, 0x132, 0x133, 0x134, 0x135,
                                       0x136, 0x137, 0x138, 0x139}  # joystick + A B C X Y Z TL TR TL2 TR2
BTN_SELECT, BTN_START, BTN_MODE = 0x13a, 0x13b, 0x13c
BTN_DPAD = {0x220: 0, 0x221: 1, 0x222: 2, 0x223: 3}                  # up down left right -> joy bit
PAD_KEYS = {BTN_START: ["SPACE"], BTN_SELECT: ["RETURN"], BTN_MODE: ["RUNSTOP"]}


def _bitmap(hexwords):
    """'/proc/bus/input/devices' bitmap ("B: KEY=...") -> set of bit numbers."""
    bits, words = set(), hexwords.split()
    for i, w in enumerate(reversed(words)):
        v = int(w, 16)
        for b in range(64):
            if v >> b & 1:
                bits.add(i * 64 + b)
    return bits


class Pad:
    def __init__(self, fd):
        self.fd = fd
        self.dirs = [0, 0, 0, 0]          # up down left right from: hat/stick/dpad (sources OR'd)
        self.hat = [0, 0]
        self.stick = [0, 0]
        self.dpad = 0
        self.fire = set()
        self.keys = set()
        self.ranges = {}
        for ax in (ABS_X, ABS_Y):
            try:
                buf = fcntl.ioctl(fd, EVIOCGABS(ax), b"\0" * 24)
                _, mn, mx, _, _, _ = struct.unpack("6i", buf)
                if mx > mn:
                    self.ranges[ax] = (mn, mx)
            except OSError:
                pass

    def joy(self):
        up = self.hat[1] < 0 or self.stick[1] < 0 or self.dpad & 1
        down = self.hat[1] > 0 or self.stick[1] > 0 or self.dpad & 2
        left = self.hat[0] < 0 or self.stick[0] < 0 or self.dpad & 4
        right = self.hat[0] > 0 or self.stick[0] > 0 or self.dpad & 8
        return (bool(up) | bool(down) << 1 | bool(left) << 2 | bool(right) << 3
                | bool(self.fire) << 4)


class InputHub:
    def __init__(self, keypos, log=print):
        self.keypos = keypos               # MEGA65 key name -> (column, row)
        self.log = log
        self.devs = {}                      # fd -> (path, kind, name)
        self.paths = {}                     # path -> fd
        self.kbd_keys = set()              # pressed linux keycodes (all keyboards)
        self.pads = []                      # Pad objects in connection order
        self.caps = False
        self.restore = False

    # ---- discovery
    def scan(self):
        info = {}
        try:
            blocks = open("/proc/bus/input/devices").read().split("\n\n")
        except OSError:
            return
        for b in blocks:
            name, ev, keys, absb, handler = "", set(), set(), set(), None
            for line in b.splitlines():
                if line.startswith("N: Name="):
                    name = line[8:].strip().strip('"')
                elif line.startswith("H: Handlers="):
                    for h in line[12:].split():
                        if h.startswith("event"):
                            handler = "/dev/input/" + h
                elif line.startswith("B: EV="):
                    ev = _bitmap(line[6:])
                elif line.startswith("B: KEY="):
                    keys = _bitmap(line[7:])
                elif line.startswith("B: ABS="):
                    absb = _bitmap(line[7:])
            if not handler:
                continue
            if keys & {0x130, 0x120} or (absb & {ABS_X, ABS_HAT0X} and keys & BTN_FIRE):
                info[handler] = ("pad", name)
            elif EV_KEY in ev and {30, 57, 28} <= keys:     # has A, SPACE, ENTER
                info[handler] = ("kbd", name)
        for path, (kind, name) in info.items():
            if path in self.paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError:
                pass
            self.devs[fd] = (path, kind, name)
            self.paths[path] = fd
            if kind == "pad":
                self.pads.append(Pad(fd))
            self.log("m65input: + %s %s (%s)" % (kind, path, name))

    def _drop(self, fd):
        path, kind, name = self.devs.pop(fd)
        self.paths.pop(path, None)
        self.pads = [p for p in self.pads if p.fd != fd]
        os.close(fd)
        self.kbd_keys.clear()             # don't leave keys stuck after an unplug
        self.log("m65input: - %s %s (%s)" % (kind, path, name))

    def fds(self):
        return list(self.devs)

    def describe(self):
        return " ".join("%s:%s" % (k, n.replace(" ", "_")) for _, k, n in self.devs.values()) or "none"

    # ---- events
    def handle(self, fd):
        """read pending events from fd; True if the MEGA65-visible state may have changed"""
        try:
            data = os.read(fd, EVENT_SIZE * 64)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            self._drop(fd)
            return True
        if not data:
            self._drop(fd)
            return True
        kind = self.devs[fd][1]
        pad = next((p for p in self.pads if p.fd == fd), None)
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, typ, code, val = struct.unpack_from(EVENT_FMT, data, i)
            if typ == EV_KEY:
                if kind == "kbd":
                    if code == KEY_CAPSLOCK:
                        if val == 1:
                            self.caps = not self.caps       # latching key, like the real one
                    elif code == KEY_PAGEUP:
                        self.restore = val != 0
                    elif val:                               # 1 = press, 2 = autorepeat
                        self.kbd_keys.add(code)
                    else:
                        self.kbd_keys.discard(code)
                elif pad:
                    if code in BTN_DPAD:
                        bit = 1 << BTN_DPAD[code]
                        pad.dpad = (pad.dpad | bit) if val else (pad.dpad & ~bit)
                    elif code in PAD_KEYS:
                        (pad.keys.add if val else pad.keys.discard)(code)
                    elif code in BTN_FIRE:
                        (pad.fire.add if val else pad.fire.discard)(code)
            elif typ == EV_ABS and pad:
                if code in (ABS_HAT0X, ABS_HAT0Y):
                    pad.hat[code - ABS_HAT0X] = (val > 0) - (val < 0)
                elif code in pad.ranges:
                    mn, mx = pad.ranges[code]
                    mid, dead = (mn + mx) / 2.0, (mx - mn) * 0.3       # 30 % from centre
                    pad.stick[code] = (val > mid + dead) - (val < mid - dead)
        return True

    # ---- state for m65d
    def matrix(self):
        cols = [0] * 9
        names = []
        for code in self.kbd_keys:
            names += KEYMAP.get(code, [])
        for p in self.pads:
            for code in p.keys:
                names += PAD_KEYS[code]
        for n in names:
            c, r = self.keypos[n]
            cols[c] |= 1 << r
        return cols

    def joysticks(self):
        """(port1 bits, port2 bits): first pad -> port 2, second -> port 1"""
        j = [p.joy() for p in self.pads] + [0, 0]
        return j[1], j[0]

    def ctrl_bits(self):
        return (2 if self.restore else 0) | (4 if self.caps else 0)
