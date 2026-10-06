#!/usr/bin/env python3
"""Local FoodStock Reels editor: Drive-backed staging, review gate, and render jobs."""
import json, os, re, secrets, shutil, subprocess, tempfile, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent
CLIPS = ROOT / "clips"; OUT = ROOT / "out"; TEMPLATE = ROOT / "template"
REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
REELS_ROOT = Path(os.path.expanduser(os.environ.get("REELS_ROOT", "~/FoodStockReels")))
PROJECTS = REELS_ROOT / "projects"; TRASH = REELS_ROOT / ".trash"
INBOX = f"{REMOTE}:{os.environ.get('RCLONE_INBOX', 'FoodStockReels/inbox')}"
DRIVE_OUT = f"{REMOTE}:{os.environ.get('RCLONE_OUTBOX', 'FoodStockReels/out')}"
PORT = int(os.environ.get("REELS_EDITOR_PORT", "8091"))
RENDER_DISABLED = os.environ.get("REELS_RENDER_DISABLED", "0") == "1"
TRANSCRIBE_DISABLED = os.environ.get("REELS_TRANSCRIBE_DISABLED", "0") == "1"
MAX_TRIM = float(os.environ.get("REELS_MAX_TRIM", "30"))
jobs, jobs_lock = {}, threading.Lock()
stage_locks, stage_locks_lock = {}, threading.Lock()
project_locks, project_locks_lock = {}, threading.Lock()
project_create_lock = threading.Lock()
project_rendering, project_rendering_lock = set(), threading.Lock()
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

def has_remote(name):
    result = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True)
    return result.returncode == 0 and f"{name}:" in result.stdout.splitlines()

def remote_json(name, filename):
    result = subprocess.run(["rclone", "cat", f"{INBOX}/{name}/{filename}"], capture_output=True, text=True)
    if result.returncode: return None
    try: return json.loads(result.stdout)
    except json.JSONDecodeError: return None

def push_json(name, filename, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2); f.write("\n"); path = f.name
    try: run(["rclone", "copyto", path, f"{INBOX}/{name}/{filename}"])
    finally: Path(path).unlink(missing_ok=True)

def refresh_staged_json(name, folder):
    for filename in ("clip.json", "transcript.json"):
        run(["rclone", "copyto", f"{INBOX}/{name}/{filename}", str(folder / filename)])

def list_clip_outputs():
    result = subprocess.run(["rclone", "lsjson", "--files-only", DRIVE_OUT], capture_output=True, text=True)
    return json.loads(result.stdout or "[]") if result.returncode == 0 else []

def rendered_info(name, include_next=False):
    files = json.loads(run(["rclone", "lsjson", "-R", "--files-only", INBOX]).stdout or "[]")
    clip_files = {}
    for item in files:
        path = item.get("Path", ""); clip, _, filename = path.partition("/")
        if clip == name: clip_files[filename] = item
    meta = remote_json(name, "clip.json") or {}
    outputs = list_clip_outputs()
    info = clip_info(name, clip_files, meta, outputs)
    versions = [int(m.group(1)) for item in outputs if (m := re.fullmatch(re.escape(name) + r"-v(\d+)-\d{4}-\d{2}-\d{2}\.mp4", item.get("Name", "")))]
    if include_next: info["_next_output"] = f"{name}-v{max(versions, default=0)+1}-{__import__('datetime').date.today().isoformat()}.mp4"
    return info

def stage(name):
    with stage_locks_lock: lock = stage_locks.setdefault(name, threading.Lock())
    with lock:
        folder = CLIPS / name
        remote_source = drive_size(f"{INBOX}/{name}/source.mp4")
        local_source = folder / "source.mp4"
        if not local_source.exists() or (remote_source is not None and local_source.stat().st_size != remote_source):
            if folder.exists(): shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)
            run(["rclone", "copy", f"{INBOX}/{name}/", str(folder)])
        return folder

def clip_info(name, files, meta, outputs):
    reviewed = meta.get("reviewed") is True
    matches = sorted(((int(m.group(1)), item["Name"], item.get("ModTime", "")) for item in outputs
                      if (m := re.fullmatch(re.escape(name) + r"-v(\d+)-\d{4}-\d{2}-\d{2}\.mp4", item.get("Name", "")))), reverse=True)
    out_name, out_time = (matches[0][1], matches[0][2]) if matches else (None, "")
    input_times = [item.get("ModTime", "") for item in files.values() if item.get("Name") in ("clip.json", "transcript.json")]
    rendered = bool(out_name and out_time and len(input_times) == 2 and all(t and out_time > t for t in input_times))
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

