#!/bin/sh
# (re)start m65io (SD + monitor pipe :4510) and m65d (control :4511, USB input) through systemd;
# does not reset the core.  Extra arguments go to m65io until the next reboot, e.g.
# m65d_restart.sh -s 25000000 -i card.img.   Logs: /root/m65/m65io.log, /root/m65/m65d.log
if [ $# -gt 0 ]; then systemctl set-environment M65IO_ARGS="$*"; else systemctl unset-environment M65IO_ARGS; fi
systemctl restart m65d.service m65io.service
sleep 1.5
cat /root/m65/m65io.log /root/m65/m65d.log
