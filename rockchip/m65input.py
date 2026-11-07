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
    CapsLock = CAPS LOCK (toggles)     cursor keys (up/left are the MEGA65's dedicated keys, INPUT ctrl b4/b5)
    F1/F3/F5/F7/F9/F11 direct, F2/F4/F6/F8/F10/F12 = SHIFT + the odd key
    ` = left-arrow  - = +  = = -  [ = @  ] = *  ; = :  ' = ;  \\ = =   keypad digits/operators
Gamepad: first pad -> joystick port 2 (most C64 games), second pad -> port 1; a pad keeps its
    port while connected (unplugging pad 1 does not move pad 2).
    D-pad / hat / left stick = directions; A/B/X/Y, shoulders, triggers = fire;
    Start = SPACE, Select = RETURN, Mode/Home = RUN/STOP.
System keys (never passed to the core), Ctrl + Alt + ...:
    Del = reset the core     F1 = MEGA65     F2 = C64     F12 = toggle MEGA65 <-> C64
    J = swap joystick ports 1 <-> 2 (pads and mouse)
    T = Linux terminal on the MEGA65 screen and back (m65term; keys then go to the shell, US layout)
    End = shut the Rockchip down safely; the MEGA65 screen says when it is safe to switch off
Mouse: Commodore 1351 in proportional mode on port 2 (all mice summed): the SID POTX/POTY
    values carry the position mod 64 (bits 6..1), left button = fire, right button = up.
    MOUSE_SCALE divides PC mouse counts (modern mice report far more counts than a 1351).
    The POT values change at most MOUSE_RATE counts/s (burst MOUSE_BURST): a 1351 driver reads
    the position once per frame and takes a change of more than 31 counts as the other direction.
Devices are classified by capability, one node can be several (keyboard + touchpad combos);
events are dispatched by type.  Hot-plug: inotify on /dev/input (+ a 2 s rescan as fallback).
After an evdev buffer overrun (SYN_DROPPED) and at plug-in the state is re-read from the kernel.
"""
import ctypes
import errno
import fcntl
import glob
import os
import struct
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
MOUSE_SCALE = 2.0                         # PC counts per 1351 count
MOUSE_RATE = 1100.0                       # 1351 counts/s per axis the POTs may move, and ...
MOUSE_BURST = 8.0                         # ... burst: <= 8 + 1100 * 20 ms = 30 counts per PAL frame
MOUSE_BACKLOG = 40.0                      # movement queued beyond the rate; more is dropped
SYN_REPORT, SYN_DROPPED = 0, 3
EVENT_FMT = "llHHi"                      # struct input_event (timeval + type/code/value)
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EVIOCGRAB = 0x40044590
EVIOCSCLOCKID = 0x400445a0              # event timestamps in CLOCK_MONOTONIC (= time.monotonic())
CLOCK_MONOTONIC = 1
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11


def EVIOCGABS(axis):
    return 0x80184540 + axis              # _IOR('E', 0x40 + axis, struct input_absinfo)


EVIOCGKEY_96 = 0x80604518                 # _IOR('E', 0x18, 96 bytes): keys currently down (KEY_MAX 0x2ff)
IN_ATTRIB, IN_CREATE, IN_DELETE = 0x4, 0x100, 0x200
_libc = ctypes.CDLL(None, use_errno=True)


def inotify_dev_input():
    """non-blocking inotify fd watching /dev/input, or None"""
    try:
        fd = _libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            return None
        if _libc.inotify_add_watch(fd, b"/dev/input", IN_CREATE | IN_ATTRIB | IN_DELETE) < 0:
            os.close(fd)
            return None
        return fd
    except (AttributeError, OSError):
        return None


def keys_down(fd):
    """set of key/button codes the kernel reports as currently down"""
    try:
        return _bitmap_bytes(fcntl.ioctl(fd, EVIOCGKEY_96, b"\0" * 96))
    except OSError:
        return set()


def abs_value(fd, axis):
    try:
        return struct.unpack("6i", fcntl.ioctl(fd, EVIOCGABS(axis), b"\0" * 24))[0]
    except OSError:
        return None


def _bitmap_bytes(buf):
    return {i * 8 + b for i, v in enumerate(buf) for b in range(8) if v >> b & 1}


def poll_ms(event_path):
    """effective USB polling interval (ms) of an evdev node's device, or None.  xHCI schedules a
    low/full-speed interrupt endpoint at the descriptor's interval rounded down to a power of 2
    (usbhid's kbpoll/mousepoll/jspoll are ignored by xHCI)"""
    try:
        d = os.path.realpath("/sys/class/input/%s/device" % os.path.basename(event_path))
        for _ in range(4):                # input -> (HID device ->) USB interface with ep_8x
            if glob.glob(os.path.join(d, "ep_8*")):
                break
            d = os.path.dirname(d)
        else:
            return None
        speed = open(os.path.join(d, "..", "speed")).read().strip()
        for e in sorted(glob.glob(os.path.join(d, "ep_8*"))):
            if open(os.path.join(e, "type")).read().strip() != "Interrupt":
                continue
            t = open(os.path.join(e, "interval")).read().strip()
            ms = float(t[:-2]) if t.endswith("ms") else float(t[:-2]) / 1000.0
            if speed in ("1.5", "12") and ms >= 1:
                p = 1
                while p * 2 <= ms:
                    p *= 2
                ms = p
            return ms
    except (OSError, ValueError):
        pass
    return None


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
    108: ["DOWN"], 106: ["RIGHT"],
    # cursor up / left: the dedicated keys (ctrl_bits), on the MEGA65 core also RIGHT SHIFT +
    # DOWN / RIGHT in the matrix -- its hardware ASCII decoder uses the former, games scanning the
    # CIA matrix the latter.  The C64 core makes RIGHT SHIFT + cursor from the dedicated key itself;
    # there the matrix copy is left out (InputHub.matrix_cursor), else its on-screen menu, which
    # reads the dedicated key as up and the plain CRSR key as down, saw up and then down.
    103: ["RSHIFT", "DOWN"], 105: ["RSHIFT", "RIGHT"],
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
MOD_NAMES = {"LSHIFT", "RSHIFT", "CTRL", "MEGA", "ALT"}
CURSOR_UP_LEFT = (103, 105)              # also sent as dedicated keys (ctrl_bits)
# A PC key mapped to modifier + key (cursor up = SHIFT + CRSR DOWN, Insert, F2, ...) shows only
# the modifier for this long first: both in one update can reach the core's scanner as
# "key without SHIFT" for one scan, and the KERNAL decodes a key only when it changes (so
# cursor up came out as cursor down).
MOD_LEAD = 0.04
# ... and the whole combination then stays in the matrix at least this long, even for a quick
# tap (the KERNAL scans every 16-20 ms; a 60 ms tap left ~20 ms of SHIFT+key)
MOD_HOLD = 0.10
CTRL_KEYS, ALT_KEYS = {29, 97}, {56, 100}
SYSTEM_KEYS = {111: "reset", 59: "core mega65", 60: "core c64", 88: "core toggle",  # Del F1 F2 F12
               36: "joyswap", 20: "terminal", 107: "poweroff",                      # J (local), T, End
               110: "view toggle"}                                                  # Insert
# (The view hotkey is not an F key: with the keyboard released to Linux, Ctrl+Alt+Fn also
#  switches the Linux console's virtual terminals.)
# With the keyboard released to Linux only these stay MEGA65 hotkeys (Linux sees every key too:
# m65d voids Ctrl+Alt+Insert in the console keymap; Ctrl+Alt+End means nothing there)
LINUX_FOCUS_KEYS = {110, 107}                                                       # Insert, End

# Terminal mode (m65term): US layout -> bytes for the shell; special keys by name
TERM_KEYS = {1: "\x1b", 14: "\x7f", 15: "\t", 28: "\r", 96: "\r", 57: " ",
             12: "-", 13: "=", 26: "[", 27: "]", 39: ";", 40: "'", 41: "`", 43: "\\", 51: ",",
             52: ".", 53: "/", 55: "*", 74: "-", 78: "+", 98: "/", 83: ".", 82: "0",
             79: "1", 80: "2", 81: "3", 75: "4", 76: "5", 77: "6", 71: "7", 72: "8", 73: "9"}