def project_lock(slug):
    with project_locks_lock: return project_locks.setdefault(slug, threading.Lock())

def project_path(slug):
    if not re.fullmatch(r"[\w\-\u0E00-\u0E7F]+", slug, re.UNICODE): return None
    path = PROJECTS / slug
    return path if path.parent.resolve() == PROJECTS.resolve() and (not path.exists() or path.resolve().parent == PROJECTS.resolve()) else None

def read_project(slug):
    folder = project_path(slug)
    if not folder or not (folder / "project.json").is_file(): return None
    try: return json.loads((folder / "project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None

def write_project(folder, project):
    fd, tmp = tempfile.mkstemp(prefix=".project-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, folder / "project.json")
    finally:
        Path(tmp).unlink(missing_ok=True)

def project_error(project):
    source_map = {x.get("id"): x for x in project.get("sources", [])}
    for i, scene in enumerate(project.get("scenes", [])):
        field = f"scenes[{i}]"
        source = source_map.get(scene.get("source"))
        if not source: return f"{field}.source: source does not exist"
        try: start, end, duration = float(scene["in"]), float(scene["out"]), float(source["duration"])
        except (KeyError, TypeError, ValueError): return f"{field}.in/out: invalid time or source duration"
        if not (0 <= start < end <= duration): return f"{field}.in/out: require 0 ≤ in < out ≤ duration"
        for j, subtitle in enumerate(scene.get("subtitles", [])):
            try: a, b = float(subtitle["start"]), float(subtitle["end"])
            except (KeyError, TypeError, ValueError): return f"{field}.subtitles[{j}].start/end: invalid time"
            if not (0 <= a <= b <= end - start): return f"{field}.subtitles[{j}]: times must be inside scene length"
    return None

def project_summary(slug, p):
    sources = p.get("sources", []); thumb = sources[0].get("thumb") if sources else None
    thumb_name = Path(thumb).name if thumb else None
    return {"slug": slug, "name": p.get("name", slug), "restaurant": p.get("restaurant", ""), "status": p.get("status", "draft"), "sources": len(sources), "scenes": len(p.get("scenes", [])), "updated_at": p.get("updated_at"), "thumb": f"/api/projects/{slug}/thumbs/{thumb_name}" if thumb else None}

def trash_move(path, slug):
    TRASH.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = TRASH / f"{slug}-{stamp}"; suffix = 1
    while target.exists(): target = TRASH / f"{slug}-{stamp}-{suffix}"; suffix += 1
    os.rename(path, target)
    return str(target)

@app.get("/")
def index(): return send_from_directory(ROOT, "editor.html")

@app.get("/api/config")
def config(): return jsonify(drive_import=has_remote("gdrive"), remote=REMOTE)

@app.get("/api/projects")
@require_auth
def projects_api():
    PROJECTS.mkdir(parents=True, exist_ok=True)
    result = []
    for folder in sorted(PROJECTS.iterdir()):
        if folder.is_dir():
            p = read_project(folder.name)
            if p: result.append(project_summary(folder.name, p))
    return jsonify(projects=result)

@app.post("/api/projects")
@require_auth
def create_project():
    data = request.get_json(silent=True) or {}; name = str(data.get("name", "")).strip(); restaurant = str(data.get("restaurant", "")).strip()
    if not name or not restaurant: return jsonify(error="name and restaurant are required"), 400
    clean = re.sub(r"[/\\]|\.\.", "", name); clean = re.sub(r"\s+", "", clean); clean = re.sub(r"[^\w\-\u0E00-\u0E7F]", "", clean, flags=re.UNICODE).strip("-_")
    if not clean: clean = "project"
    PROJECTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    try: target = float(data.get("target_seconds", 30))
    except (TypeError, ValueError): return jsonify(error="target_seconds must be numeric"), 400
    if target <= 0: return jsonify(error="target_seconds must be greater than zero"), 400
    with project_create_lock:
        slug = clean; n = 2
        while (PROJECTS / slug).exists(): slug = f"{clean}-{n}"; n += 1
        with project_lock(slug):
            folder = PROJECTS / slug; folder.mkdir(); (folder / "sources").mkdir(); (folder / "thumbs").mkdir()
            p = {"name": name, "restaurant": restaurant, "platform": "ig_reels", "target_seconds": target, "prompt": "", "sources": [], "scenes": [], "status": "draft", "rev": 1, "created_at": now, "updated_at": now}
            write_project(folder, p)
    return jsonify(slug=slug, project=p), 201

@app.get("/api/projects/<slug>")
@require_auth
def get_project(slug):
    p = read_project(slug)
    return jsonify(p) if p else (jsonify(error="project not found"), 404)

@app.put("/api/projects/<slug>")
@require_auth
def put_project(slug):
    folder = project_path(slug); payload = request.get_json(silent=True)
    if not folder or not isinstance(payload, dict): return jsonify(error="project object required"), 400
    with project_lock(slug):
        current = read_project(slug)
        if current is None: return jsonify(error="project not found"), 404
        if payload.get("rev") != current.get("rev"): return jsonify(error="project revision conflict", current_rev=current["rev"]), 409
        saved = dict(payload); saved["sources"] = current.get("sources", []); saved["rev"] = current["rev"] + 1
        saved["created_at"] = current.get("created_at"); saved["updated_at"] = datetime.now(timezone.utc).isoformat()
        error = project_error(saved)
        if error: return jsonify(error=error), 400
        write_project(folder, saved)
    return jsonify(project=saved, rev=saved["rev"])

@app.delete("/api/projects/<slug>")
@require_auth
def delete_project(slug):
    folder = project_path(slug)
    if not folder or not folder.is_dir(): return jsonify(error="project not found"), 404
    with project_rendering_lock:
        if slug in project_rendering: return jsonify(error="project render is running"), 409
    with project_lock(slug):
        try: target = trash_move(folder, slug)
        except OSError as exc: return jsonify(error=f"could not move project to trash: {exc}"), 500
    return jsonify(trash_path=target)

@app.post("/api/projects/<slug>/sources")
@require_auth
def add_project_sources(slug):
    folder = project_path(slug); files = request.files.getlist("files") or request.files.getlist("file")
    if not folder or not files: return jsonify(error="project and video files are required"), 400
    with project_lock(slug):
        p = read_project(slug)
        if p is None: return jsonify(error="project not found"), 404
        staged = []; created_paths = []
        try:
            for upload in files:
                original = Path(upload.filename or "").name
                ext = Path(original).suffix.lower()
                if ext not in (".mp4", ".mov"): raise ValueError(f"{original}: only .mp4/.mov are supported")
                nums = [int(re.search(r"\d+", s["id"]).group()) for s in p["sources"] if re.fullmatch(r"s\d+", s.get("id", ""))]
                nums += [int(re.search(r"\d+", x["id"]).group()) for x in staged]
                sid = f"s{max(nums, default=0)+1}"
                dst = folder / "sources" / f"{sid}{ext}"; upload.save(dst); created_paths.append(dst)
                probe = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration:stream=width,height", "-of", "json", str(dst)]).stdout)
                stream = next(x for x in probe.get("streams", []) if x.get("width") and x.get("height"))
                duration = float(probe["format"]["duration"]); thumb_rel = f"thumbs/{sid}.jpg"
                thumb_path = folder / thumb_rel; run(["ffmpeg", "-y", "-ss", "1", "-i", str(dst), "-frames:v", "1", "-q:v", "2", str(thumb_path)]); created_paths.append(thumb_path)
                staged.append({"id": sid, "file": f"sources/{sid}{ext}", "duration": round(duration, 3), "width": int(stream["width"]), "height": int(stream["height"]), "thumb": thumb_rel, "original_name": original})
            p["sources"].extend(staged); p["rev"] += 1; p["updated_at"] = datetime.now(timezone.utc).isoformat(); write_project(folder, p)
        except (ValueError, KeyError, StopIteration, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            for path in created_paths:
                if path.exists(): trash_move(path, f"{slug}-failed-upload")
            if isinstance(exc, ValueError): return jsonify(error=str(exc)), 400
            return jsonify(error=f"could not process source: {exc}"), 422
    return jsonify(sources=p["sources"], rev=p["rev"]), 201

@app.get("/api/projects/<slug>/thumbs/<thumb>")
@require_auth
def project_thumb(slug, thumb):
    if not re.fullmatch(r"s\d+\.jpg", thumb): return jsonify(error="thumb not found"), 404
    folder = project_path(slug); path = folder / "thumbs" / thumb if folder else None
    return send_file(path, mimetype="image/jpeg") if path and path.is_file() else (jsonify(error="thumb not found"), 404)

@app.get("/api/projects/<slug>/sources/<sid>")
@require_auth
def project_source(slug, sid):
    p = read_project(slug); source = next((x for x in p.get("sources", []) if x.get("id") == sid), None) if p else None
    if not source: return jsonify(error="source not found"), 404
    folder = project_path(slug); path = folder / source["file"]
    if not path.resolve().is_relative_to(folder.resolve()): return jsonify(error="source not found"), 404
    return send_file(path, mimetype="video/quicktime" if path.suffix == ".mov" else "video/mp4", conditional=True)

@app.delete("/api/projects/<slug>/sources/<sid>")
@require_auth
def delete_project_source(slug, sid):
    folder = project_path(slug)
    if not folder: return jsonify(error="project not found"), 404
    with project_lock(slug):
        p = read_project(slug)
        if p is None: return jsonify(error="project not found"), 404
        used = [x for x in p.get("scenes", []) if x.get("source") == sid]
        if used: return jsonify(error="source is used by scenes", scenes=used), 409
        src = next((x for x in p["sources"] if x.get("id") == sid), None)
        if not src: return jsonify(error="source not found"), 404
        try:
            trash_move(folder / src["file"], f"{slug}-{sid}")
            thumb_path = folder / src["thumb"]
            if thumb_path.exists(): trash_move(thumb_path, f"{slug}-{sid}-thumb")
        except OSError as exc: return jsonify(error=f"could not move source to trash: {exc}"), 500
        p["sources"].remove(src); p["rev"] += 1; p["updated_at"] = datetime.now(timezone.utc).isoformat(); write_project(folder, p)
    return jsonify(sources=p["sources"], rev=p["rev"])

@app.get("/fonts/<path:name>")
def fonts(name): return send_from_directory(ROOT / "fonts", name)

@app.get("/brand/<path:name>")
def brand(name): return send_from_directory(ROOT / "brand", name)

@app.get("/api/clips")
@require_auth
def clips_api():
    listing = run(["rclone", "lsjson", "-R", "--files-only", INBOX]).stdout
    outputs = json.loads(run(["rclone", "lsjson", "--files-only", DRIVE_OUT]).stdout or "[]")
    by_clip = {}
    for item in json.loads(listing or "[]"):
        path = item.get("Path", ""); name, _, filename = path.partition("/")
        by_clip.setdefault(name, {})[filename] = item
    with tempfile.TemporaryDirectory(prefix="reels-meta-") as tmp:
        run(["rclone", "copy", INBOX, tmp, "--include", "*/clip.json"])
        clips = []
        for name, files in sorted(by_clip.items()):
            try: meta = json.loads((Path(tmp) / name / "clip.json").read_text())
            except (OSError, json.JSONDecodeError): meta = {}
            clips.append(clip_info(name, files, meta, outputs))
    return jsonify(clips=clips)

@app.post("/api/clips")
@require_auth
def upload_clip():
    file = request.files.get("file"); restaurant = (request.form.get("restaurant") or "").strip()
    if not file or not file.filename or not restaurant: return jsonify(error="file and restaurant are required"), 400
    if Path(file.filename).suffix.lower() != ".mp4": return jsonify(error="only .mp4 is supported"), 400
    name = request.form.get("name") or "-".join(restaurant.split())
    if not name or any(c in name for c in "/\\"): return jsonify(error="invalid clip name"), 400
    if name in drive_lines(INBOX, dirs=True): return jsonify(error="ชื่อคลิปนี้มีอยู่แล้ว"), 409
    folder = Path(tempfile.mkdtemp(prefix="foodstock-reel-")); file.save(folder / "source.mp4")
    json_write(folder / "clip.json", {"restaurant": restaurant, "reviewed": False})
    run(["rclone", "copy", str(folder), f"{INBOX}/{name}/"]); shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True, clip=rendered_info(name)), 201

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
    if name in drive_lines(INBOX, dirs=True): return jsonify(error="ชื่อคลิปนี้มีอยู่แล้ว"), 409
    if not has_remote("gdrive"): return jsonify(error="ต้องตั้ง rclone remote gdrive ก่อน (ดู #121)"), 503
    target = f"{INBOX}/{name}/source.mp4"
    try: run(["rclone", "copyto", f"{root}{file_name}", target])
    except subprocess.CalledProcessError as exc: return jsonify(error=f"copy จาก Drive ไม่สำเร็จ: {exc.stderr.strip()}"), 502
    folder = Path(tempfile.mkdtemp(prefix="foodstock-meta-")); json_write(folder / "clip.json", {"restaurant": restaurant, "reviewed": False})
    try: run(["rclone", "copyto", str(folder / "clip.json"), f"{INBOX}/{name}/clip.json"])
    finally: shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True, clip=rendered_info(name)), 201

@app.post("/api/clips/<name>/transcribe")
@require_auth
def transcribe(name):
    if TRANSCRIBE_DISABLED: return jsonify(error="transcribe ทำบน Mac"), 501
    def task(log):
        folder = stage(name); log("กำลังถอดเสียง", 20); run(["npx", "hyperframes", "transcribe", str(folder / "source.mp4")]); log("ถอดเสียงเสร็จ รอ review", 80)
        meta = remote_json(name, "clip.json") or {}; meta["reviewed"] = False
        run(["rclone", "copy", str(folder / "transcript.json"), f"{INBOX}/{name}/"]); push_json(name, "clip.json", meta)
    return jsonify(job_id=set_job(name, task)), 202

@app.put("/api/clips/<name>/settings")
@require_auth
def settings(name):
    payload = request.get_json(silent=True) or {}
    meta = remote_json(name, "clip.json") or {}
    if "restaurant" in payload: meta["restaurant"] = str(payload["restaurant"]).strip() or meta.get("restaurant", name)
    if "source_duration" in payload: meta["source_duration"] = max(0, float(payload["source_duration"]))
    if "trim_start" in payload or "trim_end" in payload:
        start = float(payload.get("trim_start", meta.get("trim_start", 0)) or 0)
        end = float(payload.get("trim_end", meta.get("trim_end", meta.get("source_duration", 0))) or 0)
        if start < 0 or end <= start or end - start > MAX_TRIM: return jsonify(error=f"trim ต้องยาวไม่เกิน {MAX_TRIM:g} วินาที"), 400
        if meta.get("source_duration") and end > float(meta["source_duration"]): return jsonify(error="trim_end เกินความยาว source"), 400
        meta["trim_start"], meta["trim_end"] = round(start, 3), round(end, 3)
    push_json(name, "clip.json", meta)
    return jsonify(ok=True, settings={k: meta[k] for k in ("restaurant", "source_duration", "trim_start", "trim_end") if k in meta})

@app.route("/api/clips/<name>/transcript", methods=["GET", "PUT"])
@require_auth
def transcript(name):
    if request.method == "GET":
        payload = remote_json(name, "transcript.json")
        return (jsonify(payload) if payload is not None else (jsonify(error="transcript not found"), 404))
    payload = request.get_json(silent=True)
    if isinstance(payload, list): payload = {"segments": payload}
    if not isinstance(payload, dict): return jsonify(error="JSON object or segments array required"), 400
    push_json(name, "transcript.json", payload)
    meta = remote_json(name, "clip.json") or {}; meta["reviewed"] = False; push_json(name, "clip.json", meta)
    return jsonify(ok=True)

@app.post("/api/clips/<name>/review")
@require_auth
def review(name):
    meta = remote_json(name, "clip.json") or {}; meta["reviewed"] = True; push_json(name, "clip.json", meta)
    return jsonify(ok=True, clip=rendered_info(name))

@app.post("/api/clips/<name>/render")
@require_auth
def render(name):
    if RENDER_DISABLED: return jsonify(error="render ทำบน Mac ผ่าน run_batch"), 501
    info = rendered_info(name, include_next=True)
    if not info["reviewed"]: return jsonify(error="Review transcript before rendering"), 409
    def task(log):
        folder = stage(name); refresh_staged_json(name, folder); log("กำลังสร้าง reel", 15); run(["python3", "make_clip.py", str(folder)]); log("กำลัง render", 35)
        output = OUT / info["_next_output"]
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
    info = rendered_info(name)
    if not info["output"]: return jsonify(error="output not found"), 404
    target = OUT / info["output"]
    if not target.exists(): run(["rclone", "copyto", f"{DRIVE_OUT}/{info['output']}", str(target)])
    return send_file(target, mimetype="video/mp4")

if __name__ == "__main__": app.run(host=os.environ.get("REELS_EDITOR_HOST", "127.0.0.1"), port=PORT)
