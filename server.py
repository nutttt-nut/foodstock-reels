#!/usr/bin/env python3
"""Local FoodStock Reels editor: Drive-backed staging, review gate, and render jobs."""
import json, os, re, secrets, shutil, subprocess, tempfile, threading, uuid
from pathlib import Path
from functools import wraps
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent
CLIPS = ROOT / "clips"; OUT = ROOT / "out"; TEMPLATE = ROOT / "template"
REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
INBOX = f"{REMOTE}:{os.environ.get('RCLONE_INBOX', 'FoodStockReels/inbox')}"
DRIVE_OUT = f"{REMOTE}:{os.environ.get('RCLONE_OUTBOX', 'FoodStockReels/out')}"
PORT = int(os.environ.get("REELS_EDITOR_PORT", "8091"))
RENDER_DISABLED = os.environ.get("REELS_RENDER_DISABLED", "0") == "1"
TRANSCRIBE_DISABLED = os.environ.get("REELS_TRANSCRIBE_DISABLED", "0") == "1"
MAX_TRIM = float(os.environ.get("REELS_MAX_TRIM", "30"))
jobs, jobs_lock = {}, threading.Lock()
app = Flask(__name__, static_folder=None)

def authorized():
    expected = os.environ.get("REELS_EDITOR_TOKEN", "")
    if not expected: return True
    supplied = request.headers.get("X-Editor-Token", "") or request.headers.get("Authorization", "")
    if supplied.startswith("Bearer "): supplied = supplied[7:]
    return secrets.compare_digest(supplied, expected)

def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if authorized(): return fn(*args, **kwargs)
        return Response("Authentication required\n", 401, {"WWW-Authenticate": "Bearer"})
    return wrapped

def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, check=True)

def drive_lines(path, dirs=False):
    args = ["rclone", "lsf"] + (["--dirs-only"] if dirs else ["--files-only"]) + [path]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode: return []
    return [line.strip().rstrip("/") for line in result.stdout.splitlines() if line.strip()]

def drive_size(path):
    result = subprocess.run(["rclone", "lsf", "--format", "s", path], capture_output=True, text=True)
    if result.returncode or not result.stdout.strip(): return None
    try: return int(result.stdout.splitlines()[0].strip())
    except ValueError: return None

def drive_json(path):
    result = subprocess.run(["rclone", "cat", path], capture_output=True, text=True)
    if result.returncode: return {}
    try: return json.loads(result.stdout)
    except json.JSONDecodeError: return {}

def stage(name):
    folder = CLIPS / name
    remote_source = drive_size(f"{INBOX}/{name}/source.mp4")
    local_source = folder / "source.mp4"
    if not local_source.exists() or (remote_source is not None and local_source.stat().st_size != remote_source):
        if folder.exists(): shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
        run(["rclone", "copy", f"{INBOX}/{name}/", str(folder)])
    return folder

def clip_info(name):
    files = set(drive_lines(f"{INBOX}/{name}"))
    meta = drive_json(f"{INBOX}/{name}/clip.json")
    reviewed = meta.get("reviewed") is True
    out_name = f"{name}-v1-{__import__('datetime').date.today().isoformat()}.mp4"
    rendered = out_name in set(drive_lines(DRIVE_OUT))
    if rendered: status = "RENDERED"
    elif reviewed: status = "READY"
    elif "transcript.json" in files: status = "TRANSCRIBED"
    else: status = "NEW"
    return {"name": name, "restaurant": meta.get("restaurant", name), "status": status, "reviewed": reviewed, "source_duration": meta.get("source_duration"), "trim_start": meta.get("trim_start", 0), "trim_end": meta.get("trim_end"), "files": sorted(files), "output": out_name if rendered else None}

def set_job(name, fn):
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock: jobs[job_id] = {"id": job_id, "name": name, "status": "queued", "progress": 0, "log": []}
    def worker():
        try:
            update_job(job_id, status="running", progress=5)
            fn(lambda msg, progress=None: update_job(job_id, log=msg, progress=progress))
            update_job(job_id, status="done", progress=100)
        except Exception as exc: update_job(job_id, status="error", log=str(exc))
    threading.Thread(target=worker, daemon=True).start()
    return job_id

