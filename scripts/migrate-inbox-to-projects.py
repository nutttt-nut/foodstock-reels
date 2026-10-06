#!/usr/bin/env python3
"""Copy legacy inbox clips into local ReelEdit project folders; safe to rerun."""
import json, os, re, shutil, subprocess, sys, tempfile, threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.expanduser(os.environ.get("REELS_ROOT", "~/FoodStockReels")))
REMOTE = os.environ.get("RCLONE_REMOTE", "localreels")
INBOX = f"{REMOTE}:{os.environ.get('RCLONE_INBOX', 'inbox')}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from make_clip import cues, trim_cues, probe
project_locks, project_locks_guard = {}, threading.Lock()

def run(args): return subprocess.run(args, capture_output=True, text=True, check=True).stdout

def safe_slug(name):
    value = re.sub(r"[/\\]|\.\.", "", name)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^\w\-\u0E00-\u0E7F]", "", value, flags=re.UNICODE).strip("-_")
    return value or "project"

def project_lock(slug):
    with project_locks_guard: return project_locks.setdefault(slug, threading.Lock())

def main():
    projects = ROOT / "projects"; projects.mkdir(parents=True, exist_ok=True)
    migrated = set()
    for folder in projects.iterdir():
        try:
            p = json.loads((folder / "project.json").read_text())
            if p.get("migrated_from") is not None: migrated.add(p["migrated_from"])
        except (OSError, json.JSONDecodeError, NotADirectoryError): pass
    listing = json.loads(run(["rclone", "lsjson", "-R", "--files-only", INBOX]) or "[]")
    clips = {}
    for item in listing:
        rel = item.get("Path", ""); name, sep, filename = rel.partition("/")
        if sep: clips.setdefault(name, set()).add(filename)
    created = []
    for name, files in sorted(clips.items()):
        if "source.mp4" not in files or name in migrated: continue
        with tempfile.TemporaryDirectory(prefix="reels-migrate-") as tmp:
            tmp = Path(tmp); source = tmp / "source.mp4"
            run(["rclone", "copyto", f"{INBOX}/{name}/source.mp4", str(source)])
            try: meta = json.loads(run(["rclone", "cat", f"{INBOX}/{name}/clip.json"])) if "clip.json" in files else {}
            except (subprocess.CalledProcessError, json.JSONDecodeError): meta = {}
            try: transcript = json.loads(run(["rclone", "cat", f"{INBOX}/{name}/transcript.json"])) if "transcript.json" in files else {"segments": []}
            except (subprocess.CalledProcessError, json.JSONDecodeError): transcript = {"segments": []}
            duration = float(meta.get("source_duration") or probe(source)); start = max(0, float(meta.get("trim_start", 0) or 0)); end = float(meta.get("trim_end") or duration)
            start, end = min(start, duration), min(max(float(meta.get("trim_end") or duration), start), duration)
            subtitles, _ = trim_cues(cues(transcript), start, end)
            slug = safe_slug(name); root = slug; suffix = 2
            while (projects / slug).exists(): slug = f"{root}-{suffix}"; suffix += 1
            folder = projects / slug; (folder / "sources").mkdir(parents=True); (folder / "thumbs").mkdir()
            shutil.copy2(source, folder / "sources" / "s1.mp4")
            run(["ffmpeg", "-y", "-ss", "1", "-i", str(folder / "sources" / "s1.mp4"), "-frames:v", "1", "-q:v", "2", str(folder / "thumbs" / "s1.jpg")])
            try:
                probe_data = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(folder / "sources" / "s1.mp4")])); stream = probe_data["streams"][0]
            except (subprocess.CalledProcessError, KeyError, IndexError): stream = {"width": None, "height": None}
            now = datetime.now(timezone.utc).isoformat()
            project = {"name": name, "restaurant": meta.get("restaurant", name), "platform": "ig_reels", "target_seconds": 30, "prompt": "", "sources": [{"id": "s1", "file": "sources/s1.mp4", "duration": round(duration, 3), "width": stream.get("width"), "height": stream.get("height"), "thumb": "thumbs/s1.jpg", "original_name": "source.mp4"}], "scenes": [{"id": "sc1", "source": "s1", "in": round(start, 3), "out": round(end, 3), "locked": False, "reason": "", "subtitles": [dict(x, origin="whisper") for x in subtitles]}], "status": "ready" if meta.get("reviewed") is True else "draft", "rev": 1, "created_at": now, "updated_at": now, "migrated_from": name}
            with project_lock(slug):
                tmp_json = folder / ".project.json.tmp"
                tmp_json.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(tmp_json, folder / "project.json")
            created.append(slug)
    print(json.dumps({"created": created, "count": len(created), "skipped_migrated": sorted(migrated)}, ensure_ascii=False))

if __name__ == "__main__": main()
