#!/usr/bin/env python3
"""app.py — Frontier's local control room.

Start it, and a browser opens on http://127.0.0.1:7860. Pick a channel, type a
title, press Create. One job runs at a time — rendering is heavy local work and
two at once just makes both slower.

    python app.py

The interface itself is `ui.html`, sitting next to this file and read fresh on
every request. Edit it, reload the page, see the change — no restart, no build
step, no Python to touch.
"""

import hashlib
import io
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

import make_video as mv
import motion
import features
import styles

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
UI = HERE / "ui.html"
# another app on 7860? FRONTIER_PORT=7870 python app.py
PORT = int(os.environ.get("FRONTIER_PORT") or 7860)
SAFE = re.compile(r"^[\w.\-]+$")          # filename guard for the file routes

app = Flask(__name__)

JOBS: dict = {}            # slug -> {lines, state, error, done, title, steps}
RUNNING = {"slug": None}   # the job currently rendering
QUEUE: list = []           # pending specs, processed in order
HISTORY: list = []         # finished slugs, oldest first
LOCK = threading.Lock()

VALID_STEPS = {"script", "voiceover", "pexels", "images", "video", "thumbnail"}

# Channels are files, not code — read them in before anything can be rendered.
LOADED = styles.apply_to_engine(mv, motion)


# ── the queue ──────────────────────────────────────────────────────────────

def _run_spec(spec):
    slug = spec["slug"]
    job = JOBS.setdefault(slug, {})
    job.update(lines=job.get("lines", []), state="running", error=None, done=False,
               title=spec["title"], steps=spec["steps"], started=time.time(),
               minutes=spec.get("minutes", 20), style=spec.get("style", ""))

    def sink(line):
        job["lines"].append(line)

    try:
        mv.run_custom(spec["title"], spec["minutes"], steps=spec["steps"],
                      script_text=spec["script_text"], force=spec["force"],
                      burn_subs=spec["burn_subs"], sink=sink, style=spec["style"],
                      thumb_count=spec["thumb_count"],
                      use_motion=spec.get("use_motion", True),
                      byo_audio=spec.get("byo_audio", ""),
                      mix=spec.get("mix") or (), meta=spec.get("meta"),
                      character=spec.get("character", ""),
                      character_image=spec.get("character_image", ""),
                      maps=spec.get("maps", True), headlines=spec.get("headlines", True),
                      spotlight=spec.get("spotlight", True), objects=spec.get("objects", True),
                      depth=spec.get("depth", True),
                      features=spec.get("features"), extra=spec.get("extra", ""))
        job["state"] = "done"
    except BaseException as e:                                   # noqa: BLE001
        job["error"] = str(e) or e.__class__.__name__
        job["state"] = "error"
        job["lines"].append(f"ERROR: {job['error']}")
    finally:
        job["done"] = True
        with LOCK:
            HISTORY.append(slug)
            RUNNING["slug"] = None


def _dispatcher():
    while True:
        spec = None
        with LOCK:
            if RUNNING["slug"] is None and QUEUE:
                spec = QUEUE.pop(0)
                RUNNING["slug"] = spec["slug"]
        if spec is None:
            time.sleep(0.4)
            continue
        _run_spec(spec)


_DISPATCHER = None


def _ensure_dispatcher():
    global _DISPATCHER
    if _DISPATCHER is None or not _DISPATCHER.is_alive():
        _DISPATCHER = threading.Thread(target=_dispatcher, daemon=True)
        _DISPATCHER.start()


# ── API ────────────────────────────────────────────────────────────────────

