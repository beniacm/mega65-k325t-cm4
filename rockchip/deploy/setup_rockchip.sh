#!/bin/bash
# setup_rockchip.sh -- put the MEGA65/C64 bridge deployment onto a Luckfox Core3566 or a Radxa CM3,
# or check one.  Runs on the PC, talks to the Rockchip over SSH (root key login).
#
#   rockchip/deploy/setup_rockchip.sh check   [root@HOST]   read-only: compare the board with the repo
#   rockchip/deploy/setup_rockchip.sh install [root@HOST]   copy files, install + enable the service
#
# Default host: root@192.168.1.143.  Units: m65d.service (+ m65io.service), m65-bootreset.service.
# The board is detected from /proc/device-tree/model:
#  - Luckfox Core3566 (Debian 10, default): SPI3 via /boot/config.txt (spi3cs1=on = spi3-m1).  The boot
#    partition (BT1120 + sdmmc2 DTB) is NOT written by this script -- see README.md "Rebuilding the
#    Rockchip from scratch", step 2.
#  - Radxa CM3 (Debian 12, "Radxa CM3 ..."): no config.txt; SPI3 = Radxa's overlay
#    rk3568-spi3-m0-cs0-spidev in /boot/dtbo (enabled by renaming *.dtbo.disabled + u-boot-update ->
#    fdtoverlays in extlinux.conf).  Packages it lacks (python3-spidev, python3-dev) are installed
#    with apt, kbd too (m65d's loadkeys).  The Linux picture: rockchip/cm3/$M65_BT656.dts (default
#    bt656-576p50; bt656-576i50 = the interlaced fallback) is compiled with dtc on the PC and
#    installed as /boot/dtbo/<name>.dtbo (the other variant is removed); it turns the module's own
#    HDMI off.  check compares the installed .dtbo with the repo and the live display state.
# Both: /root/m65/m65d.env gets M65_PLUG_HOST=$M65_PLUG_HOST (default 192.168.1.133:4530, the PC
# that runs scripts/plugd.py; M65_PLUG_HOST= (empty) = no plugd, the line is removed); other lines
# in m65d.env are kept.
# Event logs: m65io / m65d write to /run/m65 (RAM, tmpfiles-m65.conf); /root/m65/m65io.log and
# m65d.log are links to them.  install removes old log files from $DEST and the persistent journal
# files of the image's first boots (/var/log/journal; journald is volatile, journald-m65.conf);
# timesyncd-m65.conf stops timesyncd's 60 s clock-file stamp.
# Radxa CM3: NetworkManager's state directory (DHCP lease rewritten at every renewal) is a tmpfs seeded
# from /var/lib/m65-nm (m65-nm-ram.service + m65-nm-ram.sh; secret_key, NetworkManager.state and the
# lease stay persistent there, changed files are copied back only at shutdown).  The Luckfox's network
# stack is left as it is.
# Radxa CM3: /tmp in RAM (tmp-m65.mount -> /etc/systemd/system/tmp.mount, 256 MB); the Luckfox keeps
# its /tmp.
# card.img is not created either (README "SD card image").  install does not restart anything;
# reboot afterwards.
set -u
MODE=${1:-check}
HOST=${2:-root@192.168.1.143}
TZ=${M65_TZ:-Europe/Bratislava}                    # the MEGA65's RTC is set in local time
HERE=$(cd "$(dirname "$0")/.." && pwd)            # .../rockchip
DEST=/root/m65
# files deployed to /root/m65 (everything the service and the PC-side tools call)
FILES="m65d_restart.sh m65d_stop.sh m65_bootreset.py m65io.c m65d.py m65input.py m65flash.py
       uinput_test.py input_bench.py input_selftest.py linktest.py usbtest.sh
       m65run lxshell/lxcore/__init__.py
       lxshell/lxcore/bus.py lxshell/lxcore/display.py lxshell/lxcore/keys.py lxshell/lxcore/menu.py
       lxshell/lxcore/browser.py lxshell/lxcore/pages.py lxshell/lxcore/settings.py
       lxshell/lxcore/runner.py lxshell/lxcore/fatimg.py lxshell/lxamiga/__init__.py
       lxshell/lxamiga/m2m.py lxshell/lxamiga/shell.py"
