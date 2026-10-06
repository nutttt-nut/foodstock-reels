# FoodStock Reels

Vertical social clip generator (IG Reels / YouTube Shorts) for restaurants in **FoodStock**.
One HyperFrames template rendered per clip — full-screen source video + title card + synced subtitles — with a small Flask editor for reviewing transcripts before render.

Sibling project to `foodstock-video` (the 38-scene loop TV composition) — shares brand palette/typography, different structure (1 template + render per clip vs. one long composition).

## Stack

- **Editor:** Flask (`server.py`) + static UI (`editor.html`)
- **Render:** HyperFrames (`template/reel.html`, `make_clip.py`)
- **Storage:** Mac-local `~/FoodStockReels` via the `localreels:` rclone alias; Drive remains available by setting `RCLONE_REMOTE=gdrive`

## Quick start

```bash
pip install -r requirements.txt
python server.py            # editor at http://127.0.0.1:8091
```

## Run locally (Mac — recommended)

เริ่ม Edit Desk บน Mac ด้วย storage ใน `~/FoodStockReels/inbox/` และ `~/FoodStockReels/out/`:

```bash
./scripts/start-local.sh
```

ตัว launcher สร้าง rclone alias `localreels:` ให้อัตโนมัติ และเปิด transcribe/render ที่ `http://127.0.0.1:8091` โดยไม่ใช้ token. ต้องมี rclone, Node.js 22+, HyperFrames และ `ffprobe`.

กลับไปใช้ Drive ได้ด้วย `RCLONE_REMOTE=gdrive ./scripts/start-local.sh` (ต้องตั้งค่า remote `gdrive:` ไว้ก่อน); launcher จะใช้ `FoodStockReels/inbox` และ `FoodStockReels/out` บน Drive. ปุ่ม Import จาก Drive link ใช้ได้เมื่อมี `gdrive:` เท่านั้น.

การ deploy ไป VPS เป็น legacy; Phase 1 ทำงานบน Mac.

Key env vars (see `server.py` / `foodstock-reels.service`):

| Var | Default | Purpose |
|-----|---------|---------|
| `REELS_EDITOR_PORT` | `8091` | editor server port |
| `REELS_EDITOR_TOKEN` | *(none)* | if set, required as `X-Editor-Token` / `Authorization: Bearer` header |
| `REELS_RENDER_DISABLED` | `0` | set `1` to disable render endpoint (e.g. VPS — render happens on Mac) |
| `REELS_TRANSCRIBE_DISABLED` | `0` | set `1` to disable transcribe endpoint |
| `RCLONE_REMOTE` / `RCLONE_INBOX` / `RCLONE_OUTBOX` | server defaults: `gdrive` / `FoodStockReels/inbox` / `FoodStockReels/out`; local launcher: `localreels` / `inbox` / `out` | active storage and paths |

## Pipeline (per clip)

1. Drop raw vertical (9:16) clip into `~/FoodStockReels/inbox/<restaurant>/source.mp4` (or the configured rclone inbox)
2. `npx hyperframes transcribe` → `transcript.json`
3. **Review the transcript** — Thai restaurant/menu names are transcribed wrong often, always fix by hand before rendering
4. Render → `npx hyperframes render` → writes to `~/FoodStockReels/out/` (or the configured rclone outbox)

Full workflow, batch script, and folder layout: [`WORKFLOW.md`](WORKFLOW.md)
Brand palette, typography, safe areas, motion rules: [`design.md`](design.md)

## VPS

VPS deployment is legacy; Phase 1 uses the Mac-local Edit Desk.
