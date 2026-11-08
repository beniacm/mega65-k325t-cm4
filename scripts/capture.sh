#!/usr/bin/env bash
# capture.sh - grab frames from the MacroSilicon MS2130 USB HDMI capture dongle.
#
# Previous working setup (from ~/.claude project logs):
#   dongle: MacroSilicon MS2130, USB id 345f:2130, UVC, /dev/video0
#   USB output format: 1920x1080 YUYV (MJPEG also available); it scales
#   internally, so a 720x576p50 FPGA input shows up inside a 1080p frame.
#   capture: ffmpeg -y -f v4l2 -i /dev/video0 -frames:v 8 -update 1 out.png
#
# Usage:
#   ./capture.sh                 # 8 frames -> captures/frame_<ts>.png
#   ./capture.sh -n 30           # more frames (lets the source settle/lock)
#   ./capture.sh -d /dev/video2  # force a device
#   ./capture.sh --list          # just enumerate devices and formats
#   ./capture.sh --analyze FILE  # mean/min/max brightness of a saved frame

set -u

OUTDIR="$(cd "$(dirname "$0")/.." && pwd)/captures"
FRAMES=8
DEV=""
LIST_ONLY=0
ANALYZE=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--frames) FRAMES="$2"; shift 2 ;;
    -d|--device) DEV="$2"; shift 2 ;;
    --list) LIST_ONLY=1; shift ;;
    --analyze) ANALYZE="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

list_devices() {
  echo "== /dev/video* =="
  ls -l /dev/video* 2>/dev/null || echo "  (none)"
  echo
  echo "== v4l2 devices =="
  v4l2-ctl --list-devices 2>/dev/null || true
  echo
  echo "== USB ids =="
  lsusb | grep -i -E '345f|macrosilicon|ms2130|uvc|video' || echo "  (no capture dongle in lsusb)"
}

if [ -n "$ANALYZE" ]; then
  [ -f "$ANALYZE" ] || { echo "no such file: $ANALYZE" >&2; exit 1; }
  identify -format 'file=%f  size=%wx%h  mean=%[fx:mean]  stddev=%[fx:standard_deviation]  min=%[fx:minima]  max=%[fx:maxima]\n' "$ANALYZE"
  exit 0
fi

if [ "$LIST_ONLY" = 1 ]; then
  list_devices
  exit 0
fi

# Pick the MS2130 by name if no device was forced.
if [ -z "$DEV" ]; then
  for v in /dev/video*; do
    [ -e "$v" ] || continue
    name="$(v4l2-ctl -d "$v" --info 2>/dev/null | sed -n 's/.*Card type *: *//p')"
    case "$name" in
      *[Mm][Ss]2130*|*[Mm]acro[Ss]ilicon*|*[Uu][Ss][Bb]*[Vv]ideo*|*[Cc]apture*)
        DEV="$v"; break ;;
    esac
  done
  # Fall back to the first node.
  [ -z "$DEV" ] && DEV="$(ls /dev/video* 2>/dev/null | head -1)"
fi

if [ -z "${DEV:-}" ] || [ ! -e "$DEV" ]; then
  echo "No capture device found." >&2
  echo "Plug the MacroSilicon MS2130 HDMI capture dongle into USB and re-run." >&2
  echo >&2
  list_devices >&2
  exit 1
fi

mkdir -p "$OUTDIR"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$OUTDIR/frame_${TS}.png"
RAW="$OUTDIR/frame_${TS}.raw.png"

echo "== device $DEV =="
v4l2-ctl -d "$DEV" --info 2>/dev/null | sed -n '1,12p'
echo
echo "== current format =="
v4l2-ctl -d "$DEV" --get-fmt-video 2>/dev/null
echo

echo "Capturing $FRAMES frames -> $OUT"
# Warm-up frames: cheap dongles need a few frames to lock to a new timing.
# Ask for 720x576 YUYV explicitly: after a USB re-enumeration the MS2130 defaults to 1920x1080
# and the tests compare pixels at the 576p geometry.
if ! timeout 30 ffmpeg -hide_banner -loglevel warning -y \
      -f v4l2 -input_format yuyv422 -video_size 720x576 -i "$DEV" -frames:v "$FRAMES" -update 1 "$RAW" 2>&1; then
  echo "ffmpeg failed" >&2
  exit 1
fi

# Keep the last frame as the result (first ones may be black while locking).
cp -f "$RAW" "$OUT"
rm -f "$RAW"

identify -format 'saved=%f  size=%wx%h  mean=%[fx:mean]  stddev=%[fx:standard_deviation]  min=%[fx:minima]  max=%[fx:maxima]\n' "$OUT"
echo "file: $OUT"
echo
echo "A mean near 0 and max near 0 means the dongle sees no HDMI signal."
echo "A mean near 0 with a non-zero max may just be a mostly-black MEGA65 screen."
