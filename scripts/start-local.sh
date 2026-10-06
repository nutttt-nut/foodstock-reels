#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REMOTE_OVERRIDE="${RCLONE_REMOTE-}"
INBOX_OVERRIDE="${RCLONE_INBOX-}"
OUTBOX_OVERRIDE="${RCLONE_OUTBOX-}"
set -a; source "$ROOT/scripts/local.env"; set +a
if [[ -n "$REMOTE_OVERRIDE" ]]; then
  RCLONE_REMOTE="$REMOTE_OVERRIDE"
  if [[ "$REMOTE_OVERRIDE" == "gdrive" ]]; then
    [[ -n "$INBOX_OVERRIDE" ]] || RCLONE_INBOX="FoodStockReels/inbox"
    [[ -n "$OUTBOX_OVERRIDE" ]] || RCLONE_OUTBOX="FoodStockReels/out"
  fi
fi
[[ -n "$INBOX_OVERRIDE" ]] && RCLONE_INBOX="$INBOX_OVERRIDE"
[[ -n "$OUTBOX_OVERRIDE" ]] && RCLONE_OUTBOX="$OUTBOX_OVERRIDE"
command -v rclone >/dev/null || { echo "ต้องติดตั้ง rclone" >&2; exit 1; }
if ! rclone listremotes | grep -Fxq "localreels:"; then
  rclone config create localreels alias "remote=$HOME/FoodStockReels"
fi
mkdir -p "$HOME/FoodStockReels/inbox" "$HOME/FoodStockReels/out"
command -v node >/dev/null || { echo "ต้องติดตั้ง Node.js >=22" >&2; exit 1; }
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' || { echo "ต้องใช้ Node.js >=22" >&2; exit 1; }
npx hyperframes --version
command -v ffprobe >/dev/null || { echo "ต้องติดตั้ง ffprobe (ffmpeg)" >&2; exit 1; }
echo "Edit Desk: http://127.0.0.1:${REELS_EDITOR_PORT:-8091} (storage: ${RCLONE_REMOTE})"
unset REELS_EDITOR_TOKEN
REELS_EDITOR_HOST=127.0.0.1 REELS_RENDER_DISABLED=0 REELS_TRANSCRIBE_DISABLED=0 exec python3 server.py
