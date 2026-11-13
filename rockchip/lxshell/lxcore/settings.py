"""lxcore/settings.py -- a core's settings on the module, stored by item name.

    {"format": 1, "menu_id": "E6132EE0", "values": {"FILTER:Lanczos": 1, "FILTER:Smooth": 0, ...}}

Every setting (radio and toggle items with a key) is stored under its name, both on and off, so a
later menu with more items keeps every setting it still has: an item the file does not name gets
its default; in a radio group the file does not name at all, the default member is chosen.
Written only when a setting changed, via a temporary file + rename (a power cut leaves the old or
the new file, never half of one).  Nothing periodic.
"""

import json
import os

FORMAT = 1


def factory(mdef):
    """item index -> on/off: the defaults of the menu description"""
    return [bool(it.default) for it in mdef.items]


def values_from_selection(mdef, sel):
    return {it.key: int(bool(sel[n])) for n, it in enumerate(mdef.items) if it.key}


def selection_from_values(mdef, values):
    """apply stored values over the defaults, radio groups kept consistent"""
    sel = factory(mdef)
    groups = {}
    for n, it in enumerate(mdef.items):
        if it.key and not it.single:
            groups.setdefault(it.word, []).append(n)
    for n, it in enumerate(mdef.items):
        if it.key and it.single and it.key in values:
            sel[n] = bool(values[it.key])
    for word, members in groups.items():
        on = [n for n in members if values.get(mdef.items[n].key) == 1]
        if on:
            for n in members:
                sel[n] = n == on[0]
    return sel


def load(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get('format') != FORMAT or not isinstance(d.get('values'), dict):
        return None
    return {k: int(v) for k, v in d['values'].items() if v in (0, 1, True, False)}


def save(path, values, menu_id):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'format': FORMAT, 'menu_id': '%08X' % menu_id, 'values': values}, f,
                  sort_keys=True, indent=1)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
