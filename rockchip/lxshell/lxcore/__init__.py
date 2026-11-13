"""lxcore -- the core-independent part of the Linux shell (DESIGN.md section 12).

Menu engine, file browser, help pages, settings store, menu-key handling, the host-bus link and a
display interface.  Rule: nothing here imports lxamiga (or any other core adapter) or knows a
core's register addresses; a core plugs in through the bus (its own register map), a display port
(where the cells go) and a menu description (hand-written, or converted by its adapter).
"""