UNITS="m65d.service m65io.service m65-bootreset.service"
ENABLE="m65d.service m65-bootreset.service"   # m65io.service comes with m65d (Wants)
# removed from the deployment (install takes them off the board)
GONE_UNITS="m65term.service"
GONE_FILES="m65term.py rk_pins.py /run/m65term.sock /run/m65term.saved"
# MEGA65 assembler IDE (vim + ca65): repo path under rockchip/ -> installed path
IDE="ide/vim/syntax/ca65.vim:/usr/share/vim/vimfiles/syntax/ca65.vim
     ide/vim/ftdetect/ca65.vim:/usr/share/vim/vimfiles/ftdetect/ca65.vim
     ide/vim/ftplugin/ca65.vim:/usr/share/vim/vimfiles/ftplugin/ca65.vim
     ide/vim/ftplugin/c/m65.vim:/usr/share/vim/vimfiles/ftplugin/c/m65.vim
     ide/vim/plugin/m65.vim:/usr/share/vim/vimfiles/plugin/m65.vim
     ide/vimrc.local:/etc/vim/vimrc.local
     ide/45gs02.txt:/usr/local/share/m65ide/45gs02.txt
     ide/mega65_regs.inc:/usr/local/share/m65ide/asminc/mega65_regs.inc
     ide/m65doc:/usr/local/bin/m65doc
     ide/m65new:/usr/local/bin/m65new
     ide/sysctl-console.conf:/etc/sysctl.d/90-m65-console.conf
     ide/ssh-runtime-preserve.conf:/etc/systemd/system/ssh@.service.d/m65-preserve.conf
     m65-safe.shutdown:/lib/systemd/system-shutdown/m65-safe.shutdown
     journald-m65.conf:/etc/systemd/journald.conf.d/m65-volatile.conf
     tmpfiles-m65.conf:/etc/tmpfiles.d/m65-run.conf
     timesyncd-m65.conf:/etc/systemd/timesyncd.conf.d/m65.conf"
# sha256 of the deployed boot image (rockchip/boot_dtb/boot_sd.img.xz unpacked, 24863744 bytes: BT1120
# 720x576@50 for linux_fb), of its 720p60 predecessor (boot_sd_720p.img.xz) and
# of the factory p3 dump (rockchip/boot_dtb/p3_boot_backup.img.xz)
BOOT_LEN=24863744
BOOT_SHA=35f646ee0b55285fe7d680d5a9623155fedf086a833ee4f3e85a3a27aacc8930
BOOT720_SHA=a34f418cacd9a595b6f1876c0fe43b235d6f210ff1268d1c3aace4c96db766b7
FACTORY_SHA=bc8d2529492bab7dbf18632f6b1653acfbcc5e8632fb3da702e6c17fb9ea9531
# Radxa CM3: SPI3 M0 CS0 overlay (pads GPIO4_A6 CS, B0 MISO, B2 MOSI, B3 CLK = BCM8..11)
RADXA_SPI_OVL=rk3568-spi3-m0-cs0-spidev
RADXA_SPI_PINS="134 136 138 139"
# Radxa CM3, AExp A1200 hard disk (opt-in: M65_HDD=1 install): the second SPI, spi0-m0 CS0 (pads GPIO0_B5
# CLK, B6 MOSI, C5 MISO, C6 CS = BCM3, 2, 6, 13) for hddsrv; the attention line BCM7 = GPIO4_C5 is a plain
# GPIO (gpiochip4 line 21).  Revert: mv the .dtbo back to .dtbo.disabled, u-boot-update, reboot.
RADXA_HDD_OVL=rk3568-spi0-m0-cs0-spidev
# pads that carry the Linux picture to the FPGA (BT.656: GPIO4_B4 clock, GPIO3_C6..D5 data): no
# other function may own them (only the video encoder, once the BT.656 overlay is in)
RADXA_VIDEO_PINS="140 118 119 120 121 122 123 124 125"
# Radxa CM3: the BT.656 overlay (rockchip/cm3/<name>.dts) and its byte clock (VOP dclk = 2x pixel clock)
RADXA_VIDEO_OVL=${M65_BT656:-bt656-576p50}
case "$RADXA_VIDEO_OVL" in bt656-576i50) RADXA_DCLK=27000000;; *) RADXA_DCLK=54000000;; esac
# Ctrl+Alt+End power-off: plugd on the PC with the smart plug (README "Real-time clock and safe power-off")
PLUG_HOST=${M65_PLUG_HOST-192.168.1.133:4530}
# Radxa CM3: NetworkManager state in RAM (unit, script -> installed path)
RADXA_NMRAM_UNIT=m65-nm-ram.service
RADXA_NMRAM_SH=/usr/local/sbin/m65-nm-ram
# Radxa CM3: /tmp in RAM
RADXA_TMP_UNIT=tmp-m65.mount
# Radxa CM3: units of the (KDE) image that a headless bridge box does not need -- install disables
# them, check warns.  Daily apt/man-db jobs rewrite tens of MB on the eMMC (apt by hand still works);
# radxa-adbd restart-loops every 5 s while the OTG port is in host mode (adb never worked on this
# carrier: no VBUS sense).  Kept: ssh, NetworkManager, timesyncd, haveged, irqbalance, zramswap.
RADXA_OFF="cups.service cups.socket cups.path samba-ad-dc.service avahi-daemon.service avahi-daemon.socket
  rkaiq_3A.service rknpu2.service rockchip-chromium-x11-utils.service accounts-daemon.service
  udisks2.service upower.service rtkit-daemon.service fwupd-refresh.timer radxa-adbd@fcc00000.usb.service
  radxa-adbd@fcc00000.dwc3.service apt-daily.timer apt-daily-upgrade.timer man-db.timer
  bluetooth.service wpa_supplicant.service"