def update_job(job_id, **values):
    with jobs_lock:
        job = jobs[job_id]
        for key, value in values.items():
            if key == "log": job["log"].append(value)
            else: job[key] = value

def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

@app.get("/")
def index(): return send_from_directory(ROOT, "editor.html")

@app.get("/fonts/<path:name>")
def fonts(name): return send_from_directory(ROOT / "fonts", name)

@app.get("/brand/<path:name>")
def brand(name): return send_from_directory(ROOT / "brand", name)

@app.get("/api/clips")
@require_auth
def clips_api(): return jsonify(clips=[clip_info(name) for name in drive_lines(INBOX, dirs=True)])

@app.post("/api/clips")
@require_auth
def upload_clip():
    file = request.files.get("file"); restaurant = (request.form.get("restaurant") or "").strip()
    if not file or not file.filename or not restaurant: return jsonify(error="file and restaurant are required"), 400
    if Path(file.filename).suffix.lower() != ".mp4": return jsonify(error="only .mp4 is supported"), 400
    name = request.form.get("name") or "-".join(restaurant.split())
    if not name or any(c in name for c in "/\\"): return jsonify(error="invalid clip name"), 400
    folder = Path(tempfile.mkdtemp(prefix="foodstock-reel-")); file.save(folder / "source.mp4")
    json_write(folder / "clip.json", {"restaurant": restaurant, "reviewed": False})
    run(["rclone", "copy", str(folder), f"{INBOX}/{name}/"]); shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True, clip=clip_info(name)), 201

def folder_source(url):
    match = re.search(r"/folders/([\w-]+)", url or "")
    if not match: raise ValueError("Invalid Google Drive folder link")
    return f"gdrive,root_folder_id={match.group(1)}:"

@app.post("/api/import/scan")
@require_auth
def import_scan():
    payload = request.get_json(silent=True) or {}
    try: root = folder_source(payload.get("url")); result = subprocess.run(["rclone", "lsf", "--format", "stp", root], capture_output=True, text=True, check=True)
    except (ValueError, subprocess.CalledProcessError): return jsonify(error="เข้า Google Drive folder ไม่ได้ หรือ link ไม่ถูกต้อง"), 400
    files = []
    for line in result.stdout.splitlines():
        parts = line.split(";", 2); path = parts[-1].strip() if parts else ""
        if path.lower().endswith((".mp4", ".mov")):
            files.append({"name": path, "size": parts[0].strip() if parts else "unknown"})
    return jsonify(files=files)

@app.post("/api/import")
@require_auth
def import_clip():
    payload = request.get_json(silent=True) or {}; restaurant = str(payload.get("restaurant", "")).strip(); file_name = str(payload.get("file", "")).strip(); name = str(payload.get("name") or "-".join(restaurant.split())).strip()
    if not restaurant or not file_name or not name or any(c in name for c in "/\\"): return jsonify(error="file, restaurant, and valid name are required"), 400
    try: root = folder_source(payload.get("url"))
    except ValueError as exc: return jsonify(error=str(exc)), 400
    target = f"{INBOX}/{name}/source.mp4"
    try: run(["rclone", "copyto", "--drive-server-side-across-configs", f"{root}{file_name}", target])
    except subprocess.CalledProcessError as exc: return jsonify(error=f"copy จาก Drive ไม่สำเร็จ: {exc.stderr.strip()}"), 502
    folder = Path(tempfile.mkdtemp(prefix="foodstock-meta-")); json_write(folder / "clip.json", {"restaurant": restaurant, "reviewed": False})
    try: run(["rclone", "copyto", str(folder / "clip.json"), f"{INBOX}/{name}/clip.json"])
    finally: shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True, clip=clip_info(name)), 201

@app.post("/api/clips/<name>/transcribe")
@require_auth
def transcribe(name):
    if TRANSCRIBE_DISABLED: return jsonify(error="transcribe ทำบน Mac"), 501
    def task(log):
        folder = stage(name); log("กำลังถอดเสียง", 20); run(["npx", "hyperframes", "transcribe", str(folder / "source.mp4")]); log("ถอดเสียงเสร็จ รอ review", 80)
        meta = json.loads((folder / "clip.json").read_text()); meta["reviewed"] = False; json_write(folder / "clip.json", meta); run(["rclone", "copy", str(folder / "transcript.json"), f"{INBOX}/{name}/"])
    return jsonify(job_id=set_job(name, task)), 202

