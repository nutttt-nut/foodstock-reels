#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
REMOTE="${RCLONE_REMOTE:-}"; INBOX="${RCLONE_INBOX:-FoodStockReels/inbox}"; OUTBOX="${RCLONE_OUTBOX:-FoodStockReels/out}"
DRY_RUN=0; [[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
if [[ -z "$REMOTE" ]] && command -v rclone >/dev/null 2>&1; then REMOTE="$(rclone listremotes | sed -n '1p' | tr -d ':')"; fi
if [[ -n "$REMOTE" ]]; then SRC="$REMOTE:$INBOX"; DEST="$REMOTE:$OUTBOX"; else SRC="${LOCAL_DRIVE:-$ROOT/mock-drive}/inbox"; DEST="${LOCAL_DRIVE:-$ROOT/mock-drive}/out"; mkdir -p "$SRC" "$DEST"; echo "Using local mock drive: $SRC"; fi
mkdir -p clips out
if [[ -n "$REMOTE" ]]; then
  items=()
  while IFS= read -r item; do items+=("$item"); done < <(rclone lsf --dirs-only "$SRC")
else
  items=("$SRC"/*)
fi
for item in "${items[@]}"; do
  if [[ -n "$REMOTE" ]]; then name="${item%/}"; else [[ -d "$item" ]] || continue; name="$(basename "$item")"; fi
  output_name="${name}-v1-$(date +%Y-%m-%d).mp4"; output="out/$output_name"
  if [[ -n "$REMOTE" ]]; then
    rclone lsf --files-only "$DEST" | grep -Fxq "$output_name" && { echo "skip $name (output exists)"; continue; }
  else
    [[ -f "$DEST/$name.mp4" || -f "$DEST/$output_name" ]] && { echo "skip $name (output exists)"; continue; }
  fi
  echo "process $name"; [[ "$DRY_RUN" == 1 ]] && continue
  rm -rf "clips/$name"; mkdir -p "clips/$name"; if [[ -n "$REMOTE" ]]; then rclone copy "$SRC/$name/" "clips/$name/"; else cp -R "$SRC/$name/." "clips/$name/"; fi
  if [[ ! -f "clips/$name/transcript.json" ]]; then echo "WAIT: transcript required for $name"; rm -rf "clips/$name"; continue; fi
  if ! python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("reviewed") is True else 1)' "clips/$name/clip.json"; then echo "WAIT: transcript review required for $name"; rm -rf "clips/$name"; continue; fi
  python3 make_clip.py "clips/$name"; npx hyperframes render "clips/$name" --output "$output"
  if [[ -n "$REMOTE" ]]; then rclone copy "$output" "$DEST"; else cp "$output" "$DEST/${name}.mp4"; fi
  rm -rf "clips/$name" "$output"
done