# the board's sshd resets connections now and then: retry
rsh() { local i; for i in 1 2 3 4 5; do timeout 300 ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "$@" && return 0; sleep 2; done; return 1; }

md5l() { md5sum "$1" | cut -d' ' -f1; }

# the repo's overlay source compiled on the PC (same dtc for install and check) -> $DTBO_TMP/<name>.dtbo
dtbo_build() {
  DTBO_TMP=$(mktemp -d); trap 'rm -rf "$DTBO_TMP"' EXIT
  dtc -@ -q -I dts -O dtb -o "$DTBO_TMP/$RADXA_VIDEO_OVL.dtbo" "$HERE/cm3/$RADXA_VIDEO_OVL.dts" \
    || { echo "dtc failed on $HERE/cm3/$RADXA_VIDEO_OVL.dts (apt install device-tree-compiler)"; exit 2; }
}

# luckfox | radxa-cm3 (anything that is not a Radxa CM3 keeps the Luckfox behaviour)
board() {
  local m; m=$(rsh "tr -d '\\000' < /proc/device-tree/model") || { echo "cannot reach $HOST" >&2; exit 2; }
  case "$m" in *"Radxa CM3"*) echo radxa-cm3;; *) echo luckfox;; esac
}

check() {
  local bad=0 out f B; B=$(board); echo "board $B"
  out=$(rsh "cd $DEST 2>/dev/null; md5sum $(echo $FILES) 2>/dev/null; echo CFG \$(cat /boot/config.txt 2>/dev/null | md5sum | cut -d' ' -f1);
    for u in $UNITS; do echo UNIT \$u \$(md5sum 2>/dev/null < /etc/systemd/system/\$u | cut -d' ' -f1) \$(stat -c %a /etc/systemd/system/\$u 2>/dev/null) \$(systemctl is-enabled \$u 2>/dev/null) \$(systemctl is-active \$u 2>/dev/null); done;
    echo GOV \$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor);
    if [ -e /dev/block/by-name/boot ]; then echo BOOT \$(head -c $BOOT_LEN /dev/block/by-name/boot | sha256sum | cut -d' ' -f1);
    echo FACT \$(sha256sum < /dev/block/by-name/boot | cut -d' ' -f1); fi;
    echo OVL \$(grep -m1 fdtoverlays /boot/extlinux/extlinux.conf 2>/dev/null);
    for f in /boot/dtbo/bt656*.dtbo; do [ -e \$f ] && echo DTBO \$(md5sum < \$f | cut -d' ' -f1) \$f; done;
    echo DPI \$(cat /sys/class/drm/card*-DPI-1/status 2>/dev/null);
    echo VP \$(grep -A4 'Video Port2' /sys/kernel/debug/dri/0/summary 2>/dev/null | grep -o -e ACTIVE -e 'bus_format\[[0-9a-f]*\]' -e 'Display mode: [0-9a-z]*' | tr ' ' _);
    echo DCLK \$(awk '\$1==\"dclk_vop2\" {print \$5}' /sys/kernel/debug/clk/clk_summary 2>/dev/null);
    echo ENV \$(grep -m1 '^M65_PLUG_HOST=' $DEST/m65d.env 2>/dev/null);
    echo HDD \$(systemctl is-enabled m65hdd 2>/dev/null || echo not-installed) \$(systemctl is-active m65hdd 2>/dev/null) image \$(ls -s --block-size=M $DEST/hdd/aexp.hdf 2>/dev/null || echo none);
    echo LOADKEYS \$(command -v loadkeys);
    for u in $(echo $RADXA_OFF); do [ \"\$(systemctl is-enabled \$u 2>/dev/null)\" = enabled ] && echo ON \$u; done;
    for p in $RADXA_SPI_PINS $RADXA_VIDEO_PINS; do echo PIN \$(grep \"^pin \$p \" /sys/kernel/debug/pinctrl/pinctrl-rockchip-pinctrl/pinmux-pins 2>/dev/null); done;
    echo STATS \$(readlink $DEST/m65io.stats);
    echo LOGS \$(readlink $DEST/m65io.log) \$(readlink $DEST/m65d.log) \$(ls $DEST/*.log.1 2>/dev/null);
    echo PJOURNAL \$(find /var/log/journal -type f 2>/dev/null | wc -l) \$(journalctl --header 2>/dev/null | grep -c 'File path: /var/log');
    echo DEFTGT \$(systemctl get-default);
    echo SPIDEV \$(ls /dev/spidev3.0 2>/dev/null) \$(python3 -c 'import spidev; print(\"py-ok\")' 2>/dev/null);
    echo GCC \$(command -v gcc);
    for f in $(for e in $IDE; do printf '%s ' ${e#*:}; done); do echo IDE \$(md5sum 2>/dev/null < \$f | cut -d' ' -f1) \$f; done;
    echo CA65 \$(ca65 --version 2>&1 | head -1);
    echo LLVMMOS \$(/opt/llvm-mos/bin/clang --version 2>/dev/null | head -1);
    echo TZ \$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone);
    echo CAD \$(systemctl is-enabled ctrl-alt-del.target 2>/dev/null);
    echo NMRAM \$(md5sum 2>/dev/null < /etc/systemd/system/$RADXA_NMRAM_UNIT | cut -d' ' -f1) \$(md5sum 2>/dev/null < $RADXA_NMRAM_SH | cut -d' ' -f1) \$(systemctl is-enabled $RADXA_NMRAM_UNIT 2>/dev/null) \$(systemctl is-active $RADXA_NMRAM_UNIT 2>/dev/null) \$(findmnt -n -o FSTYPE /var/lib/NetworkManager) \$(cmp -s /var/lib/NetworkManager/secret_key /var/lib/m65-nm/secret_key && echo key-same);
    echo TMPFS \$(md5sum 2>/dev/null < /etc/systemd/system/tmp.mount | cut -d' ' -f1) \$(systemctl is-enabled tmp.mount 2>/dev/null) \$(findmnt -n -o FSTYPE /tmp);
    echo CARD \$(stat -c %s $DEST/card.img 2>/dev/null)") || { echo "cannot reach $HOST"; exit 2; }
  for f in $FILES; do
    if echo "$out" | grep -q "^$(md5l "$HERE/$f")  $f\$"; then echo "ok    $DEST/$f"; else echo "DIFF  $DEST/$f"; bad=1; fi
  done
  if [ $B = luckfox ]; then
  [ "$(echo "$out" | awk '/^CFG/{print $2}')" = "$(md5l "$HERE/deploy/config.txt")" ] \
    && echo "ok    /boot/config.txt" || { echo "DIFF  /boot/config.txt"; bad=1; }
  fi
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
  if [ $B = luckfox ]; then
  case "$(echo "$out" | awk '/^BOOT/{print $2}')" in
    $BOOT_SHA) echo "ok    boot partition = boot_sd.img (BT1120 576p50 + sdmmc2 DTB)";;
    $BOOT720_SHA) echo "WARN  boot partition = boot_sd_720p.img (Linux picture cropped to its top-left 720x576)";;
    *) if [ "$(echo "$out" | awk '/^FACT/{print $2}')" = "$FACTORY_SHA" ]; then
         echo "WARN  boot partition = factory image (untested with this deployment, see README)"
       else echo "DIFF  boot partition: unknown image"; bad=1; fi;;
  esac
  else
    local ovl p own; ovl=$(echo "$out" | sed -n 's/^OVL *//p')
    case "$ovl" in *"/$RADXA_SPI_OVL.dtbo"*) echo "ok    overlay $RADXA_SPI_OVL in extlinux.conf";;
      *) echo "DIFF  overlay $RADXA_SPI_OVL not in extlinux.conf ($ovl)"; bad=1;; esac
    case "$ovl" in *"/$RADXA_HDD_OVL.dtbo"*) echo "ok    overlay $RADXA_HDD_OVL in extlinux.conf (AExp hard disk SPI, opt-in)";;
      *) echo "ok    overlay $RADXA_HDD_OVL off (AExp hard disk SPI; M65_HDD=1 install enables it)";; esac
    echo "info  m65hdd.service: $(echo "$out" | sed -n 's/^HDD *//p')"
    # the Linux picture: overlay file = the repo's, enabled, and the display pipeline live
    local dt
    dtbo_build
    dt=$(echo "$out" | awk '$1=="DTBO" {print $3}' | tr '\n' ' ')
    if [ "$(echo "$out" | awk -v f=/boot/dtbo/$RADXA_VIDEO_OVL.dtbo '$1=="DTBO" && $3==f {print $2}')" = "$(md5l "$DTBO_TMP/$RADXA_VIDEO_OVL.dtbo")" ] \
       && [ "$dt" = "/boot/dtbo/$RADXA_VIDEO_OVL.dtbo " ]; then
      echo "ok    /boot/dtbo/$RADXA_VIDEO_OVL.dtbo = rockchip/cm3/$RADXA_VIDEO_OVL.dts"
    else echo "DIFF  BT.656 overlay files: '${dt:-none}' (want only /boot/dtbo/$RADXA_VIDEO_OVL.dtbo from the repo)"; bad=1; fi
    case "$ovl" in *"/$RADXA_VIDEO_OVL.dtbo"*) echo "ok    overlay $RADXA_VIDEO_OVL in extlinux.conf";;
      *) echo "DIFF  overlay $RADXA_VIDEO_OVL not in extlinux.conf (u-boot-update; no Linux picture)"; bad=1;; esac
    local vp; vp=$(echo "$out" | sed -n 's/^VP *//p')
    if [ "$(echo "$out" | awk '/^DPI/{print $2}')" = connected ] && [ "$(echo "$out" | awk '/^DCLK/{print $2}')" = $RADXA_DCLK ] \
       && echo "$vp" | grep -q 'ACTIVE.*bus_format\[2006\].*Display_mode:_720x576'; then
      echo "ok    DPI-1 connected, VP2 $(echo "$vp" | grep -o 'mode:_[0-9a-z]*' | cut -c7-) BT.656 (UYVY8_2X8), dclk_vop2 $RADXA_DCLK"
    else
      echo "DIFF  Linux picture not live: DPI '$(echo "$out" | awk '/^DPI/{print $2}')', VP2 '$vp', dclk_vop2 '$(echo "$out" | awk '/^DCLK/{print $2}')' (want $RADXA_DCLK; reboot after install?)"; bad=1
    fi
    for p in $RADXA_SPI_PINS; do
      own=$(echo "$out" | awk -v p=$p '$1=="PIN" && $3==p {print $5}')
      [ "$own" = fe640000.spi ] && echo "ok    pad $p owned by spi3" || { echo "DIFF  pad $p owned by '${own:-?}' (want fe640000.spi, reboot after install?)"; bad=1; }
    done
    local vbad=0
    for p in $RADXA_VIDEO_PINS; do
      own=$(echo "$out" | awk -v p=$p '$1=="PIN" && $3==p {print $5}')
      case "$own" in *rgb*) ;; *) echo "DIFF  video pad $p owned by '${own:-?}' (want the BT.656 encoder fdc60000.syscon:rgb)"; bad=1; vbad=1;; esac
    done
    [ $vbad = 0 ] && echo "ok    video pads (GPIO4_B4, GPIO3_C6..D5) owned by the BT.656 encoder"
    local on; on=$(echo "$out" | awk '$1=="ON" {printf "%s ", $2}')
    [ -z "$on" ] && echo "ok    image services not needed here are disabled" || echo "WARN  enabled, not needed: $on(install disables them)"
    set -- $(echo "$out" | awk '$1=="NMRAM" {print $2, $3, $4, $5, $6, $7}')
    if [ "${1:-}" = "$(md5l "$HERE/$RADXA_NMRAM_UNIT")" ] && [ "${2:-}" = "$(md5l "$HERE/m65-nm-ram.sh")" ] \
       && [ "${3:-}" = enabled ] && [ "${4:-}" = active ] && [ "${5:-}" = tmpfs ] && [ "${6:-}" = key-same ]; then
      echo "ok    /var/lib/NetworkManager in RAM ($RADXA_NMRAM_UNIT), secret_key = /var/lib/m65-nm/secret_key"
    else echo "DIFF  NetworkManager state in RAM: unit/script/enabled/active/fstype/key '$*' (install, then reboot)"; bad=1; fi
    set -- $(echo "$out" | awk '$1=="TMPFS" {print $2, $3, $4}')
    if [ "${1:-}" = "$(md5l "$HERE/$RADXA_TMP_UNIT")" ] && [ "${2:-}" = enabled ] && [ "${3:-}" = tmpfs ]; then
      echo "ok    /tmp in RAM (tmp.mount = $RADXA_TMP_UNIT)"
    else echo "DIFF  /tmp in RAM: unit/enabled/fstype '$*' (install, then reboot)"; bad=1; fi
  fi
  echo "$out" | grep -q "^SPIDEV /dev/spidev3.0 py-ok" && echo "ok    /dev/spidev3.0 + python3 spidev" \
    || { echo "DIFF  spidev missing (SPI3 enabled? python3-spidev? reboot after install)"; bad=1; }
  [ "$(echo "$out" | awk '/^STATS/{print $2}')" = /run/m65io.stats ] && echo "ok    $DEST/m65io.stats -> /run/m65io.stats (RAM)" \
    || { echo "DIFF  $DEST/m65io.stats is not a link to /run/m65io.stats"; bad=1; }
  [ "$(echo "$out" | sed -n 's/^LOGS *//p')" = "/run/m65/m65io.log /run/m65/m65d.log" ] \
    && echo "ok    $DEST/m65io.log, m65d.log -> /run/m65 (RAM), no old log files" \
    || { echo "DIFF  logs: '$(echo "$out" | sed -n 's/^LOGS *//p')' (want links to /run/m65/m65io.log, m65d.log only)"; bad=1; }
  [ "$(echo "$out" | sed -n 's/^PJOURNAL *//p')" = "0 0" ] && echo "ok    no persistent journal files (/var/log/journal)" \
    || { echo "DIFF  persistent journal: files, open: $(echo "$out" | sed -n 's/^PJOURNAL *//p') (want 0 0)"; bad=1; }
  case "$(echo "$out" | awk '/^DEFTGT/{print $2}')" in graphical.target) echo "WARN  default target graphical (desktop)";; esac
  echo "$out" | grep -q "^GCC /" && echo "ok    gcc" || { echo "DIFF  gcc missing (apt install gcc)"; bad=1; }
  # m65d voids Ctrl+Alt+Insert in the console keymap with loadkeys when the picture goes to Linux
  echo "$out" | grep -q "^LOADKEYS /" && echo "ok    loadkeys" || { echo "DIFF  loadkeys missing (apt install kbd)"; bad=1; }
  local env; env=$(echo "$out" | sed -n 's/^ENV *//p')
  if [ -n "$PLUG_HOST" ]; then
    [ "$env" = "M65_PLUG_HOST=$PLUG_HOST" ] && echo "ok    $DEST/m65d.env $env (plugd)" \
      || { echo "DIFF  $DEST/m65d.env '${env:-no M65_PLUG_HOST}' (want M65_PLUG_HOST=$PLUG_HOST)"; bad=1; }
  else
    [ -z "$env" ] && echo "ok    no plugd (M65_PLUG_HOST empty)" || { echo "DIFF  $DEST/m65d.env has '$env' (want none)"; bad=1; }
  fi
  local e
  for e in $IDE; do
    echo "$out" | grep -q "^IDE $(md5l "$HERE/${e%%:*}") ${e#*:}\$" && echo "ok    ${e#*:}" \
      || { echo "DIFF  ${e#*:}"; bad=1; }
  done
  echo "$out" | grep -q "^CA65 ca65 V" && echo "ok    $(echo "$out" | sed -n 's/^CA65 //p')" \
    || echo "WARN  ca65 missing (run deploy/build_tools.sh on the Rockchip)"
  echo "$out" | grep -q "^LLVMMOS clang version" && echo "ok    llvm-mos: $(echo "$out" | sed -n 's/^LLVMMOS //p' | cut -c1-60)" \
    || echo "WARN  llvm-mos missing (run deploy/build_llvm_mos.sh on the PC)"
  [ "$(echo "$out" | awk '/^TZ /{print $2}')" = "$TZ" ] && echo "ok    timezone $TZ" \
    || { echo "DIFF  timezone $(echo "$out" | awk '/^TZ /{print $2}') (want $TZ)"; bad=1; }
  # Ctrl+Alt+Del is the MEGA65 reset; with the keyboard released to Linux (view linux) it
  # must not reboot the Rockchip
  [ "$(echo "$out" | awk '/^CAD /{print $2}')" = "masked" ] && echo "ok    ctrl-alt-del.target masked" \
    || { echo "DIFF  ctrl-alt-del.target not masked"; bad=1; }
  local card; card=$(echo "$out" | awk '/^CARD/{print $2}')
  [ -n "$card" ] && echo "ok    card.img $((card / 1048576)) MB" || { echo "DIFF  $DEST/card.img missing (README: SD card image)"; bad=1; }
  [ $bad = 0 ] && echo "== $HOST matches the repo" || echo "== $HOST differs (see DIFF lines)"
  return $bad
}

