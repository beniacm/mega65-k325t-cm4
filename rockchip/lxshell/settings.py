"""settings.py -- the Amiga core's settings on the module (amiga.settings) and the import of the
94-byte M2M settings file (/amiga/aexp020.cfg) of the QNICE builds.

amiga.settings is a small JSON file:
    {"format": 1, "menu_id": "E6132EE0", "values": {"FILTER:Lanczos": 1, "FILTER:Smooth": 0, ...}}
Every setting is stored under its name (MenuDef: group name + ":" + item text), both on and off,
so a later menu with more lines (93 -> 94 for the IDE item) keeps every setting it still has:
an item the file does not name gets its factory default; in a radio group that the file does not
name at all, the default member is chosen.  Shell-owned items ("shell:rtc") live in the same map.

It is written only when a setting changed (menu closed), via a temporary file + rename, so a
power cut leaves the old or the new file, never half of one.  Nothing periodic.
"""

import json
import os

FORMAT = 1
LEGACY_SIZES = (94, 93)         # 93 = the builds before the IDE line (87) was added


def factory(mdef):
    """item index -> on/off: the OPTM_G_STDSEL defaults (shell items: off)"""
    return [bool(it.stdsel) for it in mdef.items]


def values_from_selection(mdef, sel):
    return {it.key: int(bool(sel[n])) for n, it in enumerate(mdef.items) if it.key}


def selection_from_values(mdef, values):
    """apply stored values over the factory defaults, radio groups kept consistent"""
    sel = factory(mdef)
    groups = {}
    for n, it in enumerate(mdef.items):
        if it.key and not it.single and not it.shell:
            groups.setdefault(it.word, []).append(n)
    for n, it in enumerate(mdef.items):
        if it.key and (it.single or it.shell) and it.key in values:
            sel[n] = bool(values[it.key])
    for word, members in groups.items():
        on = [n for n in members if values.get(mdef.items[n].key) == 1]
        if on:
            for n in members:
                sel[n] = n == on[0]
    return sel


def import_legacy(mdef, raw):
    """the 94-byte (or the older 93-byte) QNICE settings file -> values, or None (defaults).
    Like the firmware: wrong size or first byte 0xFF = defaults; a byte other than 0/1 = corrupt."""
    if len(raw) not in LEGACY_SIZES or raw[0] == 0xFF or any(b > 1 for b in raw):
        return None
    bits = list(raw)
    if len(raw) == 93:
        bits.insert(87, 0)                      # no IDE line yet: off
    sel = factory(mdef)
    for n, it in enumerate(mdef.items):
        if it.cfm_bit is not None and it.cfm_bit < len(bits):
            sel[n] = bool(bits[it.cfm_bit])
    return values_from_selection(mdef, sel)


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


def cfm_words(mdef, sel, base=None):
    """the 16 CFM words for a selection (core items only; shell items have no CFM bit)"""
    words = list(base) if base else [0] * 16
    for n, it in enumerate(mdef.items):
        b = it.cfm_bit
        if b is None:
            continue
        if sel[n]:
            words[b // 16] |= 1 << (b % 16)
        else:
            words[b // 16] &= ~(1 << (b % 16))
    return words


def selection_from_cfm(mdef, words, shell_values=None):
    sel = []
    for it in mdef.items:
        if it.cfm_bit is not None:
            sel.append(bool(words[it.cfm_bit // 16] >> (it.cfm_bit % 16) & 1))
        else:
            sel.append(bool((shell_values or {}).get(it.key, 0)) if it.key else False)
    return sel
