#!/bin/sh
# stop m65io + m65d (anchored patterns: never match the calling shell)
for p in $(pgrep -f "^python3 m65d.py") $(pgrep -f "^\./m65io"); do kill "$p"; done
sleep 0.5; sync
