"""lxcore/runner.py -- the shell's driver inside m65d: one foreground task, keys, display windows.

A core adapter subclasses Runner and provides: self.port (display port, lxcore.display), the menu
session and start sequence as generators, background(now), next_wakeup(), and open_key (the
menu-open key: (key number a, key number b, combination)).  m65d calls:
    poll(now)                   often; returns the seconds until it wants the next call
    keys(cols, ctrl, now)       the MEGA65 matrix state m65input builds (also when unchanged)
    filter_input(cols, ctrl)    what the core may see of the keyboard (nothing while the menu is
                                open; never the menu-open key)
Foreground work runs as one generator; it yields between bus chunks (None), for a key (KEY) or to
sleep (('sleep', s)); after a plain None the driver returns to m65d's loop, so a key event never
waits for more than one chunk.
"""

import time

from lxcore.display import Screen
from lxcore.keys import KEY, KEY_HELP, KeyScanner, m65_keys


class Runner:
    def __init__(self, bus, port, log=print, mono=time.monotonic, screen=None):
        self.bus = bus
        self.port = port
        self.log = log
        self.mono = mono
        self.scr = screen or Screen()
        self.scan = KeyScanner()
        self.task = None                # the foreground generator
        self.task_kind = None
        self.want = None                # what it waits for: KEY, a wake-up time, or None
        self.keyq = []
        self.win_req = None
        self.menu_open = False
        self.kbd_to_core = False        # keyboard forwarded (menu closed and connected)
        self.open_key = (67, 67, False)  # MEGA65 Help
        self.started = False

    # ---- to be provided by the core adapter
    def menu_session(self):
        raise NotImplementedError

    def background(self, now):
        pass

    def next_wakeup(self):
        return 1e18

    # ---- m65d interface
    def filter_input(self, cols, ctrl):
        if not self.started:
            return cols, ctrl
        if not self.kbd_to_core:
            return [0] * len(cols), ctrl & ~0x30
        a, b, combo = self.open_key
        cols = list(cols)
        if combo:
            if cols[a // 8] >> (a % 8) & 1:       # the first key held: the second is the combo's
                cols[b // 8] &= ~(1 << (b % 8))
        elif a not in (73, 74):
            cols[a // 8] &= ~(1 << (a % 8))
        return cols, ctrl

    def keys(self, cols, ctrl, now):
        if not self.started:
            return
        a, b, combo = self.open_key
        self.scan.scan(m65_keys(cols, ctrl, a, b, combo), now)
        while True:
            k = self.scan.getkey()
            if not k:
                break
            if self.task is not None and self.want == KEY:
                self.keyq.append(k)
            elif self.task is None and k == KEY_HELP:
                self.run_task(self.menu_session(), 'menu')
            # other keys with the menu closed are consumed, like the firmware's main loop

    def poll(self, now):
        if not self.started:
            return 0.5
        self.step_task(now)
        if self.task is None or self.want == KEY:
            self.background(now)
            self.bus.flush()
        if self.task is not None and self.want is None:
            return 0.0
        nxt = self.next_wakeup()
        if self.task is not None and isinstance(self.want, float):
            nxt = min(nxt, self.want)
        return max(0.0, min(0.05, nxt - now))

    # ---- task driver
    def run_task(self, gen, kind):
        self.task, self.task_kind, self.want = gen, kind, None
        self.keyq.clear()
        self.step_task(self.mono())

    def step_task(self, now):
        for _ in range(64):
            if self.task is None:
                break
            if self.want == KEY:
                if not self.keyq:
                    break
                send = self.keyq.pop(0)
            elif isinstance(self.want, float):
                if now < self.want:
                    break
                send = None
            else:
                send = None
            try:
                req = self.task.send(send)
            except StopIteration:
                req = 'done'
            self.scr.flush(self.port)
            self.apply_window()
            self.bus.flush()                # queued writes leave now, not with the next read
            if req == 'done':
                self.task = self.task_kind = self.want = None
                break
            now = self.mono()
            if isinstance(req, tuple) and req[0] == 'sleep':
                self.want = now + req[1]
            else:
                self.want = req
            if req is None:
                break

    # ---- display windows: a change is a request, carried out after the next flush, so a window
    # never shows the content of the previous one; hiding is immediate
    def show_full(self):
        self.win_req = (0, 0, self.scr.dx, self.scr.dy)

    def show_window(self, x, y, dx, dy):
        self.win_req = (x, y, dx, dy)

    def hide(self):
        self.win_req = 'off'
        self.apply_window()

    def apply_window(self):
        req, self.win_req = self.win_req, None
        if req is None:
            return
        if req == 'off':
            self.port.window(None)
            return
        self.scr.flush(self.port)
        self.port.window(req)