@app.post("/api/run")
def api_run():
    d = request.get_json(force=True)
    titles = [t.strip() for t in (d.get("title") or "").splitlines() if t.strip()]
    if not titles:
        return jsonify(error="Give it at least one title — one per line."), 400

    steps = [s for s in (d.get("steps") or []) if s in VALID_STEPS]
    if not steps:
        return jsonify(error="Pick at least one thing to make."), 400

    style = d.get("style") or ""
    if style not in mv.prompts.PROMPT_SETS:
        have = ", ".join(sorted(mv.prompts.PROMPT_SETS)) or "none yet"
        return jsonify(error=f"No channel called '{style}'. Available: {have}."), 400

    try:
        minutes = max(1, min(60, int(d.get("minutes") or 10)))
    except (TypeError, ValueError):
        minutes = 10
    try:
        thumb_count = max(1, min(4, int(d.get("thumb_count") or 2)))
    except (TypeError, ValueError):
        thumb_count = 2

    # the mix sliders: three numbers that add up to 100 (AI stills, stock, graphics)
    mix = ()
    raw_mix = d.get("mix") or []
    if isinstance(raw_mix, (list, tuple)) and len(raw_mix) == 3:
        try:
            mix = tuple(max(0.0, float(x)) for x in raw_mix)
        except (TypeError, ValueError):
            mix = ()
    # the YouTube description, with as many chapters and sources as asked for
    meta = None
    if d.get("desc"):
        def _n(key, lo, hi):
            try:
                return max(lo, min(hi, int(d.get(key) or 0)))
            except (TypeError, ValueError):
                return lo
        meta = {"chapters": _n("chapters", 0, 20), "sources": _n("sources", 0, 10)}

    spec_base = dict(
        steps=steps, minutes=minutes, script_text=d.get("script") or "", mix=mix, meta=meta,
        burn_subs=bool(d.get("burn_subs", True)), force=bool(d.get("force", False)),
        style=style, thumb_count=thumb_count,
        use_motion=bool(d.get("use_motion", True)),
        byo_audio=(d.get("byo_audio") or "").strip(),
        character=(d.get("character") or "").strip()[:4000],
        character_image=_char_rel(d.get("character_image")),
        maps=bool(d.get("maps", True)),
        headlines=bool(d.get("headlines", True)),
        spotlight=bool(d.get("spotlight", True)),
        objects=bool(d.get("objects", True)),
        depth=bool(d.get("depth", True)),
        # the Options switches (features.FEATURES keys -> on/off) and the extra instructions
        features={k: bool(v) for k, v in (d.get("features") or {}).items() if k in features.BY_KEY} or None,
        extra=(d.get("extra") or "").strip()[:4000],
    )

    queued = []
    for t in titles:
        slug = mv.slugify(t)
        spec = dict(spec_base, title=t, slug=slug)
        JOBS[slug] = {"lines": [], "state": "queued", "error": None, "done": False,
                      "title": t, "steps": steps, "minutes": minutes, "style": style}
        with LOCK:
            QUEUE.append(spec)
        queued.append({"slug": slug, "title": t})
    _ensure_dispatcher()
    return jsonify(queued=queued)


@app.get("/api/channels")
def api_channels():
    """The channel picker. Reloaded from disk each time, so a style file edited
    in Claude Code shows up on a page refresh instead of needing a restart."""
    styles.load_all()
    styles.apply_to_engine(mv, motion)
    # which DLCs are on this machine, so Options only offers what can actually run
    dlc = {"maps": (HERE / "maps.py").exists() and (HERE / "assets" / "maps" / "gazetteer.json").exists(),
           "headlines": (HERE / "headlines.py").exists() and (HERE / "assets" / "headlines" / "page.js").exists(),
           "photofx": (HERE / "photofx.py").exists() and (HERE / "assets" / "photofx" / "page.js").exists()}
    # every switch in Options, and whether its API key is there — a missing key shows in red
    return jsonify(channels=styles.catalogue(), dlc=dlc, features=features.FEATURES,
                   caps=features.capabilities(), prices=features.prices())