TERM_SHIFT = {12: "_", 13: "+", 26: "{", 27: "}", 39: ":", 40: '"', 41: "~", 43: "|", 51: "<",
              52: ">", 53: "?"}
for _i, (_c, _s) in enumerate(zip("1234567890", "!@#$%^&*()")):
    TERM_KEYS[2 + _i], TERM_SHIFT[2 + _i] = _c, _s
for _codes, _chars in (([16, 17, 18, 19, 20, 21, 22, 23, 24, 25], "qwertyuiop"),
                       ([30, 31, 32, 33, 34, 35, 36, 37, 38], "asdfghjkl"),
                       ([44, 45, 46, 47, 48, 49, 50], "zxcvbnm")):
    for _c, _ch in zip(_codes, _chars):
        TERM_KEYS[_c], TERM_SHIFT[_c] = _ch, _ch.upper()
TERM_SPECIAL = {103: "UP", 108: "DOWN", 105: "LEFT", 106: "RIGHT", 102: "HOME", 107: "END",
                104: "PGUP", 109: "PGDN", 110: "INS", 111: "DEL", 87: "F11", 88: "F12"}
TERM_SPECIAL.update({59 + i: "F%d" % (i + 1) for i in range(10)})
SHIFT_KEYS = {42, 54}

# Gamepad buttons
BTN_FIRE = set(range(0x120, 0x12c)) | {0x130, 0x131, 0x132, 0x133, 0x134, 0x135,
                                       0x136, 0x137, 0x138, 0x139}  # joystick + A B C X Y Z TL TR TL2 TR2
