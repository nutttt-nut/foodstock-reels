# FoodStock Reels

Vertical social clip generator (IG Reels / YouTube Shorts) for restaurants in **FoodStock**.
One HyperFrames template rendered per clip — full-screen source video + title card + synced subtitles — with a small Flask editor for reviewing transcripts before render.

Sibling project to `foodstock-video` (the 38-scene loop TV composition) — shares brand palette/typography, different structure (1 template + render per clip vs. one long composition).

## Stack

- **Editor:** Flask (`server.py`) + static UI (`editor.html`)
- **Render:** HyperFrames (`template/reel.html`, `make_clip.py`)
- **Storage:** Google Drive via `rclone` — no video kept permanently on the render machine, staged one clip at a time under `clips/`

## Quick start

```bash
pip install -r requirements.txt
python server.py            # editor at http://127.0.0.1:8091
```

Key env vars (see `server.py` / `foodstock-reels.service`):

| Var | Default | Purpose |
|-----|---------|---------|
| `REELS_EDITOR_PORT` | `8091` | editor server port |
| `REELS_EDITOR_TOKEN` | *(none)* | if set, required as `X-Editor-Token` / `Authorization: Bearer` header |
| `REELS_RENDER_DISABLED` | `0` | set `1` to disable render endpoint (e.g. VPS — render happens on Mac) |
| `REELS_TRANSCRIBE_DISABLED` | `0` | set `1` to disable transcribe endpoint |
| `RCLONE_REMOTE` / `RCLONE_INBOX` / `RCLONE_OUTBOX` | `gdrive` / `FoodStockReels/inbox` / `FoodStockReels/out` | Drive source of truth |

## Pipeline (per clip)

1. Drop raw vertical (9:16) clip into Drive inbox — `FoodStockReels/inbox/<restaurant>/source.mp4`
2. `npx hyperframes transcribe` → `transcript.json`
3. **Review the transcript** — Thai restaurant/menu names are transcribed wrong often, always fix by hand before rendering
4. Render → `npx hyperframes render` → uploads to `FoodStockReels/out/`

Full workflow, batch script, and folder layout: [`WORKFLOW.md`](WORKFLOW.md)
Brand palette, typography, safe areas, motion rules: [`design.md`](design.md)

## Deploy

`./deploy.sh` rsyncs the editor (excluding `clips/`, `out/`, `mock-drive/`, `.venv/`) to a VPS and installs it as a systemd service (`foodstock-reels.service`, port 8091, render/transcribe disabled remotely — those steps run locally on the Mac where `hyperframes` and Whisper live).

```bash
./deploy.sh   # DEPLOY_HOST env var overrides the default host
```