@app.get("/api/queue")
def api_queue():
    with LOCK:
        pending = [{"slug": s["slug"], "title": s["title"]} for s in QUEUE]
        running = RUNNING["slug"]
    out = []
    if running and running in JOBS:
        j = JOBS[running]
        el = time.time() - j.get("started", time.time())
        out.append({"slug": running, "title": j.get("title", running),
                    "state": "running", "elapsed": int(el),
                    "line": (j["lines"][-1] if j.get("lines") else ""),
                    "progress": _progress(j)})
    out += [{**p, "state": "queued"} for p in pending]
    for slug in reversed(HISTORY[-12:]):
        j = JOBS.get(slug) or {}
        out.append({"slug": slug, "title": j.get("title", slug),
                    "state": j.get("state", "done"), "error": j.get("error")})
    return jsonify(jobs=out, busy=bool(running))


def _progress(job) -> float:
    """0..1 — how far through the step list this job has got.

    Read off the log rather than tracked separately: the pipeline already
    announces each stage, and a second source of truth would drift from it.
    """
    steps = job.get("steps") or []
    if not steps:
        return 0.0
    text = "\n".join(job.get("lines", [])[-400:])
    marks = [("script", "1/"), ("voiceover", "2/"), ("images", "kie:"),
             ("pexels", "Pexels:"), ("video", "segments"), ("thumbnail", "thumbnail")]
    done = sum(1 for name, mark in marks if name in steps and mark in text)
    return min(0.98, done / max(1, len(steps)))


@app.post("/api/queue/clear")
def api_queue_clear():
    with LOCK:
        n = len(QUEUE)
        QUEUE.clear()
    return jsonify(cleared=n)


@app.get("/api/status/<slug>")
def api_status(slug):
    j = JOBS.get(slug)
    if not j:
        return jsonify(error="unknown job"), 404
    return jsonify(lines=j.get("lines", [])[-500:], state=j.get("state"),
                   done=j.get("done"), error=j.get("error"),
                   progress=_progress(j))


@app.get("/api/result/<slug>")
def api_result(slug):
    job = OUT / slug
    if not job.is_dir():
        return jsonify(error="no such job"), 404
    thumbs = sorted(p.name for p in job.glob("thumbnail_[0-9]*.png"))
    return jsonify(
        slug=slug,
        title=(job / "title.txt").read_text(encoding="utf-8").splitlines()[0]
        if (job / "title.txt").exists() else slug,
        video=(job / "video.mp4").exists(),
        audio=(job / "audio.mp3").exists(),
        script=(job / "script.txt").exists(),
        srt=(job / "subs.srt").exists(),
        desc=(job / "youtube.txt").exists(),
        thumbs=thumbs,
        clips=len(list((job / "pexels").glob("*.mp4"))) if (job / "pexels").is_dir() else 0,
        images=len(list((job / "images").glob("*.jpg"))) if (job / "images").is_dir() else 0,
    )


@app.get("/api/jobs")
def api_jobs():
    """Everything already rendered, newest first."""
    rows = []
    if OUT.is_dir():
        for d in OUT.iterdir():
            v = d / "video.mp4"
            if not (d.is_dir() and v.exists()):
                continue
            t = (d / "title.txt")
            rows.append({"slug": d.name,
                         "title": t.read_text(encoding="utf-8").splitlines()[0] if t.exists() else d.name,
                         "mtime": v.stat().st_mtime,
                         "size": v.stat().st_size,
                         "desc": (d / "youtube.txt").exists(),
                         "style": (d / "style.txt").read_text(encoding="utf-8").strip()
                         if (d / "style.txt").exists() else ""})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    # which file manager "Open in …" opens on this computer
    platform = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
    return jsonify(jobs=rows[:60], platform=platform)