install() {
  local B; B=$(board); echo "board $B"
  local spi="if ! cmp -s deploy/config.txt /boot/config.txt; then cp /boot/config.txt /boot/config.txt.pre-m65 &&
           cp deploy/config.txt /boot/config.txt && echo 'config.txt replaced (old: /boot/config.txt.pre-m65)'; fi"
  local vtar=""
  if [ $B = radxa-cm3 ]; then
    dtbo_build; vtar="-C $DTBO_TMP $RADXA_VIDEO_OVL.dtbo"
    spi="{ python3 -c 'import spidev' 2>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends python3-spidev python3-dev gcc libc6-dev make >/dev/null; } &&
         { command -v loadkeys >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends kbd >/dev/null; } &&
         { [ -f /boot/dtbo/$RADXA_SPI_OVL.dtbo ] || mv /boot/dtbo/$RADXA_SPI_OVL.dtbo.disabled /boot/dtbo/$RADXA_SPI_OVL.dtbo; } &&
         { [ '${M65_HDD:-0}' != 1 ] || [ -f /boot/dtbo/$RADXA_HDD_OVL.dtbo ] ||
           { mv /boot/dtbo/$RADXA_HDD_OVL.dtbo.disabled /boot/dtbo/$RADXA_HDD_OVL.dtbo && echo 'hard disk SPI overlay $RADXA_HDD_OVL enabled (reboot)'; }; } &&
         { cmp -s $RADXA_VIDEO_OVL.dtbo /boot/dtbo/$RADXA_VIDEO_OVL.dtbo || echo 'BT.656 overlay $RADXA_VIDEO_OVL installed (reboot; the module HDMI goes off)'; } &&
         for f in /boot/dtbo/bt656*.dtbo; do [ \$f = /boot/dtbo/$RADXA_VIDEO_OVL.dtbo ] || rm -f \$f; done &&
         install -m 644 $RADXA_VIDEO_OVL.dtbo /boot/dtbo/$RADXA_VIDEO_OVL.dtbo && rm $RADXA_VIDEO_OVL.dtbo &&
         install -m 755 m65-nm-ram.sh $RADXA_NMRAM_SH && install -m 644 $RADXA_NMRAM_UNIT /etc/systemd/system/$RADXA_NMRAM_UNIT &&
         { mountpoint -q /var/lib/NetworkManager || [ -d /var/lib/m65-nm ] ||
           { mkdir -m 700 /var/lib/m65-nm && cp -a /var/lib/NetworkManager/. /var/lib/m65-nm/ && echo 'NetworkManager state seeded to /var/lib/m65-nm (tmpfs after the reboot)'; }; } &&
         install -m 644 $RADXA_TMP_UNIT /etc/systemd/system/tmp.mount &&
         systemctl daemon-reload && systemctl enable -q $RADXA_NMRAM_UNIT tmp.mount &&
         u-boot-update >/dev/null 2>&1 &&
         grep -q '/$RADXA_SPI_OVL.dtbo' /boot/extlinux/extlinux.conf && grep -q '/$RADXA_VIDEO_OVL.dtbo' /boot/extlinux/extlinux.conf &&
         { systemctl disable --now $(echo $RADXA_OFF) >/dev/null 2>&1; true; }"
  fi
  # m65d.env: only the M65_PLUG_HOST line is ours
  local penv="{ grep -v '^M65_PLUG_HOST=' m65d.env 2>/dev/null; [ -z '$PLUG_HOST' ] || echo 'M65_PLUG_HOST=$PLUG_HOST'; } > m65d.env.new && mv m65d.env.new m65d.env && { [ -s m65d.env ] || rm m65d.env; }"
  echo "copying to $HOST:$DEST"
  tar -C "$HERE" -cf - $FILES $UNITS deploy/config.txt ide m65-safe.shutdown journald-m65.conf tmpfiles-m65.conf timesyncd-m65.conf m65-nm-ram.sh $RADXA_NMRAM_UNIT $RADXA_TMP_UNIT $vtar | \
    rsh "mkdir -p $DEST && tar -C $DEST -xf - && cd $DEST && chmod 644 $(echo $FILES) && rm -f /usr/local/bin/m65run && install -m 755 m65run /usr/local/bin/m65run && rm -f m65io &&
         for u in $UNITS; do install -m 644 \$u /etc/systemd/system/\$u && rm \$u || exit 1; done &&
         $spi && $penv &&
         rm -r deploy && gcc -O2 -Wall -o m65io m65io.c &&
         for e in $(echo $IDE); do install -D -m \$(case \${e#*:} in /usr/local/bin/*|*.shutdown) echo 755;; *) echo 644;; esac) \${e%%:*} \${e#*:} || exit 1; done && rm -r ide m65-safe.shutdown journald-m65.conf tmpfiles-m65.conf timesyncd-m65.conf m65-nm-ram.sh $RADXA_NMRAM_UNIT $RADXA_TMP_UNIT &&
         { systemctl try-restart systemd-timesyncd 2>/dev/null; true; } &&
         systemctl restart systemd-journald && rm -rf /var/log/journal/* &&
         systemd-tmpfiles --create /etc/tmpfiles.d/m65-run.conf &&
         for l in m65io m65d; do [ -L \$l.log ] || { [ -f \$l.log ] && cat \$l.log >> /run/m65/\$l.log; }; rm -f \$l.log \$l.log.1 && ln -s /run/m65/\$l.log \$l.log || exit 1; done &&
         rm -f m65io.stats m65io.stats.tmp && ln -s /run/m65io.stats m65io.stats &&
         for u in $GONE_UNITS; do systemctl disable --now \$u 2>/dev/null; rm -f /etc/systemd/system/\$u; done;
         rm -f $GONE_FILES && sysctl -q -p /etc/sysctl.d/90-m65-console.conf &&
         timedatectl set-timezone $TZ && echo $TZ > /etc/timezone &&
         ln -sfn /dev/null /etc/systemd/system/ctrl-alt-del.target &&    # mask (the image links it to reboot.target)
         systemctl daemon-reload && systemctl enable $ENABLE" || { echo "install FAILED"; exit 1; }
  if [ "${M65_HDD:-0}" = 1 ] && [ $B = radxa-cm3 ]; then
    # the AExp hard disk service (never on the Luckfox: no hard disk link there): m65hdd.py + the unit
    # from this repo, hddsrv's sources from the core repo (built on the module at the service start)
    local hsrc=${M65_HDDSRV:-$HOME/projects/mega65/aexp-lx/K325T/hddsrv}
    tar -cf - -C "$HERE/hdd" m65hdd.py -C "$HERE" m65hdd.service -C "$hsrc" hddsrv_core.c hddsrv_core.h hddsrv_linux.c | \
      rsh "mkdir -p $DEST/hdd && tar -C $DEST/hdd -xf - && install -m 644 $DEST/hdd/m65hdd.service /etc/systemd/system/m65hdd.service &&
           rm $DEST/hdd/m65hdd.service && rm -f $DEST/hdd/hddsrv && systemctl daemon-reload && systemctl enable -q m65hdd.service &&
           echo 'm65hdd.service installed and enabled (it runs once $DEST/hdd/aexp.hdf exists)'" || { echo "hdd install FAILED"; exit 1; }
  fi
  echo "installed; m65io built.  Reboot the Rockchip (or: ssh ... sh $DEST/m65d_restart.sh), then: $0 check $HOST"
}

case "$MODE" in
  check) check;;
  install) install;;
  *) sed -n '2,10p' "$0"; exit 1;;
esac
