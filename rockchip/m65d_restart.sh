#!/bin/sh
# (re)start the Rockchip MEGA65 service: m65io (C hot loop: SD + monitor pipe :4510)
# and m65d.py (control :4511).  Extra arguments go to m65io, e.g. -s 25000000 -i card.img
# Logs: /root/m65/m65io.log, /root/m65/m65d.log
cd /root/m65 || exit 1
sh /root/m65/m65d_stop.sh
python3 /root/m65/rk_pins.py >/dev/null   # release BT1120-muxed CM4 pads first
[ m65io -nt m65io.c ] || gcc -O2 -Wall -o m65io m65io.c || exit 1
nohup ./m65io "$@" > m65io.log 2>&1 < /dev/null &
nohup python3 m65d.py > m65d.log 2>&1 < /dev/null &
sleep 1.5
cat m65io.log m65d.log
