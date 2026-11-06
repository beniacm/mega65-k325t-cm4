#!/bin/sh
# stop m65d + m65io (e.g. before mounting card.img on the Rockchip); start: m65d_restart.sh
systemctl stop m65d.service m65io.service
sync