BTN_SELECT, BTN_START, BTN_MODE = 0x13a, 0x13b, 0x13c
BTN_DPAD = {0x220: 0, 0x221: 1, 0x222: 2, 0x223: 3}                  # up down left right -> joy bit
PAD_KEYS = {BTN_START: ["SPACE"], BTN_SELECT: ["RETURN"], BTN_MODE: ["RUNSTOP"]}
# Generic USB pads that call themselves a "joystick" (e.g. DragonRise 0079:0006 "PC TWIN SHOCK")
# report buttons 1..12 as BTN_TRIGGER..BTN_BASE6; their usual layout (SDL gamecontrollerdb) has
# button 9 = Select, 10 = Start.  Applied only to such twin-stick pads (12 buttons, Z/RZ axes),
# so the base buttons of a real joystick stay fire buttons.
JOY_SELECT, JOY_START = 0x128, 0x129
ABS_Z, ABS_RZ = 0x02, 0x05


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
    def __init__(self, fd, port, keys=(), absb=()):
        self.fd = fd
        self.port = port                  # 1 or 2, kept while connected
        self.remap = {}                   # button code -> PAD_KEYS code
        if set(range(0x120, 0x12c)) <= set(keys) and not {BTN_SELECT, BTN_START} & set(keys) \
                and {ABS_Z, ABS_RZ} <= set(absb):
            self.remap = {JOY_SELECT: BTN_SELECT, JOY_START: BTN_START}
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

    def key(self, code, down):
        code = self.remap.get(code, code)
        if code in BTN_DPAD:
            bit = 1 << BTN_DPAD[code]
            self.dpad = (self.dpad | bit) if down else (self.dpad & ~bit)
        elif code in PAD_KEYS:
            (self.keys.add if down else self.keys.discard)(code)
        elif code in BTN_FIRE:
            (self.fire.add if down else self.fire.discard)(code)

    def axis(self, code, val):
        if code in (ABS_HAT0X, ABS_HAT0Y):
            self.hat[code - ABS_HAT0X] = (val > 0) - (val < 0)
        elif code in self.ranges:
            mn, mx = self.ranges[code]
            mid, dead = (mn + mx) / 2.0, (mx - mn) * 0.3       # 30 % from centre
            self.stick[code] = (val > mid + dead) - (val < mid - dead)

    def resync(self):
        down = keys_down(self.fd)
        self.dpad, self.fire, self.keys = 0, set(), set()
        for c in down:
            self.key(c, True)
        for ax in (ABS_HAT0X, ABS_HAT0Y, ABS_X, ABS_Y):
            v = abs_value(self.fd, ax)
            if v is not None:
                self.axis(ax, v)

    def joy(self):
        up = self.hat[1] < 0 or self.stick[1] < 0 or self.dpad & 1
        down = self.hat[1] > 0 or self.stick[1] > 0 or self.dpad & 2
        left = self.hat[0] < 0 or self.stick[0] < 0 or self.dpad & 4
        right = self.hat[0] > 0 or self.stick[0] > 0 or self.dpad & 8
        return (bool(up) | bool(down) << 1 | bool(left) << 2 | bool(right) << 3
                | bool(self.fire) << 4)


