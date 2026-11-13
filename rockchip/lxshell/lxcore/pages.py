"""lxcore/pages.py -- full-screen text: help pages (M2M whs.asm) and messages."""

from lxcore.keys import KEY, KEY_SPACE, KEY_RUNSTOP, KEY_LEFT, KEY_RIGHT


def show_pages(screen, pages):
    """generator: Left / Right page, Space or Run/Stop ends"""
    p = 0
    while True:
        screen.frame_fullscr()
        screen.printstr(pages[p])
        while True:
            k = yield KEY
            if k in (KEY_SPACE, KEY_RUNSTOP):
                return
            if k == KEY_LEFT and p > 0:
                p -= 1
                break
            if k == KEY_RIGHT and p < len(pages) - 1:
                p += 1
                break


def message(screen, text):
    """a full-screen message (replaces the firmware's FATAL screen and the warnings)"""
    screen.frame_fullscr()
    screen.printstr(text)
