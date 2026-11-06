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
System keys (never passed to the core), Ctrl + Alt + ...:
    Del = reset the core     F1 = MEGA65     F2 = C64     F12 = toggle MEGA65 <-> C64
Mouse: Commodore 1351 in proportional mode on port 2 (all mice summed): the SID POTX/POTY
    values carry the position mod 64 (bits 6..1), left button = fire, right button = up.
    MOUSE_SCALE divides PC mouse counts (modern mice report far more counts than a 1351).
"""
import errno
import fcntl
import glob
import os
import struct

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
MOUSE_SCALE = 2.0                         # PC counts per 1351 count
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
CTRL_KEYS, ALT_KEYS = {29, 97}, {56, 100}
SYSTEM_KEYS = {111: "reset", 59: "core mega65", 60: "core c64", 88: "core toggle"}  # Del F1 F2 F12

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


class Mouse:
    def __init__(self, fd):
        self.fd = fd
        self.buttons = set()


class InputHub:
    def __init__(self, keypos, log=print):
        self.keypos = keypos               # MEGA65 key name -> (column, row)
        self.log = log
        self.devs = {}                      # fd -> (path, kind, name)
        self.paths = {}                     # path -> fd
        self.kbd_keys = set()              # pressed linux keycodes (all keyboards)
        self.pads = []                      # Pad objects in connection order
        self.mice = []                      # Mouse objects
        self.mx = self.my = 0.0             # 1351 position (all mice summed)
        self.script_buttons = 0             # scripted mouse buttons (m65d 'mouse' command)
        self.caps = False
        self.restore = False
        self.actions = []                   # system-key actions for m65d (reset / core switch)
        self.sys_held = set()               # system keys swallowed while held

    # ---- discovery
    def scan(self):
        info = {}
        try:
            blocks = open("/proc/bus/input/devices").read().split("\n\n")
        except OSError:
            return
        for b in blocks:
            name, ev, keys, absb, rel, handler = "", set(), set(), set(), set(), None
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
                elif line.startswith("B: REL="):
                    rel = _bitmap(line[7:])
            if not handler:
                continue
            if {REL_X, REL_Y} <= rel and BTN_LEFT in keys:
                info[handler] = ("mouse", name)
            elif keys & {0x130, 0x120} or (absb & {ABS_X, ABS_HAT0X} and keys & BTN_FIRE):
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
            elif kind == "mouse":
                self.mice.append(Mouse(fd))
            self.log("m65input: + %s %s (%s)" % (kind, path, name))

    def _drop(self, fd):
        path, kind, name = self.devs.pop(fd)
        self.paths.pop(path, None)
        self.pads = [p for p in self.pads if p.fd != fd]
        self.mice = [m for m in self.mice if m.fd != fd]
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
        mouse = next((m for m in self.mice if m.fd == fd), None)
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, typ, code, val = struct.unpack_from(EVENT_FMT, data, i)
            if mouse:
                if typ == EV_REL and code == REL_X:
                    self.mx += val / MOUSE_SCALE
                elif typ == EV_REL and code == REL_Y:
                    self.my -= val / MOUSE_SCALE          # 1351: moving up counts up
                elif typ == EV_KEY:
                    (mouse.buttons.add if val else mouse.buttons.discard)(code)
                continue
            if typ == EV_KEY:
                if kind == "kbd":
                    if code == KEY_CAPSLOCK:
                        if val == 1:
                            self.caps = not self.caps       # latching key, like the real one
                    elif code == KEY_PAGEUP:
                        self.restore = val != 0
                    elif code in SYSTEM_KEYS and (val == 0 and code in self.sys_held or
                                                  val and self.kbd_keys & CTRL_KEYS
                                                  and self.kbd_keys & ALT_KEYS):
                        if val == 1:
                            self.actions.append(SYSTEM_KEYS[code])
                            self.sys_held.add(code)
                        elif val == 0:
                            self.sys_held.discard(code)
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
        """(port1 bits, port2 bits): first pad -> port 2, second -> port 1; mouse on port 2"""
        j = [p.joy() for p in self.pads] + [0, 0]
        btn = set().union(*(m.buttons for m in self.mice)) if self.mice else set()
        mb = (0x10 if BTN_LEFT in btn or self.script_buttons & 1 else 0) | \
             (0x01 if BTN_RIGHT in btn or self.script_buttons & 2 else 0)
        return j[1], j[0] | mb

    def move_mouse(self, dx, dy):
        """scripted movement in 1351 counts (dy > 0 = up)"""
        self.mx += dx
        self.my += dy

    def mouse_active(self):
        return bool(self.mice) or self.mx != 0 or self.my != 0 or self.script_buttons != 0

    def pots(self):
        """SID values (pot1 x, pot1 y, pot2 x, pot2 y); $FF = nothing connected.
        1351 proportional mode: bits 6..1 = position mod 64, bit 0 = noise (0 here)."""
        if not self.mouse_active():
            return (0xFF, 0xFF, 0xFF, 0xFF)
        return (0xFF, 0xFF, (int(self.mx) & 63) << 1, (int(self.my) & 63) << 1)

    def ctrl_bits(self):
        return (2 if self.restore else 0) | (4 if self.caps else 0)
