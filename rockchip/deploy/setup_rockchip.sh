#!/bin/bash
# setup_rockchip.sh -- put the MEGA65/C64 bridge deployment onto a Luckfox Core3566, or check one.
# Runs on the PC, talks to the Rockchip over SSH (root key login).
#
#   rockchip/deploy/setup_rockchip.sh check   [root@HOST]   read-only: compare the board with the repo
#   rockchip/deploy/setup_rockchip.sh install [root@HOST]   copy files, install + enable the service
#
# Default host: root@192.168.1.143.  Units: m65d.service (+ m65io.service), m65-bootreset.service.
# The boot partition (BT1120 + sdmmc2 DTB) is NOT written by this
# script -- see README.md "Rebuilding the Rockchip from scratch", step 2.  card.img is not created
# either (README "SD card image").  install does not restart anything; reboot afterwards.
set -u
MODE=${1:-check}
HOST=${2:-root@192.168.1.143}
HERE=$(cd "$(dirname "$0")/.." && pwd)            # .../rockchip
DEST=/root/m65
# files deployed to /root/m65 (everything the service and the PC-side tools call)
FILES="m65d_restart.sh m65d_stop.sh m65_bootreset.py m65io.c m65d.py m65input.py m65flash.py
       rk_pins.py uinput_test.py input_bench.py input_selftest.py linktest.py usbtest.sh
       m65term.py m65run"
UNITS="m65d.service m65io.service m65-bootreset.service m65term.service"
ENABLE="m65d.service m65-bootreset.service m65term.service"   # m65io.service comes with m65d (Wants)
# MEGA65 assembler IDE (vim + ca65): repo path under rockchip/ -> installed path
IDE="ide/vim/syntax/ca65.vim:/usr/share/vim/vimfiles/syntax/ca65.vim
     ide/vim/ftdetect/ca65.vim:/usr/share/vim/vimfiles/ftdetect/ca65.vim
     ide/vim/ftplugin/ca65.vim:/usr/share/vim/vimfiles/ftplugin/ca65.vim
     ide/vimrc.local:/etc/vim/vimrc.local
     ide/45gs02.txt:/usr/local/share/m65ide/45gs02.txt
     ide/m65doc:/usr/local/bin/m65doc
     ide/m65new:/usr/local/bin/m65new"
# sha256 of the deployed boot image (~/qmtech: rockchip/boot_dtb/boot_sd.img, 24863744 bytes) and
# of the factory p3 dump (~/qmtech: rockchip/p3_boot_backup.img.xz)
BOOT_LEN=24863744
BOOT_SHA=a34f418cacd9a595b6f1876c0fe43b235d6f210ff1268d1c3aace4c96db766b7
FACTORY_SHA=bc8d2529492bab7dbf18632f6b1653acfbcc5e8632fb3da702e6c17fb9ea9531

# the board's sshd resets connections now and then: retry
rsh() { local i; for i in 1 2 3 4 5; do timeout 300 ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "$@" && return 0; sleep 2; done; return 1; }

md5l() { md5sum "$1" | cut -d' ' -f1; }

