#!/bin/sh
# switch the OTG port to host for 20 s, record what enumerates, switch back (adb returns)
F=/sys/devices/platform/fe8a0000.usb2-phy/otg_mode
L=/root/m65/usbtest.log
echo "before: $(cat $F)" > $L
sleep 2
echo host > $F
sleep 20
echo "--- lsusb (host mode)" >> $L; lsusb >> $L 2>&1
echo "--- input devices" >> $L; grep -E "^N:" /proc/bus/input/devices >> $L
echo "--- m65d inputs" >> $L; grep "m65input" /root/m65/m65d.log | tail -3 >> $L
echo otg > $F
echo "after: $(cat $F)" >> $L