@app.post("/api/reveal/<slug>")
def api_reveal(slug):
    """Open the video's folder in the file manager with the video selected: Finder on a Mac, File Explorer
    on Windows, the folder itself elsewhere. Frontier listens on 127.0.0.1 only, so only this computer asks."""
    if not SAFE.match(slug):
        return jsonify(error="bad slug"), 400
    d = OUT / slug
    if not (d / "video.mp4").exists():
        return jsonify(error="That video is not on this computer any more."), 404
    t = d / "title.txt"
    named = d / mv.youtube_filename(t.read_text(encoding="utf-8").splitlines()[0]) if t.exists() else None
    target = named if named is not None and named.exists() else d / "video.mp4"
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif os.name == "nt":
            # one string on purpose: Explorer wants /select,"<path>" exactly as written
            subprocess.Popen(f'explorer /select,"{target}"')
        else:
            subprocess.Popen(["xdg-open", str(d)])
    except OSError as e:
        return jsonify(error=f"Could not open the folder ({e}). The video is in {d}"), 500
    return jsonify(ok=True, path=str(target))


# ── files ──────────────────────────────────────────────────────────────────

def _send(slug, *parts, **kw):
    if not SAFE.match(slug):
        return jsonify(error="bad slug"), 400
    p = OUT.joinpath(slug, *parts)
    if not p.exists():
        return jsonify(error="not found"), 404
    return send_file(p, **kw)


@app.get("/f/video/<slug>")
def f_video(slug):
    return _send(slug, "video.mp4")


@app.get("/f/audio/<slug>")
def f_audio(slug):
    return _send(slug, "audio.mp3")


@app.get("/f/thumb/<slug>/<name>")
def f_thumb(slug, name):
    if not SAFE.match(name):
        return jsonify(error="bad name"), 400
    return _send(slug, name)


@app.get("/f/script/<slug>")
def f_script(slug):
    return _send(slug, "script.txt", mimetype="text/plain")


@app.get("/f/srt/<slug>")
def f_srt(slug):
    return _send(slug, "subs.srt", mimetype="text/plain")


@app.get("/f/desc/<slug>")
def f_desc(slug):
    """The YouTube description: text, chapters, tags, sources — ready to paste."""
    return _send(slug, "youtube.txt", mimetype="text/plain")