@app.put("/api/clips/<name>/settings")
@require_auth
def settings(name):
    payload = request.get_json(silent=True) or {}
    folder = stage(name); path = folder / "clip.json"
    meta = json.loads(path.read_text()) if path.exists() else {}
    if "restaurant" in payload: meta["restaurant"] = str(payload["restaurant"]).strip() or meta.get("restaurant", name)
    if "source_duration" in payload: meta["source_duration"] = max(0, float(payload["source_duration"]))
    if "trim_start" in payload or "trim_end" in payload:
        start = float(payload.get("trim_start", meta.get("trim_start", 0)) or 0)
        end = float(payload.get("trim_end", meta.get("trim_end", meta.get("source_duration", 0))) or 0)
        if start < 0 or end <= start or end - start > MAX_TRIM: return jsonify(error=f"trim ต้องยาวไม่เกิน {MAX_TRIM:g} วินาที"), 400
        if meta.get("source_duration") and end > float(meta["source_duration"]): return jsonify(error="trim_end เกินความยาว source"), 400
        meta["trim_start"], meta["trim_end"] = round(start, 3), round(end, 3)
    json_write(path, meta); run(["rclone", "copy", str(path), f"{INBOX}/{name}/"])
    return jsonify(ok=True, settings={k: meta[k] for k in ("restaurant", "source_duration", "trim_start", "trim_end") if k in meta})

@app.route("/api/clips/<name>/transcript", methods=["GET", "PUT"])
@require_auth
def transcript(name):
    folder = stage(name); path = folder / "transcript.json"
    if request.method == "GET":
        if not path.exists(): return jsonify(error="transcript not found"), 404
        return jsonify(json.loads(path.read_text()))
    payload = request.get_json(silent=True)
    if isinstance(payload, list): payload = {"segments": payload}
    if not isinstance(payload, dict): return jsonify(error="JSON object or segments array required"), 400
    json_write(path, payload); run(["rclone", "copy", str(path), f"{INBOX}/{name}/"])
    return jsonify(ok=True)

@app.post("/api/clips/<name>/review")
@require_auth
def review(name):
    folder = stage(name); meta = json.loads((folder / "clip.json").read_text()); meta["reviewed"] = True; json_write(folder / "clip.json", meta)
    run(["rclone", "copy", str(folder / "clip.json"), f"{INBOX}/{name}/"])
    return jsonify(ok=True, clip=clip_info(name))

@app.post("/api/clips/<name>/render")
@require_auth
def render(name):
    if RENDER_DISABLED: return jsonify(error="render ทำบน Mac ผ่าน run_batch"), 501
    info = clip_info(name)
    if not info["reviewed"]: return jsonify(error="Review transcript before rendering"), 409
    def task(log):
        folder = stage(name); log("กำลังสร้าง reel", 15); run(["python3", "make_clip.py", str(folder)]); log("กำลัง render", 35)
        output = OUT / info["output"] if info["output"] else OUT / f"{name}-v1-{__import__('datetime').date.today().isoformat()}.mp4"
        run(["npx", "hyperframes", "render", str(folder), "--output", str(output)]); log("กำลังอัปโหลด Drive", 85); run(["rclone", "copy", str(output), DRIVE_OUT]); shutil.rmtree(folder, ignore_errors=True); output.unlink(missing_ok=True)
    return jsonify(job_id=set_job(name, task)), 202

@app.get("/api/status/<job_id>")
@require_auth
def status(job_id):
    with jobs_lock: job = jobs.get(job_id)
    return (jsonify(job) if job else (jsonify(error="job not found"), 404))

@app.get("/api/clips/<name>/source")
@require_auth
def source(name): return send_file(stage(name) / "source.mp4", mimetype="video/mp4")

@app.get("/api/clips/<name>/output")
@require_auth
def output(name):
    info = clip_info(name)
    if not info["output"]: return jsonify(error="output not found"), 404
    target = OUT / info["output"]
    if not target.exists(): run(["rclone", "copyto", f"{DRIVE_OUT}/{info['output']}", str(target)])
    return send_file(target, mimetype="video/mp4")

if __name__ == "__main__": app.run(host=os.environ.get("REELS_EDITOR_HOST", "127.0.0.1"), port=PORT)
