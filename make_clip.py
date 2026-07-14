#!/usr/bin/env python3
"""Generate one self-contained HyperFrames reel from a clip staging folder."""
import html, json, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template" / "reel.html"

def probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True)
    return round(float(out.strip()), 3)

def words_from(data):
    items = data.get("words") if isinstance(data, dict) else data
    if items: return [(float(x.get("start", 0)), float(x.get("end", x.get("start", 0))), str(x.get("word", x.get("text", "")).strip())) for x in items if x.get("text", x.get("word", "")).strip()]
    segments = data.get("segments", []) if isinstance(data, dict) else []
    result = []
    for s in segments:
        text = str(s.get("text", "")).strip()
        if text: result.append((float(s.get("start", 0)), float(s.get("end", 0)), text))
    return result

def cues(data):
    result, current = [], None
    for start, end, word in words_from(data):
        if current and (start - current["start"] > 0.55 or len(current["text"] + " " + word) > 42):
            result.append(current); current = None
        if not current: current = {"start": start, "end": end, "text": word}
        else: current["text"] += " " + word; current["end"] = end
    if current: result.append(current)
    for cue in result:
        cue["text"] = re.sub(r"\s+", " ", cue["text"]).strip()
    for previous, following in zip(result, result[1:]):
        previous["end"] = min(previous["end"], following["start"])
    return result

def trim_cues(items, trim_start, trim_end):
    kept = []
    dropped = 0
    for cue in items:
        if float(cue["end"]) <= trim_start or float(cue["start"]) >= trim_end:
            dropped += 1
            continue
        cue = dict(cue)
        cue["start"] = max(0, round(float(cue["start"]) - trim_start, 3))
        cue["end"] = min(round(trim_end - trim_start, 3), round(float(cue["end"]) - trim_start, 3))
        kept.append(cue)
    return kept, dropped


def main(folder):
    folder = Path(folder).resolve(); source = folder / "source.mp4"; meta = json.loads((folder / "clip.json").read_text())
    transcript_path = folder / "transcript.json"
    transcript = json.loads(transcript_path.read_text()) if transcript_path.exists() else {"segments": []}
    source_duration = probe(source)
    trim_start = max(0, float(meta.get("trim_start", 0) or 0))
    trim_end = float(meta.get("trim_end", source_duration) or source_duration)
    trim_start = min(trim_start, source_duration); trim_end = min(max(trim_end, trim_start), source_duration)
    duration = round(trim_end - trim_start, 3) if trim_end > trim_start else source_duration
    outro_start = round(1.2 + duration, 3); total = round(outro_start + 1.2, 3)
    cues_json = cues(transcript)
    dropped = 0
    if trim_start or trim_end != source_duration:
        cues_json, dropped = trim_cues(cues_json, trim_start, trim_end)
    # HyperFrames treats the clip folder as the project root during lint/render.
    # Keep generated clips self-contained while the source template remains reusable.
    (folder / "fonts").mkdir(exist_ok=True); (folder / "brand").mkdir(exist_ok=True)
    for asset in ("Kanit-Bold.ttf", "NotoSansThai.ttf"):
        shutil.copy2(ROOT / "fonts" / asset, folder / "fonts" / asset)
    shutil.copy2(ROOT / "brand" / "logo-cream.png", folder / "brand" / "logo-cream.png")
    text = TEMPLATE.read_text()
    replacements = {
        "__FONTS__": "fonts", "__SOURCE__": "source.mp4", "__LOGO__": "brand/logo-cream.png",
        "__RESTAURANT__": html.escape(str(meta["restaurant"])), "__SOURCE_DURATION__": f"{duration:.3f}",
        "__OUTRO_START__": f"{outro_start:.3f}", "__TOTAL_DURATION__": f"{total:.3f}",
        "__MEDIA_START__": f'data-media-start="{trim_start:.3f}"' if trim_start else "",
        "__TRANSCRIPT__": json.dumps(transcript, ensure_ascii=False), "__CUES__": json.dumps(cues_json, ensure_ascii=False),
    }
    for key, value in replacements.items(): text = text.replace(key, value)
    (folder / "index.html").write_text(text, encoding="utf-8")
    print(json.dumps({"folder": str(folder), "source_duration": source_duration, "trim_start": trim_start, "trim_end": trim_end, "duration": duration, "total_duration": total, "cues": len(cues_json), "dropped_cues": dropped}))

if __name__ == "__main__":
    if len(sys.argv) != 2: raise SystemExit("usage: python3 make_clip.py clips/<name>")
    main(sys.argv[1])
