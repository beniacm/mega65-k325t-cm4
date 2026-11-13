#!/usr/bin/env python3
"""test_vim.py -- the IDE's vim files give a ca65 buffer F5 / F7 / K (rockchip/ide/vim + vimrc.local).

Runs the PC's vim in silent Ex mode with only this repo's vim files on top of vim's own runtime.
A fixture ftplugin/asm.vim like vim 9.0's (Debian 12: sets b:did_ftplugin, no b:undo_ftplugin) is put
in front, because filetype.vim's *.s rule (asm) fires before the ca65 ftdetect rule: with that file,
ftplugin/ca65.vim used to skip itself and F5 did nothing on the Radxa CM3 (2026-11-13).
Also: a C file in a project whose Makefile uses mos-mega65-clang gets F5 / F7.
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ASM90 = 'if exists("b:did_ftplugin") | finish | endif\nsetl commentstring=;%s\nlet b:did_ftplugin = 1\n'


def probe(d, name, fixture):
    rtp = os.path.join(HERE, "vim")
    fix = os.path.join(d, "fixture")
    os.makedirs(os.path.join(fix, "ftplugin"), exist_ok=True)
    with open(os.path.join(fix, "ftplugin", "asm.vim"), "w") as f:
        f.write(ASM90)
    with open(os.path.join(d, "vimrc"), "w") as f:
        f.write("set rtp^=%s\n" % rtp + ("set rtp^=%s\n" % fix if fixture else "")
                + "source %s\n" % os.path.join(HERE, "vimrc.local"))
    out = os.path.join(d, "out.txt")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(["vim", "-N", "-u", os.path.join(d, "vimrc"), "-i", "NONE", "-es", name,
                    "-c", "redir! > " + out,
                    "-c", 'echo "ft=" . &ft . "|F5=" . maparg("<F5>", "n") . "|F7=" . maparg("<F7>", "n")'
                          ' . "|iF5=" . maparg("<F5>", "i") . "|kp=" . &l:keywordprg',
                    "-c", "redir END", "-c", "qa!"], cwd=d, timeout=30, check=False,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dict(kv.split("=", 1) for kv in open(out).read().strip().split("|"))


def main():
    if not shutil.which("vim"):
        print("SKIP: no vim")
        return 0
    fails = 0
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "main.s"), "w").write("lda #1\n")
        open(os.path.join(d, "main.c"), "w").write("int main(void) { return 0; }\n")
        open(os.path.join(d, "Makefile"), "w").write("CC = mos-mega65-clang\nrun:\n\tm65run main.prg\n")
        for fixture in (False, True):
            r = probe(d, "main.s", fixture)
            ok = (r["ft"] == "ca65" and "M65Make('run')" in r["F5"] and "M65Make('')" in r["F7"]
                  and "M65Make('run')" in r["iF5"] and r["kp"] == "m65doc")
            print("%s main.s (%s): %s" % ("PASS" if ok else "FAIL",
                                          "vim 9.0 asm ftplugin" if fixture else "this vim", r))
            fails += not ok
            r = probe(d, "main.c", fixture)
            ok = r["ft"] == "c" and "M65Make('run')" in r["F5"] and "M65Make('')" in r["F7"]
            print("%s main.c (%s): %s" % ("PASS" if ok else "FAIL",
                                          "vim 9.0 asm ftplugin" if fixture else "this vim", r))
            fails += not ok
    print("test_vim: %s" % ("PASS" if not fails else "%d FAIL" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
