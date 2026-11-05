#!/bin/sh
# (re)start m65d in the background; log in /root/m65/m65d.log
cd /root/m65 || exit 1
for p in $(pgrep -f "python3 m65d.py"); do kill "$p"; done
sleep 0.5
python3 /root/m65/rk_pins.py >/dev/null   # release BT1120-muxed CM4 pads first
nohup python3 m65d.py "$@" > m65d.log 2>&1 < /dev/null &
sleep 2
cat m65d.log
