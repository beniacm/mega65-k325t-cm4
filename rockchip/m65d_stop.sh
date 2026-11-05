#!/bin/sh
# stop m65d (anchored pattern: never matches the calling shell)
for p in $(pgrep -f "^python3 m65d.py"); do kill "$p"; done
sleep 0.5; sync
