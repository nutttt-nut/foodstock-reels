#!/usr/bin/env bash
# Run against an already running local server using the same REELS_ROOT.
# REELS_EDITOR_PID enables sampled server RSS proof during the 600 MiB export.
set -euo pipefail
: "${REELS_ROOT:?Set REELS_ROOT to the running server storage directory}"
command -v ffmpeg >/dev/null || { echo 'FAIL prerequisites: ffmpeg missing'; exit 1; }
command -v ffprobe >/dev/null || { echo 'FAIL prerequisites: ffprobe missing'; exit 1; }
exec python3 "$(dirname "$0")/check-2b.py"