check() {
  local bad=0 out f
  out=$(rsh "cd $DEST 2>/dev/null; md5sum $(echo $FILES) 2>/dev/null; echo CFG \$(md5sum < /boot/config.txt | cut -d' ' -f1);
    for u in $UNITS; do echo UNIT \$u \$(md5sum < /etc/systemd/system/\$u 2>/dev/null | cut -d' ' -f1) \$(stat -c %a /etc/systemd/system/\$u 2>/dev/null) \$(systemctl is-enabled \$u 2>/dev/null) \$(systemctl is-active \$u 2>/dev/null); done;
    echo GOV \$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor);
    echo BOOT \$(head -c $BOOT_LEN /dev/block/by-name/boot | sha256sum | cut -d' ' -f1);
    echo FACT \$(sha256sum < /dev/block/by-name/boot | cut -d' ' -f1);
    echo SPIDEV \$(ls /dev/spidev3.0 2>/dev/null) \$(python3 -c 'import spidev; print(\"py-ok\")' 2>/dev/null);
    echo GCC \$(command -v gcc);
    for f in $(for e in $IDE; do printf '%s ' ${e#*:}; done); do echo IDE \$(md5sum < \$f 2>/dev/null | cut -d' ' -f1) \$f; done;
    echo CA65 \$(ca65 --version 2>&1 | head -1);
    echo CARD \$(stat -c %s $DEST/card.img 2>/dev/null)") || { echo "cannot reach $HOST"; exit 2; }
  for f in $FILES; do
    if echo "$out" | grep -q "^$(md5l "$HERE/$f")  $f\$"; then echo "ok    $DEST/$f"; else echo "DIFF  $DEST/$f"; bad=1; fi
  done
  [ "$(echo "$out" | awk '/^CFG/{print $2}')" = "$(md5l "$HERE/deploy/config.txt")" ] \
    && echo "ok    /boot/config.txt" || { echo "DIFF  /boot/config.txt"; bad=1; }
  local u
  for u in $UNITS; do
    set -- $(echo "$out" | awk -v u=$u '$1=="UNIT" && $2==u {print $3, $4, $5, $6}')
    [ "${1:-}" = "$(md5l "$HERE/$u")" ] && echo "ok    $u (mode ${2:-?}, ${3:-?}, ${4:-?})" \
      || { echo "DIFF  $u content"; bad=1; }
    [ "${2:-}" = 644 ] || echo "WARN  $u mode ${2:-missing} (want 644)"
    case " $ENABLE " in *" $u "*) [ "${3:-}" = enabled ] || { echo "DIFF  $u not enabled"; bad=1; };; esac
  done
  [ "$(echo "$out" | awk '/^GOV/{print $2}')" = performance ] && echo "ok    cpufreq governor performance" \
    || echo "WARN  cpufreq governor $(echo "$out" | awk '/^GOV/{print $2}') (m65d.service sets performance)"
  case "$(echo "$out" | awk '/^BOOT/{print $2}')" in
    $BOOT_SHA) echo "ok    boot partition = boot_sd.img (BT1120 + sdmmc2 DTB)";;
    *) if [ "$(echo "$out" | awk '/^FACT/{print $2}')" = "$FACTORY_SHA" ]; then
         echo "WARN  boot partition = factory image (untested with this deployment, see README)"
       else echo "DIFF  boot partition: unknown image"; bad=1; fi;;
  esac
  echo "$out" | grep -q "^SPIDEV /dev/spidev3.0 py-ok" && echo "ok    /dev/spidev3.0 + python3 spidev" \
    || { echo "DIFF  spidev missing (config.txt spi3cs1=on applied? reboot after install)"; bad=1; }
  echo "$out" | grep -q "^GCC /" && echo "ok    gcc" || { echo "DIFF  gcc missing (apt install gcc)"; bad=1; }
  local e
  for e in $IDE; do
    echo "$out" | grep -q "^IDE $(md5l "$HERE/${e%%:*}") ${e#*:}\$" && echo "ok    ${e#*:}" \
      || { echo "DIFF  ${e#*:}"; bad=1; }
  done
  echo "$out" | grep -q "^CA65 ca65 V" && echo "ok    $(echo "$out" | sed -n 's/^CA65 //p')" \
    || echo "WARN  ca65 missing (run deploy/build_tools.sh on the Rockchip)"
  local card; card=$(echo "$out" | awk '/^CARD/{print $2}')
  [ -n "$card" ] && echo "ok    card.img $((card / 1048576)) MB" || { echo "DIFF  $DEST/card.img missing (README: SD card image)"; bad=1; }
  [ $bad = 0 ] && echo "== $HOST matches the repo" || echo "== $HOST differs (see DIFF lines)"
  return $bad
}

install() {
  echo "copying to $HOST:$DEST"
  tar -C "$HERE" -cf - $FILES $UNITS deploy/config.txt ide | \
    rsh "mkdir -p $DEST && tar -C $DEST -xf - && cd $DEST && chmod 644 $(echo $FILES) && rm -f /usr/local/bin/m65run && install -m 755 m65run /usr/local/bin/m65run && rm -f m65io &&
         for u in $UNITS; do install -m 644 \$u /etc/systemd/system/\$u && rm \$u || exit 1; done &&
         if ! cmp -s deploy/config.txt /boot/config.txt; then cp /boot/config.txt /boot/config.txt.pre-m65 &&
           cp deploy/config.txt /boot/config.txt && echo 'config.txt replaced (old: /boot/config.txt.pre-m65)'; fi &&
         rm -r deploy && gcc -O2 -Wall -o m65io m65io.c &&
         for e in $(echo $IDE); do install -D -m \$(case \${e#*:} in /usr/local/bin/*) echo 755;; *) echo 644;; esac) \${e%%:*} \${e#*:} || exit 1; done && rm -r ide &&
         systemctl daemon-reload && systemctl enable $ENABLE" || { echo "install FAILED"; exit 1; }
  echo "installed; m65io built.  Reboot the Rockchip (or: ssh ... sh $DEST/m65d_restart.sh), then: $0 check $HOST"
}

case "$MODE" in
  check) check;;
  install) install;;
  *) sed -n '2,10p' "$0"; exit 1;;
esac