class Dev:
    def __init__(self, fd, path, roles, name, poll):
        self.fd, self.path, self.roles, self.name, self.poll = fd, path, roles, name, poll
        self.keys = set()                 # keyboard keys down on this device
        self.buttons = set()              # mouse buttons down on this device
        self.pad = None
        self.skip = False                 # after SYN_DROPPED: ignore events until SYN_REPORT


class Axis:
    """1351 position on one axis, rate-limited (token bucket) so that a driver reading once per
    frame never sees more than ~30 counts of change"""
    def __init__(self):
        self.target = 0.0                 # where the mouse is
        self.out = 0.0                    # what the POTs show
        self.tokens = MOUSE_BURST

    def add(self, d, cap=True):
        self.target += d
        if cap:                           # too fast for a 1351: drop the excess
            self.target = max(self.out - MOUSE_BACKLOG, min(self.out + MOUSE_BACKLOG, self.target))

    def tick(self, dt):
        self.tokens = min(MOUSE_BURST, self.tokens + MOUSE_RATE * dt)
        d = self.target - self.out
        step = max(-self.tokens, min(self.tokens, d))
        self.out += step
        self.tokens -= abs(step)
        return abs(self.target - self.out) > 0.5        # backlog left


class InputHub:
    def __init__(self, keypos, log=print):
        self.keypos = keypos               # MEGA65 key name -> (column, row)
        self.log = log
        self.devs = {}                      # fd -> Dev
        self.paths = {}                     # path -> fd
        self.kbd_keys = set()              # pressed linux keycodes (all keyboards)
        self.ax, self.ay = Axis(), Axis()   # 1351 position (all mice summed)
        self.mouse_t = time.monotonic()
        self.mouse_backlog = False
        self.script_buttons = 0             # scripted mouse buttons (m65d 'mouse' command)
        self.caps = False
        self.restore = False
        self.joy_swap = False
        self.matrix_cursor = True           # cursor up/left also as RSHIFT + cursor in the matrix
        self.term_mode = False              # keys go to the Linux terminal (m65term), not the MEGA65
        self.term_caps = False
        self.term_out = []                  # messages for m65term ("k NAME mods" / "t hex")
        self.actions = []                   # system-key actions for m65d (reset / core switch)
        self.linux_focus = False            # keyboards released to Linux (view linux)
        self.lead = {}                      # keycode -> press time (modifier-first combos)
        self.keylog = []                    # last key events (m65ctl keylog)
        self.held = {}                      # keycode -> release time (released early, held)
        self.sys_held = set()               # system keys swallowed while held
        self.pending = None                 # (timestamp, kind) of the oldest event not yet sent
        self.watch = inotify_dev_input()
        self.next_scan = 0.0                # periodic fallback rescan
        self.rescan_at = []                 # quick rescans after an inotify event
        self.dropped = 0                    # SYN_DROPPED seen
        self.proc_text = None               # last /proc/bus/input/devices parsed
        self.proc_paths = set()

    # ---- discovery
    @property
    def pads(self):
        return [d.pad for d in self.devs.values() if d.pad]

    @property
    def mice(self):
        return [d for d in self.devs.values() if "mouse" in d.roles]

    def scan(self):
        try:
            text = open("/proc/bus/input/devices").read()
        except OSError:
            return
        if text == self.proc_text and set(self.paths) >= self.proc_paths:
            return                        # nothing new (parsing costs ~2 ms, reading 0.2 ms)
        self.proc_text = text
        info = {}
        blocks = text.split("\n\n")
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
            roles = set()
            if {REL_X, REL_Y} <= rel and BTN_LEFT in keys:
                roles.add("mouse")
            if keys & {0x130, 0x120} or (absb & {ABS_X, ABS_HAT0X} and keys & BTN_FIRE):
                roles.add("pad")
            if EV_KEY in ev and {30, 57, 28} <= keys:     # has A, SPACE, ENTER
                roles.add("kbd")
            if roles:
                info[handler] = (roles, name, keys, absb)
        self.proc_paths = set(info)       # retried until all are open (node may lag the list)
        for path, (roles, name, keys, absb) in info.items():
            if path in self.paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                if not self.linux_focus:
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError:
                pass
            try:
                fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            except OSError:
                pass
            try:
                self._add(fd, path, roles, name, keys, absb)
            except Exception as e:        # one odd device must not stop the others
                self.paths.pop(path, None)
                self.devs.pop(fd, None)
                os.close(fd)
                self.log("m65input: ! %s (%s) not used: %r" % (path, name, e))

    def _add(self, fd, path, roles, name, keys, absb):
        dev = Dev(fd, path, roles, name, poll_ms(path))
        if "pad" in roles:
            used = {p.port for p in self.pads}
            dev.pad = Pad(fd, 2 if 2 not in used else 1 if 1 not in used else 2, keys, absb)
        self.devs[fd] = dev
        self.paths[path] = fd
        self._resync(dev)
        self.log("m65input: + %s %s (%s)%s%s%s" % (
            "+".join(sorted(roles)), path, name,
            " poll %gms" % dev.poll if dev.poll else "",
            " -> port %d" % dev.pad.port if dev.pad else "",
            " (buttons 9/10 = Select/Start)" if dev.pad and dev.pad.remap else ""))

    def _drop(self, fd):
        dev = self.devs.pop(fd)
        self.paths.pop(dev.path, None)
        os.close(fd)
        self._merge_keys()
        self.log("m65input: - %s %s (%s)" % ("+".join(sorted(dev.roles)), dev.path, dev.name))

    def _merge_keys(self):
        self.kbd_keys = set().union(*(d.keys for d in self.devs.values()))

    def _resync(self, dev):
        """read the device's current state from the kernel (plug-in, after SYN_DROPPED)"""
        down = keys_down(dev.fd)
        if "kbd" in dev.roles:
            dev.keys = {c for c in down if c < 0x100 and c not in (KEY_CAPSLOCK, KEY_PAGEUP)
                        and c not in SYSTEM_KEYS}
            self._merge_keys()
        if "mouse" in dev.roles:
            dev.buttons = {c for c in down if 0x110 <= c < 0x118}
        if dev.pad:
            dev.pad.resync()

    def fds(self):
        return list(self.devs) + ([self.watch] if self.watch is not None else [])

    def describe(self):
        for d in self.devs.values():
            if d.poll is None:            # sysfs may lag the event node at plug-in: look again
                d.poll = poll_ms(d.path)
        return " ".join("%s:%s%s%s" % ("+".join(sorted(d.roles)), d.name.replace(" ", "_"),
                                       "@%gms" % d.poll if d.poll else "",
                                       "@port%d" % d.pad.port if d.pad else "")
                        for d in self.devs.values()) or "none"

    def maintain(self, now):
        """hot-plug rescans + mouse rate limiting; returns (state changed, max seconds to sleep)"""
        changed = False
        if now >= self.next_scan or (self.rescan_at and now >= self.rescan_at[0]):
            n = len(self.devs)
            self.scan()
            changed = len(self.devs) != n
            self.next_scan = now + 2.0
            self.rescan_at = [t for t in self.rescan_at if t > now]
        before = (int(self.ax.out), int(self.ay.out))
        dt, self.mouse_t = now - self.mouse_t, now
        bx, by = self.ax.tick(dt), self.ay.tick(dt)
        self.mouse_backlog = bx or by
        if (int(self.ax.out), int(self.ay.out)) != before:
            changed = True
        wait = 2.0
        if self.mouse_backlog:
            wait = 0.002
        if self.rescan_at:
            wait = min(wait, max(0.0, self.rescan_at[0] - now))
        return changed, wait

    # ---- events
    def handle(self, fd):
        """read pending events from fd; True if the MEGA65-visible state may have changed"""
        if fd == self.watch:
            try:
                os.read(fd, 4096)
            except OSError:
                pass
            now = time.monotonic()            # node permissions / handler lines settle: retry
            self.rescan_at = sorted(set(self.rescan_at) | {now + 0.02, now + 0.25, now + 1.0})
            return False
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
        dev = self.devs[fd]
        pad = dev.pad
        is_kbd, is_mouse = "kbd" in dev.roles, "mouse" in dev.roles
        kchanged = False
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            sec, usec, typ, code, val = struct.unpack_from(EVENT_FMT, data, i)
            if typ == EV_SYN:
                if code == SYN_DROPPED:
                    dev.skip = True
                    self.dropped += 1
                elif code == SYN_REPORT and dev.skip:
                    dev.skip = False
                    self._resync(dev)
                    kchanged = True
                continue
            if dev.skip:
                continue
            if self.pending is None:
                self.pending = (sec + usec * 1e-6, "mouse" if typ == EV_REL else
                                "pad" if (pad and (typ == EV_ABS or code >= 0x120)) else
                                "kbd" if code < 0x100 else "mouse")
            if is_mouse and self.linux_focus and (typ == EV_REL or 0x110 <= code < 0x118):
                pass                                         # the mouse belongs to Linux now
            elif typ == EV_REL and is_mouse:
                if code == REL_X:
                    self.ax.add(val / MOUSE_SCALE)
                elif code == REL_Y:
                    self.ay.add(-val / MOUSE_SCALE)          # 1351: moving up counts up
            elif typ == EV_ABS and pad:
                pad.axis(code, val)
            elif typ == EV_KEY:
                if is_mouse and 0x110 <= code < 0x118:
                    (dev.buttons.add if val else dev.buttons.discard)(code)
                elif pad and code >= 0x120:
                    pad.key(code, val != 0)
                elif is_kbd and code < 0x100:
                    self._kbd_key(dev, code, val)
                    kchanged = True
        if kchanged:
            self._merge_keys()
        return True

    def _sys_combo(self, code, val):
        """a system key with Ctrl+Alt held (press), or the release of one that was taken"""
        return code in SYSTEM_KEYS and (val == 0 and code in self.sys_held or
                                        val and self.kbd_keys & CTRL_KEYS and self.kbd_keys & ALT_KEYS)

    def _kbd_key(self, dev, code, val):
        self.keylog = (self.keylog + ["%d:%d" % (code, val)])[-20:]
        if self.linux_focus and not (code in LINUX_FOCUS_KEYS and self._sys_combo(code, val)):
            # Linux has the keyboard (released): nothing reaches the MEGA65 (no RESTORE from
            # Page Up, no CAPS LOCK); only the modifiers are watched for the hotkeys
            if code in CTRL_KEYS or code in ALT_KEYS:
                (dev.keys.add if val else dev.keys.discard)(code)
        elif code == KEY_CAPSLOCK:
            if val == 1:
                self.caps = not self.caps       # latching key, like the real one
        elif code == KEY_PAGEUP:
            self.restore = val != 0
        elif self.term_mode and not (code in SYSTEM_KEYS and (val == 0 and code in self.sys_held or
                                                               val and self.kbd_keys & CTRL_KEYS
                                                               and self.kbd_keys & ALT_KEYS)):
            self._term_key(dev, code, val)
        elif code in SYSTEM_KEYS and (val == 0 and code in self.sys_held or
                                      val and self.kbd_keys & CTRL_KEYS
                                      and self.kbd_keys & ALT_KEYS):
            if val == 1:
                if SYSTEM_KEYS[code] == "joyswap":
                    self.swap_ports()
                else:
                    self.actions.append(SYSTEM_KEYS[code])
                self.sys_held.add(code)
            elif val == 0:
                self.sys_held.discard(code)
        elif val:                               # 1 = press, 2 = autorepeat
            if val == 1 and code not in self.kbd_keys:
                names = self._names(code)
                if any(n in MOD_NAMES for n in names) and \
                   any(n not in MOD_NAMES for n in names):
                    self.lead[code] = time.time()
                    self.held.pop(code, None)
            dev.keys.add(code)
        else:
            if code in self.lead:
                # quick tap of a combination: keep it until lead + hold time
                self.held[code] = self.lead[code] + MOD_LEAD + MOD_HOLD
            dev.keys.discard(code)
            self.lead.pop(code, None)

    def _term_key(self, dev, code, val):
        (dev.keys.add if val else dev.keys.discard)(code)     # modifier state
        if not val:
            return
        if code == KEY_CAPSLOCK:
            if val == 1:
                self.term_caps = not self.term_caps
            return
        keys = self.kbd_keys | dev.keys
        shift = bool(keys & SHIFT_KEYS)
        ctrl, alt = bool(keys & CTRL_KEYS), bool(keys & ALT_KEYS)
        if code in TERM_SPECIAL:
            self.term_out.append("k %s %d" % (TERM_SPECIAL[code], shift | alt << 1 | ctrl << 2))
            return
        ch = TERM_SHIFT.get(code) if shift else TERM_KEYS.get(code)
        if ch is None:
            return
        if self.term_caps and ch.isalpha():
            ch = ch.swapcase()
        if ctrl:
            if ch.isalpha():
                ch = chr(ord(ch.lower()) - 96)
            else:
                ch = {" ": "\x00", "2": "\x00", "[": "\x1b", "\\": "\x1c", "]": "\x1d",
                      "6": "\x1e", "-": "\x1f", "/": "\x1f"}.get(ch, ch)
        data = ch.encode()
        if alt:
            data = b"\x1b" + data
        self.term_out.append("t " + data.hex())

    def set_linux_focus(self, on):
        """keyboards to Linux (released, the MEGA65 sees no keys) or back to the MEGA65 (grabbed)"""
        self.linux_focus = on
        if on:
            self.restore = False
        for fd, dev in self.devs.items():
            if "kbd" in dev.roles or "mouse" in dev.roles:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 0 if on else 1)
                except OSError:
                    pass
            dev.keys.clear()                # nothing stays pressed on either side
            if hasattr(dev, "buttons"):
                dev.buttons.clear()
        self.lead.clear()
        self.held.clear()
        self._merge_keys()
        self.log("m65input: keyboard focus %s" % ("Linux" if on else "MEGA65"))

    def set_term_mode(self, on):
        self.term_mode = on
        self.lead.clear()
        self.held.clear()

    def swap_ports(self):
        self.joy_swap = not self.joy_swap
        self.log("m65input: joystick ports %s" % ("swapped" if self.joy_swap else "normal"))

    # ---- state for m65d
    def take_pending(self):
        """(timestamp, kind) of the oldest event since the last call, or None (latency stats)"""
        p, self.pending = self.pending, None
        return p

    def lead_pending(self):
        """True while a modifier-first combo still holds back its key (m65d re-pushes)"""
        now = time.time()
        expired = False
        for code, t in list(self.lead.items()):
            if now - t >= MOD_LEAD:
                del self.lead[code]
                if code in self.kbd_keys:     # still held: from now on it is a normal key
                    self.held.pop(code, None)
                expired = True               # push once more: now with the key part
        for code, t in list(self.held.items()):
            if now >= t:
                del self.held[code]
                expired = True               # push once more: now released
        return bool(self.lead) or bool(self.held) or expired

    def matrix(self):
        cols = [0] * 9
        if self.term_mode:
            return cols
        names = []
        now = time.time()
        # with the keyboard released to Linux the MEGA65 sees no keyboard keys at all
        # (the modifiers are still tracked, only for the view hotkey)
        for code in (set() if self.linux_focus else set(self.kbd_keys) | set(self.held)):
            k = self._names(code)
            if code in self.lead and now - self.lead[code] < MOD_LEAD:
                k = [n for n in k if n in MOD_NAMES]        # modifier first
            names += k
        for p in self.pads:
            for code in p.keys:
                names += PAD_KEYS[code]
        for n in names:
            if n in self.keypos:
                c, r = self.keypos[n]
                cols[c] |= 1 << r
        return cols

    def _names(self, code):
        if code in CURSOR_UP_LEFT and not self.matrix_cursor:
            return []                     # dedicated key only (C64 core)
        return KEYMAP.get(code, [])

    def _mouse_port(self):
        return 1 if self.joy_swap else 2

    def joysticks(self):
        """(port1 bits, port2 bits): first pad -> port 2, second -> port 1; mouse on port 2
        (all swapped with Ctrl+Alt+J)"""
        j = {1: 0, 2: 0}
        if self.term_mode:
            return 0, 0
        for p in self.pads:
            j[(3 - p.port) if self.joy_swap else p.port] |= p.joy()
        btn = set().union(*(m.buttons for m in self.mice)) if self.mice else set()
        mb = (0x10 if BTN_LEFT in btn or self.script_buttons & 1 else 0) | \
             (0x01 if BTN_RIGHT in btn or self.script_buttons & 2 else 0)
        j[self._mouse_port()] |= mb
        return j[1], j[2]

    def move_mouse(self, dx, dy):
        """scripted movement in 1351 counts (dy > 0 = up); rate-limited, never dropped"""
        self.ax.add(dx, cap=False)
        self.ay.add(dy, cap=False)

    def mouse_active(self):
        return bool(self.mice) or self.ax.target != 0 or self.ay.target != 0 or self.script_buttons != 0

    def pots(self):
        """SID values (pot1 x, pot1 y, pot2 x, pot2 y); $FF = nothing connected.
        1351 proportional mode: bits 6..1 = position mod 64, bit 0 = noise (0 here)."""
        if not self.mouse_active() or self.term_mode:
            return (0xFF, 0xFF, 0xFF, 0xFF)
        m = ((int(self.ax.out) & 63) << 1, (int(self.ay.out) & 63) << 1)
        return (m + (0xFF, 0xFF)) if self._mouse_port() == 1 else ((0xFF, 0xFF) + m)

    def ctrl_bits(self):
        # b1 RESTORE, b2 CAPS LOCK, b4 / b5 cursor up / left: the MEGA65 keyboard's dedicated
        # keys (MEGA65 core keyup/keyleft, C64 core keyboard keys 73/74); the matrix gets
        # RIGHT SHIFT + DOWN / RIGHT as well (KEYMAP), as from a real MEGA65 keyboard
        if self.term_mode:
            return 4 if self.caps else 0
        return ((2 if self.restore else 0) | (4 if self.caps else 0) |
                (0x10 if 103 in self.kbd_keys else 0) | (0x20 if 105 in self.kbd_keys else 0))
