#!/usr/bin/env python3
# edit_dts_sd.py — enable sdmmc2 (dwmmc@fe000000) in the CURRENT (bt1120-
# enabled) boot DTB, for the FPGA SD-passthrough on the sdmmc2m1 pads.
#
# Deliberate choices:
#  * NO pinctrl property on the node: the rgb/DPI driver owns the shared
#    GPIO3_A1..A6 pads (bt1120hd group) at boot; claiming them here would
#    -EBUSY the probe. The pad iomux is switched at runtime by sd_mode.sh
#    (video func 2 <-> sdmmc func 5) behind pinctrl's back — the DRM pipeline
#    never tears down, and card detection works by broken-cd polling.
#  * max-frequency 12.5 MHz: the FPGA repeater adds ~15 ns of turnaround
#    latency per direction; 12.5 MHz leaves wide margin (raise later).
#  * bus-width 4, broken-cd (no CD line routed to the controller), no UHS
#    caps listed -> the core never attempts 1.8 V switching (bus is 3V3-only).
#
#   usage: edit_dts_sd.py           (fdt_new.dts -> fdt_sd.dts)
import re, sys

src = open('fdt_new.dts').read()
edits = 0

def sub1(pat, rep, s, flags=0):
    global edits
    out, n = re.subn(pat, rep, s, flags=flags)
    assert n == 1, f"pattern matched {n} times (want 1): {pat[:80]!r}"
    edits += 1
    return out

# dwmmc@fe000000 (= sdmmc2): enable + SD-card properties.
# Anchor on the unique reg to avoid touching the other dwmmc nodes.
src = sub1(
    r'(dwmmc@fe000000 \{\n'
    r'(?:.*\n)*?'
    r')\t\tmax-frequency = <0x8f0d180>;\n',
    r'\g<1>\t\tmax-frequency = <0x7a1200>;\n'      # 8,000,000 Hz (write start-bit arm margin)
    r'\t\tbus-width = <0x04>;\n'
    r'\t\tbroken-cd;\n'
    # RK BSP mmc core only ATTACHES card types whitelisted via the vendor
    # supports-* properties (restrict_caps); without this the rescan loops
    # CMD0 -> power-off forever (found via ILA + dyndbg 2026-07-18)
    r'\t\tsupports-sd;\n',
    src)

src = sub1(
    r'(dwmmc@fe000000 \{\n'
    r'(?:.*\n)*?'
    r')\t\tstatus = "disabled";\n'
    r'(\t\tphandle = <0x16e>;)',
    r'\g<1>\t\tstatus = "okay";\n\g<2>',
    src)

open('fdt_sd.dts', 'w').write(src)
print(f"OK: {edits} edits -> fdt_sd.dts")
