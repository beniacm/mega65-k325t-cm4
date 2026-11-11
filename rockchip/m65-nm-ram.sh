#!/bin/sh
# m65-nm-ram.sh start|stop -- NetworkManager's state directory in RAM (Radxa CM3, Debian 12).
#
# NetworkManager rewrites its DHCP lease file /var/lib/NetworkManager/internal-<uuid>-<if>.lease at
# every renewal (new file + rename: ~250 eMMC sectors each time, every 15 min with this router).  NM
# has no option for another lease directory, so a tmpfs is mounted over /var/lib/NetworkManager before
# NetworkManager starts and seeded from the persistent copy /var/lib/m65-nm:
#  - secret_key   (NM's per-host secret: stable-privacy IPv6 addresses, "stable" ids / DUIDs) stays
#                  the same, so everything NM derives from it is unchanged;
#  - NetworkManager.state  (WirelessEnabled=false: Wi-Fi stays off);
#  - the lease files: NM asks for the same address at boot (INIT-REBOOT with Requested-IP).
# The DHCP client-id is the MAC (01:<MAC>, NM's internal client default), not derived from a file.
# stop (at shutdown, after NetworkManager): files whose content changed are copied back, e.g. a new
# address or a radio switched with nmcli; normally nothing differs and nothing is written.
# "timestamps" (rewritten at every activation) is not copied back.
D=/var/lib/NetworkManager
S=/var/lib/m65-nm
case "$1" in
start)
  mountpoint -q $D && exit 0
  if [ ! -d $S ]; then                       # first start: the current directory is the seed
    mkdir -p $S && chmod 700 $S && cp -a $D/. $S/ || exit 1
  fi
  mount -t tmpfs -o mode=0700,size=1m,nosuid,nodev,noexec m65-nm $D || exit 1
  cp -a $S/. $D/
  ;;
stop)
  mountpoint -q $D || exit 0
  for f in $D/*; do
    [ -f "$f" ] || continue
    b=${f##*/}
    case "$b" in timestamps|seen-bssids) continue;; esac
    cmp -s "$f" "$S/$b" || cp -p "$f" "$S/$b"
  done
  ;;
*) echo "usage: $0 start|stop" >&2; exit 2;;
esac
exit 0
