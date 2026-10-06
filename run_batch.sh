#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
REMOTE_OVERRIDE="${RCLONE_REMOTE-}"; INBOX_OVERRIDE="${RCLONE_INBOX-}"; OUTBOX_OVERRIDE="${RCLONE_OUTBOX-}"
[[ -f "$ROOT/scripts/local.env" ]] && source "$ROOT/scripts/local.env"
if [[ -n "$REMOTE_OVERRIDE" ]]; then
  RCLONE_REMOTE="$REMOTE_OVERRIDE"
  if [[ "$REMOTE_OVERRIDE" == "gdrive" ]]; then
    [[ -n "$INBOX_OVERRIDE" ]] || RCLONE_INBOX="FoodStockReels/inbox"
    [[ -n "$OUTBOX_OVERRIDE" ]] || RCLONE_OUTBOX="FoodStockReels/out"
  fi
fi
[[ -n "$INBOX_OVERRIDE" ]] && RCLONE_INBOX="$INBOX_OVERRIDE"
[[ -n "$OUTBOX_OVERRIDE" ]] && RCLONE_OUTBOX="$OUTBOX_OVERRIDE"
REMOTE="${RCLONE_REMOTE:-}"; INBOX="${RCLONE_INBOX:-FoodStockReels/inbox}"; OUTBOX="${RCLONE_OUTBOX:-FoodStockReels/out}"
DRY_RUN=0; [[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
[[ -n "$REMOTE" ]] || { echo "RCLONE_REMOTE is empty; set it or restore scripts/local.env" >&2; exit 1; }
command -v rclone >/dev/null || { echo "rclone is required" >&2; exit 1; }
SRC="$REMOTE:$INBOX"; DEST="$REMOTE:$OUTBOX"
mkdir -p clips out
items=()
while IFS= read -r item; do items+=("$item"); done < <(rclone lsf --dirs-only "$SRC")
for item in "${items[@]}"; do
  name="${item%/}"
  output_meta="$(mktemp)"; input_meta="$(mktemp)"
  rclone lsjson --files-only "$DEST" > "$output_meta"
  rclone lsjson -R --files-only "$SRC/$name" > "$input_meta"
  read -r is_rendered max_version < <(python3 -c 'import json,re,sys; name=sys.argv[1]; ins=json.load(open(sys.argv[2])); outs=json.load(open(sys.argv[3])); pat=re.compile(re.escape(name)+r"-v(\d+)-\d{4}-\d{2}-\d{2}\.mp4$"); matches=[(int(m.group(1)),x) for x in outs if (m:=pat.fullmatch(x.get("Name","")))]; maxv=max((n for n,_ in matches),default=0); newest=max(matches,key=lambda p:p[0])[1] if matches else {}; times={x.get("Name"):x.get("ModTime","") for x in ins}; fresh=bool(newest.get("ModTime") and all(times.get(f) and newest["ModTime"]>times[f] for f in ("clip.json","transcript.json"))); print(int(fresh),maxv)' "$name" "$input_meta" "$output_meta")
  rm -f "$input_meta" "$output_meta"
  [[ "$is_rendered" == 1 ]] && { echo "skip $name (rendered)"; continue; }
  output_name="${name}-v$((max_version+1))-$(date +%Y-%m-%d).mp4"; output="out/$output_name"
  echo "process $name"; [[ "$DRY_RUN" == 1 ]] && continue
  rm -rf "clips/$name"; mkdir -p "clips/$name"; rclone copy "$SRC/$name/" "clips/$name/"
  if [[ ! -f "clips/$name/transcript.json" ]]; then echo "WAIT: transcript required for $name"; rm -rf "clips/$name"; continue; fi
  if ! python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("reviewed") is True else 1)' "clips/$name/clip.json"; then echo "WAIT: transcript review required for $name"; rm -rf "clips/$name"; continue; fi
  python3 make_clip.py "clips/$name"; npx hyperframes render "clips/$name" --output "$output"
  rclone copy "$output" "$DEST"
  rm -rf "clips/$name" "$output"
done
