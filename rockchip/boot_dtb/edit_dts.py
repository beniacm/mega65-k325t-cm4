#!/usr/bin/env python3
# edit_dts.py — merge the BT1120 enablement into the decompiled boot DTB.
# Path-anchored text edits; every substitution asserts exactly-one match.
import re, sys

src = open('fdt_orig.dts').read()
edits = 0

def sub1(pat, rep, s, flags=0):
    global edits
    out, n = re.subn(pat, rep, s, flags=flags)
    assert n == 1, f"pattern matched {n} times (want 1): {pat[:80]!r}"
    edits += 1
    return out

# 1. pinctrl: add the bt1120 pin group (mux 2, pull-none phandle 0xfa).
PINS = [(3,6),(3,1),(3,2),(3,3),(3,4),(3,5),(3,7),(3,8),(3,9),(3,10),
        (3,11),(3,12),(3,13),(3,14),(3,17),(3,18),(3,19)]
cells = ' '.join(f'0x{b:02x} 0x{p:02x} 0x02 0xfa' for b, p in PINS)
btgroup = (
    "\\g<1>\n"
    "\t\tbt1120 {\n\n"
    "\t\t\tbt1120-pins {\n"
    f"\t\t\t\trockchip,pins = <{cells}>;\n"
    "\t\t\t\tphandle = <0x2b0>;\n"
    "\t\t\t};\n"
    "\t\t};\n"
)
# insert AFTER pinctrl's properties (DTS: properties must precede subnodes);
# the node's own phandle property trails ranges; consume it too
# vendor DTB already ships /pinctrl/bt1120/bt1120-pins with the exact same cells (phandle 0x1b2) — reuse it

# 2. rgb node: enable + point at the bt1120 pins.
src = sub1(
    r'(\t\trgb \{\n\t\t\tcompatible = "rockchip,rk3568-rgb";\n'
    r'\t\t\tpinctrl-names = "default";\n'
    r'\t\t\tpinctrl-0 = )<0x30>;\n\t\t\tstatus = "disabled";',
    r'\g<1><0x1b2>;\n\t\t\tstatus = "okay";', src)

# 3. rgb_in_vp2 endpoint (remote 0x19, phandle 0x8d): enable.
src = sub1(
    r'(remote-endpoint = <0x19>;\n\t{6})status = "disabled";',
    r'\g<1>status = "okay";', src)

# 4. rgb ports: add port@1 -> panel after port@0 (closes with phandle 0x8d).
port1 = (
    "\t\t\t\t\tphandle = <0x8d>;\n"
    "\t\t\t\t\t};\n"
    "\t\t\t\t};\n\n"
    "\t\t\t\tport@1 {\n"
    "\t\t\t\t\treg = <0x01>;\n"
    "\t\t\t\t\t#address-cells = <0x01>;\n"
    "\t\t\t\t\t#size-cells = <0x00>;\n\n"
    "\t\t\t\t\tendpoint@0 {\n"
    "\t\t\t\t\t\treg = <0x00>;\n"
    "\t\t\t\t\t\tremote-endpoint = <0x2b1>;\n"
    "\t\t\t\t\t\tphandle = <0x2b2>;\n"
    "\t\t\t\t\t};\n"
    "\t\t\t\t};\n"
)
src = sub1(r'\t{5}phandle = <0x8d>;\n\t{5}\};\n\t{4}\};\n', port1, src)

# 5. root: the bt1120 panel (640x480@60, BT1120 16-bit YCbCr bus format).
panel = """\tbt1120-panel {
\t\tcompatible = "simple-panel";
\t\tbus-format = <0x2011>;
\t\tstatus = "okay";
\t\tphandle = <0x2b3>;

\t\tport {

\t\t\tendpoint {
\t\t\t\tremote-endpoint = <0x2b2>;
\t\t\t\tphandle = <0x2b1>;
\t\t\t};
\t\t};

\t\tdisplay-timings {
\t\t\tnative-mode = <0x2b4>;

\t\t\ttiming0 {
\t\t\t\tclock-frequency = <25175000>;
\t\t\t\thactive = <640>;
\t\t\t\tvactive = <480>;
\t\t\t\thfront-porch = <16>;
\t\t\t\thback-porch = <48>;
\t\t\t\thsync-len = <96>;
\t\t\t\tvfront-porch = <10>;
\t\t\t\tvback-porch = <33>;
\t\t\t\tvsync-len = <2>;
\t\t\t\thsync-active = <0>;
\t\t\t\tvsync-active = <0>;
\t\t\t\tde-active = <1>;
\t\t\t\tpixelclk-active = <0>;
\t\t\t\tphandle = <0x2b4>;
\t\t\t};
\t\t};
\t};

\tsyscon@fdc70000 {"""
src = sub1(r'\tsyscon@fdc70000 \{', panel, src)

open('fdt_new.dts', 'w').write(src)
print(f"OK {edits} edits applied")

# 6. VOP2 per-VP plane assignment: the DTB pins vp0=0x05/2, vp1=0x0a/3 and
#    leaves vp2 with nothing (mask 0x0 -> CRTC unusable).  Steal Esmart1 (phy 3)
#    from the unused vp1: vp1 -> Cluster1 only, vp2 -> Esmart1.
src = sub1(
    r'rockchip,plane-mask = <0x0a>;\n(\t{4})rockchip,primary-plane = <0x03>;\n(\t{4})phandle = <0x165>;',
    r'rockchip,plane-mask = <0x02>;\n\g<1>rockchip,primary-plane = <0x01>;\n\g<2>phandle = <0x165>;',
    src)
src = sub1(
    r'(\t{4})(phandle = <0x166>;\n)',
    r'\g<1>rockchip,plane-mask = <0x08>;\n\g<1>rockchip,primary-plane = <0x03>;\n\g<1>\g<2>',
    src)

open('fdt_new.dts', 'w').write(src)
print(f"OK total {edits} edits")