@app.get("/f/zip/<slug>/<folder>")
def f_zip(slug, folder):
    if not (SAFE.match(slug) and SAFE.match(folder)):
        return jsonify(error="bad path"), 400
    d = OUT / slug / folder
    if not d.is_dir():
        return jsonify(error="not found"), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.iterdir()):
            if p.is_file():
                z.write(p, p.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{slug}-{folder}.zip")


CHAR_DIR = HERE / "assets" / "characters"
CHAR_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _char_rel(raw) -> str:
    """A character picked in Options, as a path under assets/ — or "" if it is not one."""
    rel = str(raw or "").strip().replace("\\", "/")
    name = rel[len("characters/"):] if rel.startswith("characters/") else ""
    if not name or "/" in name or ".." in name or Path(name).suffix.lower() not in CHAR_EXT:
        return ""
    return rel if (CHAR_DIR / name).is_file() else ""


def _char_items() -> list:
    """Every character picture in assets/characters, with the words that go with it."""
    own = {((st.get("look") or {}).get("character_image") or "").replace("\\", "/"): n
           for n, st in styles.STYLES.items()}
    out = []
    for p in sorted(CHAR_DIR.glob("*")) if CHAR_DIR.is_dir() else []:
        if not p.is_file() or p.suffix.lower() not in CHAR_EXT:
            continue
        rel = f"characters/{p.name}"
        side = p.with_suffix(".txt")
        out.append({"rel": rel, "file": p.name, "src": f"/charimg/{p.name}",
                    "label": re.sub(r"-[0-9a-f]{6}$", "", p.stem).replace("-", " ").strip() or "character",
                    "channel": own.get(rel, ""), "mtime": int(p.stat().st_mtime),
                    "text": side.read_text(encoding="utf-8").strip() if side.exists() else ""})
    return out


@app.get("/api/characters")
def api_characters():
    return jsonify(characters=_char_items())


@app.get("/charimg/<name>")
def f_charimg(name):
    p = CHAR_DIR / name
    if "/" in name or "\\" in name or ".." in name or p.suffix.lower() not in CHAR_EXT or not p.is_file():
        return Response(status=404)
    return send_file(p)


@app.post("/api/characters")
def api_character_upload():
    """A character of your own: saved beside the others, and put into words by
    Claude — the storyboard repeats those words in every image prompt."""
    f = request.files.get("file")
    if f is None:
        return jsonify(error="No picture came with the upload."), 400
    raw = f.read()
    if len(raw) > 20 * 1024 * 1024:
        return jsonify(error="That picture is over 20 MB — export a smaller one."), 400
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = ImageOps.exif_transpose(im)
    except Exception:                                            # noqa: BLE001
        return jsonify(error="Frontier cannot read that file as a picture — use PNG, JPG or WebP."), 400
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    im.thumbnail((1600, 1600))              # plenty for a reference, quick to put online
    stem = re.sub(r"[^a-z0-9]+", "-", Path(f.filename or "").stem.lower()).strip("-")[:40] or "character"
    CHAR_DIR.mkdir(parents=True, exist_ok=True)
    dest = CHAR_DIR / f"{stem}-{hashlib.sha1(raw).hexdigest()[:6]}.png"
    im.save(dest, "PNG", optimize=True)
    try:
        dest.with_suffix(".txt").write_text(mv.describe_character(dest), encoding="utf-8")
    except BaseException as e:              # the Claude layer can sys.exit; the upload still stands
        print(f"  character description failed: {str(e)[:160]}")
    item = next((x for x in _char_items() if x["file"] == dest.name), None)
    return jsonify(character=item)


@app.get("/char/<style>")
def f_char(style):
    """The channel's main character (`look.character_image`), shown in Options."""
    st = styles.STYLES.get(style) or {}
    rel = ((st.get("look") or {}).get("character_image") or "").strip()
    p = HERE / "assets" / rel
    if not rel or ".." in rel or not p.exists():
        return Response(status=404)
    return send_file(p)


@app.get("/ref/<style>")
def f_ref(style):
    """The picture a channel is aiming at (`look.reference` in its style file).
    Missing is not an error — the card simply shows no image."""
    st = styles.STYLES.get(style) or {}
    rel = ((st.get("look") or {}).get("reference") or "").strip()
    p = HERE / "assets" / rel
    if not rel or ".." in rel or not p.exists():
        return Response(status=404)
    return send_file(p)


@app.get("/sample/<style>")
def f_sample(style):
    """A video of what the channel makes (`look.sample` in its style file, a path under assets/).
    The card plays it muted; "Watch sample" opens it with sound. Sent with range support, so the
    browser can seek without reading the whole file. Missing is not an error — the card shows the
    reference picture instead."""
    st = styles.STYLES.get(style) or {}
    rel = ((st.get("look") or {}).get("sample") or "").strip()
    p = HERE / "assets" / rel
    if not rel or ".." in rel or not p.is_file():
        return Response(status=404)
    kind = {".webm": "video/webm", ".mov": "video/quicktime"}.get(p.suffix.lower(), "video/mp4")
    return send_file(p, mimetype=kind, conditional=True, max_age=3600)


@app.get("/brand/<name>")
def f_brand(name):
    """Frontier and Algrow marks. Missing files are not an error — the page
    falls back to type, so the tool still runs before the logos are dropped in."""
    if not SAFE.match(name):
        return jsonify(error="bad name"), 400
    p = HERE / "assets" / "brand" / name
    if not p.exists():
        return Response(status=404)
    return send_file(p)


@app.get("/")
def index():
    if not UI.exists():
        return Response("ui.html is missing next to app.py", mimetype="text/plain")
    return Response(UI.read_text(encoding="utf-8"), mimetype="text/html")


def _open_browser():
    time.sleep(0.8)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    except Exception:                                            # noqa: BLE001
        pass


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    print("\n  FRONTIER")
    print(f"  channels: {', '.join(LOADED) if LOADED else 'none yet — see CLAUDE.md'}")
    print(f"  http://127.0.0.1:{PORT}/\n")
    _ensure_dispatcher()
    if not os.environ.get("FRONTIER_NO_BROWSER"):
        threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
