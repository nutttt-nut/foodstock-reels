#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
HOST="${DEPLOY_HOST:-152.42.197.67}"
REMOTE_DIR="/opt/foodstock-reels"

rsync -az --delete \
  --exclude 'clips/' --exclude 'out/' --exclude 'mock-drive/' --exclude '.venv/' --exclude '__pycache__/' \
  "$ROOT/" "root@$HOST:$REMOTE_DIR/"
ssh "root@$HOST" "mkdir -p $REMOTE_DIR/clips $REMOTE_DIR/out && python3 -m venv $REMOTE_DIR/.venv && $REMOTE_DIR/.venv/bin/pip install -q -r $REMOTE_DIR/requirements.txt && if [ ! -f /etc/foodstock-reels.env ]; then umask 077; printf 'REELS_EDITOR_TOKEN=%s\\nREELS_RENDER_DISABLED=1\\nREELS_TRANSCRIBE_DISABLED=1\\n' \"\$(openssl rand -hex 32)\" > /etc/foodstock-reels.env; fi && install -m 644 $REMOTE_DIR/foodstock-reels.service /etc/systemd/system/foodstock-reels.service && systemctl daemon-reload && rm -rf /opt/whisper.cpp /root/.cache/hyperframes/whisper /root/.npm/_npx/*/node_modules/hyperframes 2>/dev/null || true"
echo "synced FoodStock Reels to $HOST; service not started (manual approval required)"
