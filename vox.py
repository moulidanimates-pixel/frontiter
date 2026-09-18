#!/usr/bin/env python3
"""vox.py — the Vox Style visuals engine (DLC): documentary paper collage, animated.

A channel opts in with `"look": {"visuals": "vox"}`; make_video.py hands itself in
as `engine` and calls two functions:

    prepare(engine, script, job, style, force)          while the voice is recorded
    assemble(engine, job, mp3, srt, style, force, subs) once the subtitles exist

What the video is made of, and why it is cheap
------------------------------------------------
The look is halftone cutouts, redacted eyes, torn cards and one hot red accent on
an archival map. Only the PICTURES need a model: one empty stage per video and
one cutout per person or object (drawn once, reused whenever it returns). They
come from WaveSpeed (bytedance/seedream-v4/edit, about $0.03 each) with a real
Wikipedia photograph as the reference, so the likeness is the real person.

Everything that MOVES is drawn here, in Chromium, for free: cutouts spring up and
settle, counters tick, pins drop and wobble, red string draws itself, labels type
on, underlines swipe, the camera drifts 2%. That is exactly what the channel's
video prompt asks for — paper handled by an invisible editor — and it keeps every
number and word on screen exact, which an image-to-video model does not.
"""

import base64
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, unquote

import numpy as np
import requests
from PIL import Image

import wavespeed

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "FrontierVideoTool/3 (documentary video generator)"}
W, H, FPS = 1920, 1080, 30
_PW_START = threading.Lock()


# ── settings from the style file ────────────────────────────────────────────
def _look(engine, style: str) -> dict:
    return ((engine.STYLE_INFO.get(style) or {}).get("look") or {})


_DEFAULTS = {}


def _defaults() -> dict:
    """assets/vox/defaults.json — the collage look for a channel whose style file has no `look.vox`
    (Vox scenes inside a Documentary or any other video)."""
    if not _DEFAULTS:
        try:
            _DEFAULTS.update(json.loads((HERE / "assets" / "vox" / "defaults.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _DEFAULTS["vox"] = {}
    return _DEFAULTS


def _cfg(engine, style: str) -> dict:
    return _look(engine, style).get("vox") or _defaults().get("vox") or {}


def _asset(rel: str) -> Path:
    return HERE / "assets" / rel


def _section(text: str, head: str) -> str:
    """One labelled part of the image prompt ("PALETTE: …"), up to the next label."""
    m = re.search(rf"{re.escape(head)}:\s*(.*?)(?=\n?[A-Z][A-Z &]+:|\Z)", text or "", re.S)
    return m.group(1).strip() if m else ""


# ── 1. the beats ────────────────────────────────────────────────────────────
KINDS = {"person", "object", "photo"}
ELEMENTS = {"cutout", "photo", "number", "counter", "headline", "label", "stamp", "shape", "pin", "string", "route", "scrap"}
TEXT_ELEMENTS = {"number", "counter", "headline", "label", "stamp"}
SHAPES = {"arrow", "burst", "cross", "circle", "tape"}
ENTERS = {"slide-left", "slide-right", "rise", "drop", "pop", "shuffle", "fade", "none"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")[:40] or "item"


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _at(v):
    return v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None


def _picture(raw: dict, kind_default: str):
    """The part of an element that becomes an AI picture."""
    describe = str(raw.get("describe") or raw.get("prompt") or "").strip()
    if not describe:
        return None
    kind = str(raw.get("kind") or kind_default).lower()
    kind = kind if kind in KINDS else kind_default
    return {"id": _slug(raw.get("id") or describe[:30]), "kind": kind, "describe": describe[:500],
            "wiki": str(raw.get("wiki") or "").strip() or None, "redact": bool(raw.get("redact", False))}


FILLER = {"so", "and", "but", "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "with", "then", "now", "yet",
          "or", "as", "it", "is", "was", "this", "that", "he", "she", "they", "we", "you", "his", "her", "their", "just",
          "even", "still", "also", "behind", "after", "before", ""}


def _size(raw: dict) -> str:
    v = str(raw.get("size") or "m").lower()
    return v if v in ("s", "m", "l") else "m"


def _element(raw, canvas: int):
    """One element of a shot, cleaned up — models drift in the details."""
    if not isinstance(raw, dict):
        return None
    el = str(raw.get("el") or raw.get("type") or "").lower()
    if el not in ELEMENTS:
        return None
    e = {"el": el, "x": _num(raw.get("x"), -0.3, canvas + 0.3, canvas / 2), "y": _num(raw.get("y"), -0.3, 1.3, 0.5),
         "rot": _num(raw.get("rot"), -40, 40, 0), "z": int(_num(raw.get("z"), 0, 9, 3)), "at": _at(raw.get("at"))}
    enter = str(raw.get("enter") or "").lower()
    e["enter"] = enter if enter in ENTERS else ""
    if el in ("cutout", "photo"):
        pic = _picture(raw, "person" if el == "cutout" else "photo")
        if not pic:
            return None
        if el == "photo":
            pic["kind"] = "photo"
        elif pic["kind"] == "photo":
            pic["kind"] = "object"
        e.update(pic)
        if el == "cutout":
            e["h"] = _num(raw.get("h"), 0.42 if pic["kind"] == "person" else 0.08, 3.0, 0.8)
            e["flip"] = bool(raw.get("flip"))
            mv = raw.get("move")
            if isinstance(mv, dict):
                e["move"] = {"x": _num(mv.get("x"), -0.5, canvas + 0.5, e["x"]),
                             "y": _num(mv.get("y"), -0.5, 1.5, e["y"]), "at": _at(mv.get("at"))}
        else:
            e["w"] = _num(raw.get("w"), 0.08, 2.0, 0.45)
            e["torn"] = bool(raw.get("torn", False))
    elif el == "number":
        text = str(raw.get("text") or raw.get("to") or "").strip()[:9]
        if not text:
            return None
        e.update(text=text, label=str(raw.get("label") or "")[:28].upper(), size=_size(raw))
    elif el == "counter":
        try:
            to = float(str(raw.get("to")).replace(",", ""))
        except (TypeError, ValueError):
            return None
        e.update(to=to, prefix=str(raw.get("prefix") or "")[:3], suffix=str(raw.get("suffix") or "")[:8],
                 label=str(raw.get("label") or "")[:24].upper(), size=_size(raw))
    elif el in ("headline", "label", "stamp"):
        text = " ".join(str(raw.get("text") or "").split()[:6])
        # a card that says "SO" or "AND" is a subtitle chunk, not a fact — it only confuses the frame
        if not text or all(w.strip(".,;:!?'\"").lower() in FILLER for w in text.split()):
            return None
        e["text"] = text if el == "label" else text.upper()
        if el == "headline":
            u = str(raw.get("underline") or "").upper().strip()
            st = str(raw.get("style") or "paper").lower()
            e.update(underline=u if u in e["text"].split() else "", style=st if st in ("paper", "mustard", "black") else "paper",
                     size=_size(raw))
    elif el == "shape":
        shape = str(raw.get("shape") or "").lower()
        if shape not in SHAPES:
            return None
        e.update(shape=shape, w=_num(raw.get("w"), 0.03, 1.6, 0.2))
    elif el == "pin":
        e["id"] = _slug(raw.get("id") or f"pin_{e['x']:.2f}_{e['y']:.2f}")
    elif el == "string":
        if not raw.get("from") or not raw.get("to"):
            return None
        e.update({"from": _slug(raw["from"]), "to": _slug(raw["to"])})
    elif el == "route":
        pts = [[_num(q[0], -0.2, canvas + 0.2, 0.5), _num(q[1], -0.2, 1.2, 0.5)]
               for q in (raw.get("points") or []) if isinstance(q, (list, tuple)) and len(q) >= 2][:10]
        if len(pts) < 2:
            return None
        e["points"] = pts
    elif el == "scrap":
        kind = str(raw.get("kind") or "newsprint").lower()
        e.update(kind=kind if kind in ("newsprint", "map", "mustard", "tape") else "newsprint",
                 w=_num(raw.get("w"), 0.03, 0.45, 0.25), h=_num(raw.get("h"), 0.03, 0.65, 0.3))
        e["z"] = min(e["z"], 2)
    return e


def _shot(raw: dict, prev):
    canvas = 2 if _num(raw.get("canvas"), 1, 2, 1) >= 1.5 else 1
    bg, background = raw.get("background"), {"type": "map"}
    if isinstance(bg, str) and bg.lower() in ("map", "paper", "newsprint"):
        background = {"type": bg.lower()}
    elif isinstance(bg, dict):
        kind = str(bg.get("type") or "").lower()
        if kind in ("map", "paper", "newsprint"):
            background = {"type": kind}
        elif kind == "photo":
            pic = _picture(bg, "photo")
            if pic:
                pic["kind"] = "photo"
                background = dict(pic, type="photo")
    els = [e for e in (_element(x, canvas) for x in (raw.get("elements") or [])[:12]) if e]
    reuse = bool(raw.get("reuse")) and prev is not None
    if reuse:
        canvas, background = prev["canvas"], prev["background"]
        els = [dict(e, enter="none", at=None, carried=True) for e in prev["elements"]] + els
    cam = raw.get("camera") if isinstance(raw.get("camera"), dict) else {}

    def view(v, dflt):
        v = v if isinstance(v, dict) else {}
        return {"x": _num(v.get("x"), 0, canvas, dflt["x"]), "y": _num(v.get("y"), 0, 1, dflt["y"]),
                "zoom": _num(v.get("zoom"), 1, 4, dflt["zoom"])}
    frm = view(cam.get("from"), {"x": canvas / 2, "y": 0.5, "zoom": 1.0})
    to = view(cam.get("to"), dict(frm, zoom=min(4, frm["zoom"] * 1.06)))
    return {"text": str(raw.get("text") or "").strip(), "idea": str(raw.get("idea") or "")[:240],
            "canvas": canvas, "background": background, "reuse": reuse, "elements": els,
            "camera": {"from": frm, "to": to}}


def _aim_camera(shot: dict) -> None:
    """Point the opening of a move at something. A two-frame pan that starts on empty
    sea for three seconds is dead air; the start is moved to the first picture."""
    cam, els = shot["camera"], [e for e in shot["elements"] if e["el"] in ("cutout", "photo", "number", "counter", "headline")]
    if not els:
        return
    f = cam["from"]
    half_w, half_h = 0.5 / f["zoom"], 0.5 / f["zoom"]
    inside = [e for e in els if abs(e["x"] - f["x"]) <= half_w and abs(e["y"] - f["y"]) <= half_h]
    if not inside:
        first = next((e for e in els if e["el"] in ("cutout", "photo")), els[0])
        f["x"], f["y"] = first["x"], first["y"]


def _pictures(shots: list) -> dict:
    """Every AI picture the shots use, once each — the first description of an id wins."""
    items = {}
    for s in shots:
        for e in s["elements"]:
            if e["el"] in ("cutout", "photo") and not e.get("carried"):
                items.setdefault(e["id"], {k: e[k] for k in ("id", "kind", "describe", "wiki", "redact")})
        bg = s["background"]
        if bg.get("type") == "photo":
            items.setdefault(bg["id"], {k: bg[k] for k in ("id", "kind", "describe", "wiki", "redact")})
    return items


def plan_beats(engine, script: str, job: Path, style: str, force: bool) -> list:
    """Claude directs the narration as shots: a composition, a camera move, elements on cue."""
    out = job / "vox" / "beats.json"
    if out.exists() and not force:
        engine.log("cached: vox/beats.json")
        return json.loads(out.read_text(encoding="utf-8"))
    cfg = _cfg(engine, style)
    beat_s = float(cfg.get("beat_s") or 3.4)
    wps = float(cfg.get("words_per_second") or 2.3)
    count = max(3, math.ceil(len(script.split()) / wps / beat_s))
    prompt = (str(cfg.get("beat_prompt") or "")
              .replace("[INSERT CHANNEL HERE]", engine._channel_brief(style))
              .replace("[INSERT COUNT HERE]", str(count))
              .replace("[INSERT WORDS HERE]", str(max(3, round(wps * beat_s))))
              .replace("[INSERT MOTION HERE]", str(cfg.get("video_style") or ""))
              .replace("[INSERT SCRIPT HERE]", script)) + (
        "\n\nREAL PEOPLE: a cutout or photo of a person is only for public figures (politicians, executives, "
        "celebrities, historical figures) — with their Wikipedia title in \"wiki\". Private people — victims, "
        "witnesses, relatives, ordinary employees, children — are NEVER drawn as a person: show them through "
        "their name on a typewriter label, a date stamp, a number card, the place, the object or a document instead."
        "\n\nACCURATE PICTURES: real people are shown as themselves — never a bar over their eyes. A vehicle, "
        "product, building or machine is drawn only with the Wikipedia title of that exact model or thing in \"wiki\" "
        "(its photo keeps the drawing true to it); when you cannot name one, show it as a label, stamp or number card. "
        "Words on cards are names, places, dates, figures or key terms — never a filler word like SO, AND or BUT — and "
        "they sit beside the cutouts, never hidden under them.")
    engine.log(f"Claude: directing {count} shots (~{beat_s:.1f}s each)...")
    raw = engine._json_items(prompt, max_tokens=max(8000, count * 1400))
    shots, prev = [], None
    for r in raw:
        if not isinstance(r, dict) or not str(r.get("text") or "").strip():
            continue
        sh = _shot(r, prev)
        if not sh["elements"] and sh["background"]["type"] != "photo":
            continue
        _aim_camera(sh)
        shots.append(sh)
        prev = sh
    if not shots:
        engine.sys.exit(f"{style}: Claude returned no shots")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")
    engine.log(f"  {len(shots)} shots, {len(_pictures(shots))} pictures to draw")
    return shots


# ── 2. real photographs from Wikipedia ──────────────────────────────────────
_WIKI_LOCK = threading.Lock()


def _wiki_json(url: str, params: dict) -> dict:
    """One Wikimedia API call. They throttle bursts, answering with an HTML error page;
    calls are made one at a time and retried with a pause when that happens."""
    last = None
    for wait in (3, 8, 15, 25, 40, 0):
        with _WIKI_LOCK:
            r = requests.get(url, headers=UA, timeout=30, params=params)
            if r.status_code == 200:
                try:
                    time.sleep(0.4)
                    return r.json()
                except ValueError:
                    pass
            last = f"HTTP {r.status_code}"
            try:
                wait = max(wait, min(60, int(r.headers.get("Retry-After") or 0)))
            except ValueError:
                pass
            time.sleep(wait)          # held inside the lock: the whole queue waits out a 429
    raise ValueError(f"Wikimedia kept refusing ({last})")


def wiki_photo(title: str, dest: Path, log=print):
    """(path, credit) for the lead photograph of a Wikipedia article, or (None, None).

    Only files on Wikimedia Commons are used: they carry a free licence (most
    historical photographs are public domain). A lead image that lives on
    Wikipedia itself is usually a non-free one, so it is skipped and the picture
    is drawn from the description alone."""
    try:
        q = _wiki_json("https://en.wikipedia.org/w/api.php", {
            "action": "query", "titles": title, "prop": "pageimages", "piprop": "thumbnail|name",
            "pithumbsize": 1200, "redirects": 1, "format": "json"})
        page = next(iter((q.get("query") or {}).get("pages", {}).values()), {})
        name, thumb = page.get("pageimage"), (page.get("thumbnail") or {}).get("source")
        if not name or not thumb:
            return None, None
        meta = _wiki_json("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "titles": f"File:{unquote(name)}", "prop": "imageinfo",
            "iiprop": "extmetadata|url", "format": "json"})
        cpage = next(iter((meta.get("query") or {}).get("pages", {}).values()), {})
        info = (cpage.get("imageinfo") or [{}])[0]
        if not info:
            log(f"    {title}: its photo is not on Commons (probably not free) — describing instead")
            return None, None
        em = info.get("extmetadata") or {}
        lic = re.sub(r"<[^>]+>", "", (em.get("LicenseShortName") or {}).get("value", "")).strip()
        # Public domain, CC0 and plain CC BY only. Share-alike and anything unclear
        # would put conditions on the finished video, so those are drawn from the
        # description instead.
        if not re.search(r"public domain|\bpd\b|cc0|no restrictions|^cc[ -]by(?![ -]?sa)[ -]?\d", lic.lower()):
            log(f"    {title}: photo licence '{lic or 'unknown'}' is not free enough — describing instead")
            return None, None
        artist = re.sub(r"<[^>]+>", "", (em.get("Artist") or {}).get("value", "")).strip()
        for wait in (5, 15, 30, 0):
            with _WIKI_LOCK:
                img = requests.get(thumb, headers=UA, timeout=60)
                if img.status_code != 429:
                    break
                time.sleep(wait)
        img.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(img.content)
        return dest, {"title": title, "file": unquote(name), "license": lic or "see Commons",
                      "artist": artist[:120], "url": info.get("descriptionurl", "")}
    except (requests.exceptions.RequestException, ValueError, StopIteration) as e:
        log(f"    {title}: no Wikipedia photo ({str(e)[:60]})")
        return None, None


# ── 3. the pictures ─────────────────────────────────────────────────────────
def _key_green(src: Path, dest: Path) -> bool:
    """Lift a cutout off its flat green backdrop. False when the backdrop is not
    green enough to trust (the caller then pays the background remover)."""
    rgba0 = np.asarray(Image.open(src).convert("RGBA")).astype(np.float32)
    im, a0 = rgba0[..., :3].copy(), rgba0[..., 3] / 255.0
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    green = g - np.maximum(r, b)
    edge = lambda x: np.concatenate([x[:8].ravel(), x[-8:].ravel(), x[:, :8].ravel(), x[:, -8:].ravel()])
    # some image services hand the cutout back already transparent (with a green rim from the prompt)
    see_through = np.median(edge(a0)) < 0.08 and 0.1 < float((a0 < 0.05).mean()) < 0.97
    if not see_through and np.median(edge(green)) < 60:
        return False
    alpha = np.clip(1.0 - (green - 35.0) / 55.0, 0.0, 1.0) * (a0 if see_through else 1.0)
    im[..., 1] = np.minimum(g, np.maximum(r, b) + 8)          # no green fringe on the keyline
    rgba = np.dstack([im, alpha * 255]).astype(np.uint8)
    _save_cutout(Image.fromarray(rgba, "RGBA"), dest)
    return True


def _save_cutout(im: Image.Image, dest: Path) -> None:
    from PIL import ImageFilter
    box = im.split()[3].point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.MedianFilter(7)).getbbox()
    if box:
        pad = 6
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                      min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    im.thumbnail((1400, 1400))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG", optimize=True)


def topic_look(job: Path) -> dict:
    """The collage look chosen for this story ({accent, stage, paper}), or {}: vox_scenes/look.json."""
    for f in (job.parent / "look.json", job / "vox_scenes" / "look.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                acc = str(d.get("accent") or "")
                d["accent"] = acc if re.fullmatch(r"#[0-9a-fA-F]{6}", acc) else ""
                return d
        except (OSError, ValueError):
            continue
    return {}


def colour_name(hexcode: str, default: str = "deep red") -> str:
    """A hex colour as words: image models paint "#0B6E4F" on the picture as text, "deep green" they understand."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hexcode or "").strip())
    if not m:
        return default
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hi, lo = max(r, g, b), min(r, g, b)
    light, sat = (hi + lo) / 2, (hi - lo)
    if sat < 0.12:
        return "near-black" if light < 0.2 else "warm grey" if light < 0.75 else "off-white"
    if hi == r:
        hue = ((g - b) / (hi - lo)) % 6
    elif hi == g:
        hue = (b - r) / (hi - lo) + 2
    else:
        hue = (r - g) / (hi - lo) + 4
    hue *= 60
    if sat < 0.32 and 18 <= hue < 70:
        return "tan" if light > 0.55 else "brown"
    if 12 <= hue < 45 and light < 0.36:
        return "brown"
    names = [(12, "red"), (32, "orange"), (50, "amber gold"), (68, "yellow"), (170, "green"), (200, "teal"),
             (255, "blue"), (290, "purple"), (335, "magenta"), (361, "red")]
    name = next(n for lim, n in names if hue < lim)
    tone = "deep " if light < 0.32 else "pale " if light > 0.72 else ""
    return tone + name


def _nohex(text: str) -> str:
    """Every hex colour code in a prompt, spoken as a colour name."""
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: colour_name(m.group(0)), str(text or ""))


def _draw(engine, model: str, payload: dict, dest: Path, label: str) -> None:
    """One picture. With an Algrow key, Algrow's image model draws it (a third of the price, and it keeps a
    real face from the reference photo best); otherwise WaveSpeed while its balance is above the reserve, then
    the engine's own image service with the same reference pictures — a collage never stops for a balance.
    VOX_IMAGES=wavespeed in .env keeps WaveSpeed first."""
    import os
    algrow_first = (os.environ.get("ALGROW_API_KEY") or "").strip() and \
        (os.environ.get("VOX_IMAGES") or "").strip().lower() != "wavespeed" and hasattr(engine, "image_gen")
    if algrow_first:
        try:
            engine._algrow_image_gen(payload.get("prompt") or "", dest, label=label,
                                     ref_urls=list(payload.get("images") or []) or None)
            return
        except Exception as e:                                  # noqa: BLE001 - WaveSpeed can still draw it
            engine.log(f"    {label}: Algrow could not draw it ({str(e)[:80]}) — trying WaveSpeed")
    if wavespeed.has_budget():
        try:
            wavespeed.run(model, payload, dest, label=label, log=engine.log)
            return
        except wavespeed.WaveSpeedBalanceError:
            engine.log(f"    {label}: WaveSpeed balance is used up — drawing it with the other image service")
    if not hasattr(engine, "image_gen"):
        raise wavespeed.WaveSpeedBalanceError("WaveSpeed balance is used up and no other image service is set")
    engine.image_gen(payload.get("prompt") or "", dest, label=label, ref_urls=list(payload.get("images") or []) or None)


def make_assets(engine, beats: list, job: Path, style: str, force: bool) -> dict:
    """Every picture the beats need, drawn once each, in parallel."""
    cfg = _cfg(engine, style)
    tlook = topic_look(job)
    accent = tlook.get("accent") or "#D62E1F"
    models = cfg.get("models") or {}
    img_style = str(cfg.get("image_style") or "")
    refs = cfg.get("references") or {}
    adir = job / "vox" / "assets"
    adir.mkdir(parents=True, exist_ok=True)
    cache = job / "vox" / "uploads.json"
    credits_f = job / "vox" / "credits.json"
    credits = json.loads(credits_f.read_text(encoding="utf-8")) if credits_f.exists() else {}
    lock = threading.Lock()

    items = _pictures(beats)

    def ref_url(rel):
        p = _asset(rel) if rel else None
        return wavespeed.upload(p, cache) if p is not None and p.is_file() else None

    palette = _section(img_style, "PALETTE")
    if tlook.get("accent"):
        palette = palette.replace("Hot Red #D62E1F", f"Story accent {accent}").replace("hot red", "accent")
    palette = _nohex(palette)
    accent_word = colour_name(accent)
    banned = _section(img_style, "PROHIBITIONS")

    def draw(it):
        ext = "jpg" if it["kind"] == "photo" else "png"
        dest = adir / f"{it['id']}.{ext}"
        if dest.exists() and not force:
            return it["id"], dest
        images = [u for u in [ref_url(refs.get("photo" if it["kind"] == "photo" else "cutout", ""))] if u]
        real = ""
        if it.get("wiki"):
            p, credit = wiki_photo(it["wiki"], adir / "wiki" / f"{it['id']}.jpg", engine.log)
            if p:
                images.append(wavespeed.upload(p, cache))
                with lock:
                    credits[it["id"]] = credit
                real = (f"Image {len(images)} is a real photograph of {it['wiki'].replace('_', ' ')}. "
                        f"Keep the real {'face, hair and clothing' if it['kind'] == 'person' else 'look and details'} "
                        f"from image {len(images)}. ")
        if it["kind"] == "photo" and it.get("wiki") and not real:
            # a real, documented moment (a crash, a ceremony) drawn from words alone comes out as another scene —
            # soldiers at an air crash — so the shot keeps its cards and its paper instead
            engine.log(f"    {it['id']}: no free photo of this real moment — left out rather than invented")
            return it["id"], None
        if it["kind"] == "person" and not real and not it.get("redact"):
            # a real, named person with no free photo to draw from would come out as a stranger wearing their
            # name — the shot keeps its cards and labels instead
            engine.log(f"    {it['id']}: no free photo of this person — left out rather than invented")
            return it["id"], None
        eyes = ("" if it.get("redact") else
                " Faces are real and uncovered: open, visible eyes — no bar, strip, tape or block over anyone's eyes "
                "(the eye bar in image 1 is NOT part of the treatment).")
        if it["kind"] == "photo":
            prompt = (f"{real}Image 1 shows only the photographic treatment to copy — never its subject, objects or "
                      f"paper. Make an archival black-and-white halftone photograph of "
                      f"{it['describe']}, as printed in an old newspaper: visible halftone dots, print grain, "
                      f"slightly faded, no border, no caption, no text, no watermark.{eyes} {banned}")
            size = "2048*1536"
        else:
            prompt = (f"Image 1 shows only the cutout TREATMENT to copy — never copy its person, its text, its tape, "
                      f"its paper or its background. {real}Make ONE documentary paper-collage cutout of {it['describe']} "
                      f"in the exact style of the cutouts in image 1: strictly black-and-white halftone photograph (no colour "
                      f"anywhere except the thin stroke) with visible "
                      f"halftone dot texture and print grain, a rough scissor-cut white keyline around the whole "
                      f"cutout, a thin offset {accent_word} stroke peeking out behind the keyline"
                      f"{', and a flat ink-black censor bar across the eyes' if it.get('redact') and it['kind'] == 'person' else ''}."
                      f"{eyes if it['kind'] == 'person' else ''} "
                      f"The whole cutout fits inside the frame with plain margin on every side. Background: plain flat "
                      f"solid pure chroma-key green and nothing else — no text, no paper, no map, no shadow. "
                      f"Only this one subject: no pins, no string, no tape, no stamps, no labels, no paper scraps, no "
                      f"colour codes, and no letters or numbers except what is really printed on the subject itself. {banned}")
            size = "1536*2048" if it["kind"] == "person" else "2048*1536"
        raw = adir / f"{it['id']}_raw.png"
        _draw(engine, models.get("cutout") or "bytedance/seedream-v4/edit",
              {"prompt": _nohex(prompt)[:3900], "images": images, "size": size}, raw, it["id"])
        if it["kind"] == "photo":
            Image.open(raw).convert("RGB").save(dest, "JPEG", quality=92)
        elif not _key_green(raw, dest):
            engine.log(f"    {it['id']}: backdrop not flat green — using the background remover")
            cut = adir / f"{it['id']}_rm.png"
            wavespeed.run(models.get("remover") or "wavespeed-ai/image-background-remover",
                          {"image": wavespeed.upload(raw, cache)}, cut, label=f"{it['id']} cutout", log=engine.log)
            _save_cutout(Image.open(cut).convert("RGBA"), dest)
        engine.log(f"    drew {it['id']} ({it['kind']}{', real photo' if real else ''})")
        return it["id"], dest

    def stage():
        dest = adir / "_stage.jpg"
        if dest.exists() and not force:
            return dest
        images = [u for u in (ref_url(r) for r in (refs.get("stage") or [])) if u]
        ground_txt = ((f"{tlook['stage']} Paper and print: {tlook.get('paper') or 'aged newsprint'}. A few torn clippings "
                       f"overlap one edge, two or three ") if tlook.get("stage") else
                      ("A muted archival sea chart fills the frame: pale grey-teal water, faded tan land with thin coastlines "
                       "and grid lines, laid on aged tan paper; a few torn newspaper clippings overlap one edge, two or three "))
        prompt = ("Using the paper, map and texture look of the reference images, create the EMPTY background stage "
                  f"of a documentary paper collage. {img_style.split('SURFACE & MOOD:')[0].strip()} "
                  f"{_section(img_style, 'SURFACE & MOOD')} Palette: {palette[:300]} "
                  f"{ground_txt}"
                  "masking tape strips, visible paper fibre, evenly lit, flat and matte, desaturated. "
                  "Absolutely empty: no people, no objects, no photos, no cards, no pins, no string, no stamps, "
                  "no readable text or letters anywhere. 16:9.")
        raw = adir / "_stage_raw.png"
        _draw(engine, models.get("stage") or "bytedance/seedream-v4/edit",
              {"prompt": _nohex(prompt)[:3900], "images": images, "size": "2560*1440"}, raw, "stage")
        im = Image.open(raw).convert("RGB")
        im = im.resize((W, int(W * im.height / im.width)), Image.LANCZOS)
        im.save(dest, "JPEG", quality=90)
        return dest

    engine.log(f"WaveSpeed: drawing the stage + {len(items)} pictures...")
    t0 = time.time()
    try:
        bal0 = wavespeed.balance()
    except Exception:                                          # noqa: BLE001 - the balance is only for the log
        bal0 = None
    paths = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs = ex.submit(stage)
        futs = [ex.submit(draw, it) for it in items.values()]
        errors = []
        for f in futs:
            try:
                k, p = f.result()
                if p is not None:
                    paths[k] = str(p)
            except wavespeed.WaveSpeedBalanceError as e:
                errors.append(str(e))
            except Exception as e:                             # noqa: BLE001
                errors.append(str(e)[:200])
        try:
            paths["_stage"] = str(fs.result())
        except Exception as e:                                 # noqa: BLE001
            errors.append(f"stage: {str(e)[:200]}")
    credits_f.write_text(json.dumps(credits, indent=2, ensure_ascii=False), encoding="utf-8")
    if errors:
        engine.sys.exit("Vox pictures could not all be made:\n  " + "\n  ".join(errors[:6]) +
                        "\nRun the same title again — every picture already made is kept.")
    spent = ""
    if bal0 is not None:
        try:
            spent = f", ${bal0 - wavespeed.balance():.2f} on WaveSpeed"
        except Exception:                                      # noqa: BLE001
            pass
    engine.log(f"  pictures ready in {time.time() - t0:.0f}s{spent}")
    return paths


def prepare(engine, script: str, job: Path, style: str, force: bool) -> None:
    """Beats and pictures — needs only the script, so it runs beside the voiceover."""
    beats = plan_beats(engine, script, job, style, force)
    make_assets(engine, beats, job, style, force)


# ── 3b. the narrator ────────────────────────────────────────────────────────
def _chunks(text: str, limit: int) -> list:
    """The script in pieces of whole sentences, each under the model's character limit."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    return out + ([cur] if cur else [])


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def voiceover(engine, script: str, job: Path, style: str, force: bool) -> tuple:
    """(audio.mp3, subs.srt) from WaveSpeed's ElevenLabs v3 with timings.

    The model returns every character's start and end, so the subtitles are the
    script's own words on the voice's own clock — no transcription pass, and no
    misspelled names in the captions."""
    mp3, srt = job / "audio.mp3", job / "subs.srt"
    if mp3.exists() and srt.exists() and not force:
        engine.log("cached: audio.mp3 + subs.srt")
        return mp3, srt
    voice = (engine.STYLE_INFO.get(style) or {}).get("voice") or {}
    model = voice.get("model") or "elevenlabs/eleven-v3/timing"
    vdir = job / "vox" / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    words, parts, offset = [], [], 0.0
    pieces = _chunks(script, 9000)
    engine.log(f"WaveSpeed voice: {model} ({voice.get('voice_id') or 'Brian'}), {len(script)} characters"
               f" in {len(pieces)} part(s)...")
    for i, text in enumerate(pieces):
        part, meta = vdir / f"part_{i:02d}.mp3", vdir / f"part_{i:02d}.json"
        if force or not (part.exists() and meta.exists()):
            d = wavespeed.result(model, {"text": text, "voice_id": voice.get("voice_id") or "Brian",
                                         "stability": float(voice.get("stability", 0.5)),
                                         "similarity": float(voice.get("similarity", 1.0)),
                                         "use_speaker_boost": True}, label=f"voice {i + 1}", log=engine.log)
            out = d["outputs"][0]
            wavespeed.download(out["audio"], part)
            meta.write_text(json.dumps(out.get("alignment") or {}), encoding="utf-8")
        al = json.loads(meta.read_text(encoding="utf-8"))
        cur, cs = "", None
        for ch, a, b in zip(al.get("characters", []), al.get("character_start_times_seconds", []),
                            al.get("character_end_times_seconds", [])):
            if ch.isspace():
                if cur:
                    words.append((cur, cs + offset, ce + offset))
                cur, cs = "", None
                continue
            cur, cs, ce = cur + ch, (a if cs is None else cs), b
        if cur:
            words.append((cur, cs + offset, ce + offset))
        parts.append(part)
        offset += engine._audio_dur(part)
    if len(parts) == 1:
        shutil.copyfile(parts[0], mp3)
    else:
        lst = vdir / "parts.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    cues, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x[0] for x in cur)
        if re.search(r"[.!?…]$", w[0]) or len(cur) >= 6 or len(text) >= 32:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    lines = []
    for k, c in enumerate(cues):
        end = c[-1][2]
        if k + 1 < len(cues) and cues[k + 1][0][1] - end < 0.3:
            end = cues[k + 1][0][1]
        lines.append(f"{k + 1}\n{_srt_time(c[0][1])} --> {_srt_time(end)}\n{' '.join(x[0] for x in c)}\n")
    srt.write_text("\n".join(lines), encoding="utf-8")
    engine.log(f"  voice: {offset:.1f}s, {len(cues)} subtitle lines")
    return mp3, srt


# ── 4. timing ───────────────────────────────────────────────────────────────
def _spoken_words(engine, cues: list) -> list:
    """[(word, seconds)] — each word timed by sliding through its subtitle cue."""
    out = []
    for st, en, txt in cues:
        ws = re.findall(r"[a-z0-9]+", engine._fold(txt))
        weights = [len(w) + 1 for w in ws]
        acc, tot = 0, float(sum(weights) or 1)
        for w, wt in zip(ws, weights):
            out.append((w, st + (en - st) * acc / tot))
            acc += wt
    return out


HOLD_S = 1.2        # a finished shot stays on screen at least this long before the cut
MIN_SHOT_S = 2.0    # and no shot is shorter than this


def _settle(e: dict) -> float:
    """Seconds an element needs to finish arriving: its entrance, plus any text it types."""
    el = e["el"]
    if el in ("cutout", "photo"):
        return 0.8
    if el == "headline":
        return 0.6 + 0.28 * max(1, len(str(e.get("text") or "").split()))
    if el == "label":
        return 0.3 + len(str(e.get("text") or "")) / 26
    if el == "number":
        return 0.76 + 0.16 * len(str(e.get("text") or "")) + len(str(e.get("label") or "")) / 26
    if el == "counter":
        return 1.5
    if el == "stamp":
        return 0.3
    if el == "string":
        return 0.8
    return 0.55


def _hold(beat: dict, dur: float, ats: list, moves: list) -> tuple:
    """Pull arrivals earlier so the last one lands HOLD_S before the cut.

    A picture that finished drawing itself a moment before the scene switched read as
    cut off: nothing may still be arriving in the final HOLD_S of a shot. Elements keep
    their order; one that has to move earlier takes the ones before it along."""
    ats, moves = list(ats), list(moves)
    els = beat["elements"]
    live = [i for i, e in enumerate(els) if not (e.get("carried") or e["el"] == "scrap")]
    order = sorted(live, key=lambda i: ats[i])       # arrival order, not the order they are listed in
    for i in live:
        ats[i] = round(max(0.0, min(ats[i], dur - HOLD_S - _settle(els[i]))), 3)
        if moves[i] is not None:
            moves[i] = round(max(ats[i] + 0.5, min(moves[i], dur - HOLD_S)), 3)
    for a, b in zip(order[::-1][1:], order[::-1]):   # walk back: what arrived first still does
        if ats[a] > ats[b]:
            ats[a] = ats[b]
    return ats, moves


def _timeline(engine, beats: list, srt: Path, total: float) -> list:
    """[{start, dur, ats, moves}] — when each shot cuts in and when each element arrives."""
    cues = engine._parse_srt_full(srt.read_text(encoding="utf-8")) if srt.exists() else []
    at = engine._story_times([b["text"] for b in beats], cues)
    n = len(beats)
    known = [(i, t) for i, t in enumerate(at) if t is not None]
    for i in range(n):
        if at[i] is None:
            a = max((k for k in known if k[0] < i), default=(-1, 0.0), key=lambda k: k[0])
            b = min((k for k in known if k[0] > i), default=(n, total), key=lambda k: k[0])
            at[i] = a[1] + (b[1] - a[1]) * (i - a[0]) / max(1, b[0] - a[0])
    at = [max(0.0, t - 0.12) for t in at]           # the cut lands a hair before the words
    at[0] = 0.0
    for i in range(1, n):
        at[i] = min(max(at[i], at[i - 1] + MIN_SHOT_S), max(at[i - 1] + 0.5, total - MIN_SHOT_S * (n - i)))
    words = _spoken_words(engine, cues)
    spans = []
    for i, b in enumerate(beats):
        start, end = at[i], (at[i + 1] if i + 1 < n else total)
        dur = end - start
        inside = [(w, t) for w, t in words if start - 0.05 <= t < end]

        def when(cue, default):
            if isinstance(cue, (int, float)):
                return min(max(float(cue), 0.0), dur * 0.8)
            key = re.findall(r"[a-z0-9]+", engine._fold(str(cue or "")))
            hit = next((t for w, t in inside if key and w == key[0]), None)
            return min(max((hit - start - 0.12) if hit is not None else default, 0.0), dur * 0.8)

        ats, moves, k = [], [], 0
        for e in b["elements"]:
            if e.get("carried") or e["el"] == "scrap":
                ats.append(0.0)
            else:
                ats.append(round(when(e.get("at"), 0.1 + 0.42 * k), 3))
                k += 1
            mv = e.get("move")
            if e["el"] == "route":
                arrive = when(e.get("at"), 0.15 + 1.4)
                ats[-1] = round(max(0.1, min(arrive - 1.0, 0.25)), 3)
                moves.append(round(max(arrive, ats[-1] + 0.9), 3))
            else:
                moves.append(round(max(when(mv.get("at"), dur * 0.85), ats[-1] + 0.9), 3) if mv else None)
        # An opening on empty paper reads as a mistake: unless a photograph fills the
        # frame, the main picture is there from the first frame.
        seen = [i for i, e in enumerate(b["elements"]) if e["el"] not in ("scrap", "string", "pin", "route")]
        if b["background"].get("type") != "photo" and seen and min(ats[i] for i in seen) > 0.12:
            first = next((i for i in seen if b["elements"][i]["el"] in ("cutout", "photo")), seen[0])
            ats[first] = 0.0
        ats, moves = _hold(b, dur, ats, moves)
        spans.append({"start": round(start, 3), "dur": round(dur, 3), "ats": ats, "moves": moves})
    return spans


# ── 5. the scene page ───────────────────────────────────────────────────────
def _data_uri(path: Path, max_side: int = 0) -> tuple:
    im = Image.open(path)
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    buf = __import__("io").BytesIO()
    if im.mode == "RGBA":
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=90)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}", im.width / max(1, im.height)


_FONT_CSS = {"css": None}


def _font_css() -> str:
    if _FONT_CSS["css"] is None:
        f = HERE / "assets" / "fonts" / "Anton-Regular.ttf"
        _FONT_CSS["css"] = (f"@font-face{{font-family:'Anton';src:url(data:font/ttf;base64,"
                            f"{base64.b64encode(f.read_bytes()).decode()});font-display:block}}") if f.exists() else ""
    return _FONT_CSS["css"]


_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
__FONTS__
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#C9BB9C}
#world{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}
#bgl{position:absolute;left:0;top:0}
.bg{position:absolute;top:0;height:1080px}
#grain{position:absolute;inset:0;pointer-events:none;opacity:.15;mix-blend-mode:multiply;background-size:420px 420px}
.el{position:absolute;will-change:transform,opacity}
.cut{display:block}
.scrapd{width:100%;height:100%;position:relative}
.news{background-color:#E6DECB;background-image:
  linear-gradient(#2a2a2a,#2a2a2a),radial-gradient(rgba(26,26,26,.5) 2.2px,transparent 2.6px),
  repeating-linear-gradient(90deg,transparent 0 118px,#E6DECB 118px 140px),
  repeating-linear-gradient(0deg,rgba(26,26,26,.30) 0 2px,transparent 2px 8px);
  background-size:62% 28px,8px 8px,100% 100%,100% 100%;background-position:24px 22px,62% 70px,0 0,0 0;
  background-repeat:no-repeat,no-repeat,repeat,repeat}
.mustard{background:#D9A441;background-image:radial-gradient(rgba(26,26,26,.14) 1.6px,transparent 1.8px);background-size:9px 9px}
.tapebit{background:rgba(232,221,190,.86)}
.paperbg{background:#D8CCAE;background-image:radial-gradient(ellipse at 30% 20%,rgba(255,250,235,.35),transparent 60%),
  radial-gradient(ellipse at 80% 90%,rgba(120,95,60,.18),transparent 55%)}
.newsbg{background-color:#D9D0BA;background-image:
  repeating-linear-gradient(90deg,transparent 0 300px,#D9D0BA 300px 336px),
  repeating-linear-gradient(0deg,rgba(40,40,40,.20) 0 3px,transparent 3px 14px);filter:sepia(.15)}
.photo{background:#F4EFE3;padding:16px;box-sizing:border-box;position:relative}
.photo img{display:block;width:100%;height:100%;object-fit:cover;filter:grayscale(1) contrast(1.05)}
.head{font-family:'Anton',Impact,sans-serif;line-height:.95;letter-spacing:1px;text-transform:uppercase;
      background:#F1EADB;color:#1A1A1A;white-space:nowrap}
.head.mustard{background-color:#D9A441}.head.black{background:#1A1A1A;color:#F1EADB}
.head .w{position:relative;display:inline-block}
.label{font-family:'American Typewriter','Courier Prime','Courier New',monospace;font-size:50px;padding:14px 32px;
       white-space:nowrap;background:#F1EADB;color:#1A1A1A}
.stamp{font-family:'Anton',Impact,sans-serif;color:var(--acc);font-size:104px;letter-spacing:4px;line-height:1.05;
       border:11px solid var(--acc);border-radius:16px;padding:6px 30px 12px;filter:url(#rough);white-space:nowrap}
.counter{background:var(--acc);color:#F4EFE3;font-family:'Anton',Impact,sans-serif;text-align:center}
.counter .n{line-height:1;padding:28px 56px 12px;white-space:nowrap}
.counter .l,.number .l{font-family:'American Typewriter','Courier Prime','Courier New',monospace;font-size:44px;
  background:#F1EADB;color:#1A1A1A;padding:10px 22px;margin:0 28px 28px;display:inline-block;white-space:nowrap}
.number{background:#E2584B;text-align:center;padding:18px 40px 8px}
.number .n{font-family:'Anton',Impact,sans-serif;line-height:1;color:#1A1A1A;white-space:nowrap;filter:url(#ink)}
.number .d{display:inline-block}
.tape{position:absolute;width:150px;height:46px;background:rgba(232,221,190,.85);box-shadow:0 1px 2px rgba(0,0,0,.14)}
.pin{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;
     background:radial-gradient(circle at 35% 30%,#fff2b0 0,#d9a441 38%,#8a5a12 100%);box-shadow:0 7px 7px rgba(0,0,0,.38)}
svg.lines{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
</style></head><body>
<svg width="0" height="0" style="position:absolute">
 <filter id="rough"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3"/><feDisplacementMap in="SourceGraphic" scale="6"/></filter>
 <filter id="ink"><feTurbulence type="fractalNoise" baseFrequency="0.55" numOctaves="3" seed="8" result="n"/>
   <feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -3.2 2.35" result="m"/>
   <feComposite in="SourceGraphic" in2="m" operator="in"/></filter>
 <g id="mbs"></g>
</svg>
<div id="world"><div id="bgl"></div></div><div id="grain"></div>
<script>
const S=__SCENE__, W=1920, H=1080, FPS=30, CW=W*S.canvas, CH=H;
const ACC=S.accent||'#D62E1F'; document.documentElement.style.setProperty('--acc',ACC);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const quint=x=>1-Math.pow(1-clamp(x,0,1),5);
const inout=x=>{x=clamp(x,0,1);return x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2};
const back=x=>{x=clamp(x,0,1);const c1=.9,c3=c1+1;return 1+c3*Math.pow(x-1,3)+c1*Math.pow(x-1,2)};
let seed=S.seed||1; const rnd=()=>{seed=(seed*16807)%2147483647;return (seed-1)/2147483646};
const world=document.getElementById('world'), bgl=document.getElementById('bgl'), mbs=document.getElementById('mbs');
Object.assign(world.style,{width:CW+'px',height:CH+'px'}); Object.assign(bgl.style,{width:CW+'px',height:CH+'px'});
document.getElementById('grain').style.backgroundImage="url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='420' height='420'><filter id='g'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .5 0 0 0 0 .45 0 0 0 0 .38 0 0 0 .55 0'/></filter><rect width='420' height='420' filter='url(%23g)'/></svg>\")";
const parts=[], pins={}, lines=[], unders=[];
const SV='http://www.w3.org/2000/svg';
function jag(amp,e){const pts=[],n=22,m=9,r=()=>(rnd()*amp).toFixed(2);
  for(let i=0;i<=n;i++)pts.push(`${(i/n*100).toFixed(1)}% ${e.top?r():0}%`);
  for(let i=0;i<=m;i++)pts.push(`${e.right?100-r():100}% ${(i/m*100).toFixed(1)}%`);
  for(let i=n;i>=0;i--)pts.push(`${(i/n*100).toFixed(1)}% ${e.bottom?100-r():100}%`);
  for(let i=m;i>=0;i--)pts.push(`${e.left?r():0}% ${(i/m*100).toFixed(1)}%`);
  return `polygon(${pts.join(',')})`}
const ALL={top:1,right:1,bottom:1,left:1};
function star(n,r1,r2){const p=[];for(let i=0;i<n*2;i++){const a=i/(n*2)*Math.PI*2-Math.PI/2,r=i%2?r2*(.75+rnd()*.35):r1*(.8+rnd()*.3);
  p.push(`${(50+50*r*Math.cos(a)).toFixed(1)}% ${(50+50*r*Math.sin(a)).toFixed(1)}%`)}return `polygon(${p.join(',')})`}
function fmt(e,v){return (e.prefix||'')+Math.round(v).toLocaleString('en-US')+(e.suffix||'')}
function tape(el,side,rot){const t=document.createElement('div');t.className='tape';
  Object.assign(t.style,{top:'-14px',transform:`rotate(${rot}deg)`});t.style[side]='-40px';el.appendChild(t)}

/* one element, centred on (x,y); moving ones get a motion-blur filter of their own */
function place(e,html,w,h,opt){const d=document.createElement('div');d.className='el';d.innerHTML=html;
  const X=e.x*W, Y=e.y*H;
  Object.assign(d.style,{left:(X-w/2)+'px',top:(Y-h/2)+'px',zIndex:10+e.z});
  if(w)d.style.width=w+'px'; if(h)d.style.height=h+'px';
  d.style.transformOrigin=opt.origin||'50% 50%'; world.appendChild(d);
  // a card sizes itself to its words: once it has a size it is centred on (x,y) too, as the beats are written
  if(!w&&!h){d.style.left=(X-d.offsetWidth/2)+'px'; d.style.top=(Y-d.offsetHeight/2)+'px';}
  const q={d,e,enter:opt.enter,at:e.at||0,rot:e.rot||0,dist:opt.dist||0,dir:opt.dir||1,w,h,
           shadow:opt.shadow||'0 14px 16px rgba(26,26,26,.30)',mb:null,move:e.move?{dx:(e.move.x-e.x)*W,dy:(e.move.y-e.y)*H,end:e.mat}:null};
  if(q.enter!=='none'||q.move){const id='mb'+parts.length, f=document.createElementNS(SV,'filter');f.id=id;
    ['x','y'].forEach(k=>f.setAttribute(k,'-30%'));['width','height'].forEach(k=>f.setAttribute(k,'160%'));
    const g=document.createElementNS(SV,'feGaussianBlur');g.setAttribute('stdDeviation','0 0');f.appendChild(g);mbs.appendChild(f);q.mb={id,g};}
  parts.push(q);return d}

function background(){const b=S.background;
  if(b.type==='photo'&&b.src){const d=document.createElement('div');d.className='bg';
    Object.assign(d.style,{left:0,width:CW+'px',background:`#222 url(${b.src}) center/cover no-repeat`,filter:'grayscale(1) contrast(1.08) brightness(.9)'});
    bgl.appendChild(d); return;}
  if(b.type==='paper'||b.type==='newsprint'){const d=document.createElement('div');d.className='bg '+(b.type==='paper'?'paperbg':'newsbg');
    Object.assign(d.style,{left:0,width:CW+'px'});bgl.appendChild(d);
    if(b.type==='newsprint') for(let c=0;c<S.canvas;c++){const ox=c*W;
      const blk=(x,y,w,h,css)=>{const k=document.createElement('div');Object.assign(k.style,{position:'absolute',left:(ox+x)+'px',top:y+'px',width:w+'px',height:h+'px'},css);d.appendChild(k);};
      blk(70,40,1780,96,{background:'#2c2c2c'});
      blk(70,170,980,54,{background:'#3a3a3a'}); blk(1100,170,750,54,{background:'#3a3a3a'});
      blk(70,260,640,420,{background:'radial-gradient(rgba(25,25,25,.62) 2.6px,transparent 3px) 0 0/10px 10px,#bdb39c'});
      blk(1290,560,560,380,{background:'radial-gradient(rgba(25,25,25,.55) 2.6px,transparent 3px) 0 0/10px 10px,#c3b9a2'});}
    return;}
  for(let i=0;i<S.canvas;i++){const im=document.createElement('img');im.className='bg';im.src=S.stage;
    Object.assign(im.style,{left:(i*W)+'px',width:W+'px',objectFit:'cover',transform:i%2?'scaleX(-1)':'none'});bgl.appendChild(im);}
  for(let i=1;i<S.canvas;i++){const d=document.createElement('div');d.className='bg news';
    Object.assign(d.style,{left:(i*W-150)+'px',width:'300px',height:'1180px',top:'-50px',transform:'rotate(1.5deg)',
      clipPath:jag(4,{left:1,right:1}),boxShadow:'0 0 14px rgba(0,0,0,.25)'});bgl.appendChild(d);}}

function build(){
  background();
  const cards=[];
  S.elements.forEach((e,i)=>{
    if(e.el==='cutout'&&e.src){const h=e.h*H, w=h*e.ar;
      const def=e.kind==='person'?(e.x<S.canvas/2?'slide-left':'slide-right'):'drop';
      const en=e.enter||def;
      place(e,`<img class="cut" src="${e.src}" style="width:${w}px;height:${h}px;${e.flip?'transform:scaleX(-1)':''}">`,w,h,
        {enter:en,dist:en.startsWith('slide')?w*.9+260:h*.8+120,dir:en==='slide-left'?-1:1,origin:'50% 90%'});}
    else if(e.el==='photo'&&e.src){const w=e.w*W, h=Math.min(H*1.2,w/e.ar);
      const d=place(e,`<div class="photo" style="width:${w}px;height:${h}px"><img src="${e.src}"></div>`,w,h,
        {enter:e.enter||'shuffle',dist:w*.8,dir:e.x<S.canvas/2?-1:1});
      if(e.torn) d.firstChild.style.clipPath=jag(3,ALL); else {tape(d.firstChild,'left',-24);tape(d.firstChild,'right',22);}}
    else if(e.el==='scrap'){const w=e.w*W,h=e.h*H, cls={newsprint:'news',map:'',mustard:'mustard',tape:'tapebit'}[e.kind];
      const d=place(e,`<div class="scrapd ${cls}"></div>`,w,h,{enter:'none',shadow:'0 5px 7px rgba(26,26,26,.2)'});
      d.firstChild.style.clipPath=e.kind==='tape'?'none':jag(2.4,ALL);
      if(e.kind==='map'){Object.assign(d.firstChild.style,{backgroundImage:`url(${S.stage})`,backgroundSize:'1920px 1080px',
        backgroundPosition:`${-Math.round(rnd()*900)}px ${-Math.round(rnd()*500)}px`,filter:'sepia(.3) contrast(1.05)'});}}
    else if(e.el==='headline'){const fs={s:92,m:132,l:184}[e.size];
      const words=e.text.split(' ').map(w=>`<span class="w">${w}</span>`).join(' ');
      const d=place(e,`<div class="head ${e.style}" style="font-size:${fs}px;padding:${fs*.2}px ${fs*.32}px ${fs*.26}px">${words}</div>`,0,0,
        {enter:e.enter||'peel'}); d.firstChild.style.clipPath=jag(2.6,ALL); cards.push(d);}
    else if(e.el==='label'){const d=place(e,`<div class="label"><span class="tx">${e.text}</span></div>`,0,0,{enter:e.enter||'unroll',origin:'0 50%'});
      tape(d,'left',-9); cards.push(d);}
    else if(e.el==='stamp'){const d=place(e,`<div class="stamp">${e.text}</div>`,0,0,{enter:e.enter||'thump',shadow:'0 4px 5px rgba(26,26,26,.15)'});cards.push(d);}
    else if(e.el==='counter'){const fs={s:130,m:200,l:280}[e.size];
      const d=place(e,`<div class="counter"><div class="n" style="font-size:${fs}px">${fmt(e,e.to)}</div>`+(e.label?`<div class="l">${e.label}</div>`:'')+`</div>`,0,0,{enter:e.enter||'pop'});
      d.firstChild.style.minWidth=d.firstChild.offsetWidth+'px'; d.firstChild.style.clipPath=jag(1.6,{top:1,bottom:1}); cards.push(d);}
    else if(e.el==='number'){const fs={s:180,m:260,l:360}[e.size];
      const digits=[...e.text].map(ch=>`<span class="d">${ch}</span>`).join('');
      const d=place(e,`<div class="number"><div class="n" style="font-size:${fs}px">${digits}</div>`+(e.label?`<div class="l"><span class="tx">${e.label}</span></div>`:'')+`</div>`,0,0,{enter:e.enter||'pop'});
      const lab=d.querySelector('.l'); if(lab) lab.style.minWidth=lab.offsetWidth+'px';
      d.firstChild.style.clipPath=jag(2.2,ALL); cards.push(d);}
    else if(e.el==='shape'){const w=e.w*W;
      if(e.shape==='arrow'){const h=w*.46;
        place(e,`<div style="width:${w}px;height:${h}px;background:${ACC};clip-path:polygon(0% 32%,64% 30%,62% 4%,100% 50%,62% 96%,64% 70%,0% 68%)"></div>`,w,h,
          {enter:e.enter||'slide-dir',dist:260,shadow:'0 6px 8px rgba(26,26,26,.25)'});}
      else if(e.shape==='burst'){place(e,`<div style="width:${w}px;height:${w*.84}px;position:relative">
          <div style="position:absolute;inset:0;background:${ACC};clip-path:${star(11,1,.52)}"></div>
          <div style="position:absolute;inset:20%;background:#F4EFE3;clip-path:${star(11,1,.5)}"></div></div>`,w,w*.84,
          {enter:e.enter||'grow',shadow:'0 6px 8px rgba(26,26,26,.2)'});}
      else if(e.shape==='cross'){place(e,`<div style="width:${w}px;height:${w}px;background:${ACC};clip-path:polygon(34% 0%,66% 0%,66% 34%,100% 34%,100% 66%,66% 66%,66% 100%,34% 100%,34% 66%,0% 66%,0% 34%,34% 34%)"></div>`,w,w,
          {enter:e.enter||'grow',shadow:'0 6px 8px rgba(26,26,26,.2)'});}
      else if(e.shape==='tape'){const h=Math.max(46,w*.07);
        place(e,`<div style="width:${w}px;height:${h}px;background:${ACC};clip-path:${jag(10,{left:1,right:1})}"></div>`,w,h,
          {enter:e.enter||'wipe',origin:'0 50%',shadow:'0 4px 6px rgba(26,26,26,.2)'});}
      else if(e.shape==='circle'){lines.push({kind:'circle',e});}}
    else if(e.el==='pin'){pins[e.id]=e;}
    else if(e.el==='string'||e.el==='route'){lines.push({kind:e.el,e});}
  });
  keepInView(cards);
  /* pins, string, routes and rings live in one svg layer above everything */
  const svg=document.createElementNS(SV,'svg');svg.setAttribute('class','lines');svg.setAttribute('width',CW);svg.setAttribute('height',CH);
  svg.style.zIndex=40; world.appendChild(svg);
  lines.forEach(L=>{const p=document.createElementNS(SV,'path'); let dstr='';
    if(L.kind==='string'){const a=pins[L.e.from],b=pins[L.e.to]; if(!a||!b) return;
      const ax=a.x*W,ay=a.y*H,bx=b.x*W,by=b.y*H; dstr=`M${ax} ${ay} Q${(ax+bx)/2} ${Math.max(ay,by)+60} ${bx} ${by}`;
      p.setAttribute('stroke','#C22A1D');p.setAttribute('stroke-width','6');L.at=Math.max(L.e.at||0,(a.at||0)+.3,(b.at||0)+.3);}
    else if(L.kind==='route'){const P=L.e.points.map(q=>[q[0]*W,q[1]*H]); dstr=`M${P[0][0]} ${P[0][1]}`;
      for(let i=1;i<P.length;i++){const p0=P[i-2]||P[i-1],p1=P[i-1],p2=P[i],p3=P[i+1]||P[i];
        const c1=[p1[0]+(p2[0]-p0[0])/6,p1[1]+(p2[1]-p0[1])/6], c2=[p2[0]-(p3[0]-p1[0])/6,p2[1]-(p3[1]-p1[1])/6];
        dstr+=` C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${p2[0]} ${p2[1]}`;}
      p.setAttribute('stroke',ACC);p.setAttribute('stroke-width','9');L.at=L.e.at||0;L.dash=true;L.end=L.e.mat;}
    else {const cx=L.e.x*W,cy=L.e.y*H,r=L.e.w*W/2; dstr=`M${cx-r} ${cy} C${cx-r} ${cy-r*.9} ${cx+r*1.05} ${cy-r*.95} ${cx+r} ${cy} C${cx+r*.95} ${cy+r} ${cx-r*.9} ${cy+r*.95} ${cx-r*1.08} ${cy-r*.1}`;
      p.setAttribute('stroke',ACC);p.setAttribute('stroke-width','10');L.at=L.e.at||0;}
    p.setAttribute('d',dstr);p.setAttribute('fill','none');p.setAttribute('stroke-linecap','round');svg.appendChild(p);
    L.p=p;L.len=p.getTotalLength();L.dur=L.kind==='route'?Math.max(.8,(L.end||L.at+1.4)-L.at):.45;
    if(L.dash){const defs=svg.querySelector('defs')||svg.insertBefore(document.createElementNS(SV,'defs'),svg.firstChild);
      const mask=document.createElementNS(SV,'mask'); mask.id='rm'+lines.indexOf(L);
      [['maskUnits','userSpaceOnUse'],['x',-200],['y',-200],['width',CW+400],['height',CH+400]].forEach(([k,v])=>mask.setAttribute(k,v));
      const mp=document.createElementNS(SV,'path'); mp.setAttribute('d',dstr); mp.setAttribute('fill','none');
      mp.setAttribute('stroke','#fff'); mp.setAttribute('stroke-width','18'); mp.setAttribute('stroke-linecap','round');
      mask.appendChild(mp); defs.appendChild(mask); p.setAttribute('mask',`url(#${mask.id})`); p.setAttribute('stroke-dasharray','26 18');
      L.mp=mp;}
  });
  Object.values(pins).forEach(e=>{const d=document.createElement('div');d.className='pin';
    Object.assign(d.style,{left:(e.x*W)+'px',top:(e.y*H)+'px',zIndex:45});world.appendChild(d);e.d=d;});
  /* the underline under a headline word sweeps in word by word up to the chosen word */
  parts.filter(q=>q.e.el==='headline'&&q.e.underline).forEach(q=>{const box=q.d.firstChild, ws=[...box.querySelectorAll('.w')];
    const k=Math.max(0,ws.findIndex(s=>s.textContent===q.e.underline));
    ws.slice(0,k+1).forEach((w,j)=>{const ww=w.offsetWidth+14, s2=document.createElementNS(SV,'svg');
      Object.assign(s2.style,{position:'absolute',left:(box.offsetLeft+w.offsetLeft-7)+'px',top:(box.offsetTop+w.offsetTop+w.offsetHeight-22)+'px',overflow:'visible'});
      s2.setAttribute('width',ww);s2.setAttribute('height',36);
      const pth=document.createElementNS(SV,'path');pth.setAttribute('d',`M5 20 C ${ww*.3} 12, ${ww*.64} 30, ${ww-5} 15`);
      pth.setAttribute('fill','none');pth.setAttribute('stroke',ACC);pth.setAttribute('stroke-width',Math.max(10,box.offsetHeight*.07));pth.setAttribute('stroke-linecap','round');
      s2.appendChild(pth);q.d.appendChild(s2);const len=pth.getTotalLength();pth.style.strokeDasharray=len;
      unders.push({p:pth,len,at:Math.min(q.at+.55+j*.28,S.dur-HOLD-.3-(k-j)*.2)});});});
}

/* nothing may still be arriving in the last HOLD seconds of a shot (vox.py HOLD_S) */
const HOLD=Math.min(1.2,Math.max(.3,S.dur*.3));
function view(t){const D=Math.max(S.dur,.1), u=inout(t/D), f=S.camera.from, g=S.camera.to;
  const z=Math.exp(Math.log(f.zoom)+(Math.log(g.zoom)-Math.log(f.zoom))*u);
  const hw=W/(2*z), hh=H/(2*z);
  return {z, cx:clamp((f.x+(g.x-f.x)*u)*W,hw,CW-hw), cy:clamp((f.y+(g.y-f.y)*u)*H,hh,CH-hh)}}
/* text must be readable on screen for the whole shot: the view is checked from the moment the card arrives to
   the end of the move (the middle of an eased zoom is tighter than either end); a card wider than the tightest
   view is shrunk to fit, then nudged inside all of them */
function keepInView(cards){const m=40;
  cards.forEach(d=>{const q=parts.find(p=>p.d===d), t0=Math.min(q?q.at:0,S.dur), t1=Math.min(S.dur,t0+2.2);
    // readable from the moment it lands for as long as it takes to read it — a pan across a wide canvas may
    // leave it behind afterwards, as the eye moves on with the camera
    const views=[0,.2,.4,.6,.8,1].map(k=>view(t0+(t1-t0)*k));
    const fitW=Math.min(...views.map(v=>W/v.z-2*m/v.z)), fitH=Math.min(...views.map(v=>H/v.z-2*m/v.z));
    const inner=d.firstElementChild, sc=Math.min(1,fitW/Math.max(1,d.offsetWidth),fitH/Math.max(1,d.offsetHeight));
    if(sc<1&&inner){const cx=parseFloat(d.style.left)+d.offsetWidth/2, cy=parseFloat(d.style.top)+d.offsetHeight/2;
      inner.style.zoom=Math.max(.4,sc*.95); d.style.left=(cx-d.offsetWidth/2)+'px'; d.style.top=(cy-d.offsetHeight/2)+'px';}
    const w=d.offsetWidth,h=d.offsetHeight; let x=parseFloat(d.style.left), y=parseFloat(d.style.top);
    const rect=v=>({x0:v.cx-W/(2*v.z)+m/v.z, x1:v.cx+W/(2*v.z)-m/v.z-w, y0:v.cy-H/(2*v.z)+m/v.z, y1:v.cy+H/(2*v.z)-m/v.z-h});
    let x0=-1e9,x1=1e9,y0=-1e9,y1=1e9;
    views.forEach(v=>{const r=rect(v); x0=Math.max(x0,r.x0); x1=Math.min(x1,r.x1); y0=Math.max(y0,r.y0); y1=Math.min(y1,r.y1);});
    if(x1>=x0) x=clamp(x,x0,x1); else x=(x0+x1)/2;
    if(y1>=y0) y=clamp(y,y0,y1); else y=(y0+y1)/2;
    d.style.left=x+'px'; d.style.top=y+'px';});
  for(let pass=0;pass<4;pass++) cards.forEach((d,i)=>cards.slice(0,i).forEach(o=>{
    const a={x:parseFloat(d.style.left),y:parseFloat(d.style.top),w:d.offsetWidth,h:d.offsetHeight},
          b={x:parseFloat(o.style.left),y:parseFloat(o.style.top),w:o.offsetWidth,h:o.offsetHeight};
    const ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x), oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
    if(ox>20&&oy>20){const v2=view(S.dur), L=v2.cx-W/(2*v2.z)+30, R=v2.cx+W/(2*v2.z)-30, T=v2.cy-H/(2*v2.z)+30, B=v2.cy+H/(2*v2.z)-30;
      const opts=[[a.x,b.y+b.h+24],[a.x,b.y-a.h-24],[b.x+b.w+24,a.y],[b.x-a.w-24,a.y]];
      // no free place in view: it stays where it is readable, overlapping, rather than leave the frame
      const ok=opts.find(([x,y])=>x>=L&&x+a.w<=R&&y>=T&&y+a.h<=B);
      if(ok){d.style.left=ok[0]+'px'; d.style.top=ok[1]+'px';}}}));}

function pose(q,lt){const P={x:0,y:0,s:1,sx:1,r:q.rot,o:lt>=0?1:0};
  if(q.enter==='none') {P.o=1;}
  else if(lt>=0){
    if(q.enter==='slide-left'||q.enter==='slide-right'){const e=quint(lt/.72);P.x=q.dir*q.dist*(1-e);P.r=q.rot+q.dir*2.4*(1-e);}
    else if(q.enter==='slide-dir'){const e=quint(lt/.6), a=q.rot*Math.PI/180;P.x=-Math.cos(a)*q.dist*(1-e);P.y=-Math.sin(a)*q.dist*(1-e);}
    else if(q.enter==='rise'){const e=quint(lt/.72);P.y=q.dist*(1-e);}
    else if(q.enter==='drop'){const e=quint(lt/.66);P.y=-q.dist*(1-e);P.r=q.rot+9*(1-e);}
    else if(q.enter==='shuffle'){const e=quint(lt/.72);P.x=q.dir*q.dist*(1-e);P.r=q.rot+q.dir*7*(1-e);}
    else if(q.enter==='peel'){const e=quint(lt/.5);P.x=-110*(1-e);P.o=clamp(lt/.14,0,1);P.r=q.rot-2*(1-e);}
    else if(q.enter==='unroll'){P.sx=quint(lt/.3);}
    else if(q.enter==='wipe'){P.sx=quint(lt/.5);}
    else if(q.enter==='thump'){P.s=1.55-.55*quint(lt/.17);P.o=.94;}
    else if(q.enter==='pop'){P.s=.9+.1*back(lt/.42);P.o=clamp(lt/.12,0,1);}
    else if(q.enter==='grow'){P.s=.2+.8*back(lt/.5);P.o=clamp(lt/.1,0,1);P.r=q.rot-10*(1-clamp(lt/.5,0,1));}
    else if(q.enter==='fade'){P.o=clamp(lt/.4,0,1);}}
  else P.o=0;
  if(q.move){const t0=q.at+.35, m=inout((lt+q.at-t0)/Math.max(.5,q.move.end-t0)); P.x+=q.move.dx*m; P.y+=q.move.dy*m;}
  return P}

window.renderFrame=function(t){
  const v=view(t);
  world.style.transform=`translate(${(W/2-v.cx*v.z).toFixed(2)}px,${(H/2-v.cy*v.z).toFixed(2)}px) scale(${v.z.toFixed(5)})`;
  const dof=S.background.type==='photo'?0:clamp((v.z-1.5)*1.8,0,2.4); bgl.style.filter=dof>.05?`blur(${dof.toFixed(2)}px)`:'none';
  parts.forEach(q=>{const lt=t-q.at, P=pose(q,lt), d=q.d, e=q.e;
    d.style.opacity=P.o;
    d.style.transform=`translate(${P.x.toFixed(2)}px,${P.y.toFixed(2)}px) rotate(${P.r.toFixed(3)}deg) scale(${(P.s*P.sx).toFixed(4)},${P.s.toFixed(4)})`;
    let f=`drop-shadow(${q.shadow})`;
    if(q.mb){const Q=pose(q,lt-1/FPS), bx=Math.min(22,Math.abs(P.x-Q.x)*v.z*.3), by=Math.min(22,Math.abs(P.y-Q.y)*v.z*.3);
      q.mb.g.setAttribute('stdDeviation',`${bx.toFixed(2)} ${by.toFixed(2)}`); if(bx>.15||by>.15) f=`url(#${q.mb.id}) `+f;}
    d.style.filter=f;
    const room=Math.max(.4,S.dur-q.at-HOLD);
    if(e.el==='label'){const dt=Math.min(e.text.length/26,room-.3), n=Math.floor(e.text.length*clamp((lt-.28)/Math.max(.15,dt),0,1));
      d.querySelector('.tx').textContent=e.text.slice(0,n)||' ';}
    else if(e.el==='counter'){const dur=clamp(room-.35,.4,1.2), k=Math.floor(Math.max(0,lt-.25)*15)/15;
      d.querySelector('.n').textContent=fmt(e,e.to*inout(k/dur));}
    else if(e.el==='number'){const ds=d.querySelectorAll('.d'), lab=e.label||'';
      const need=.3+ds.length*.16+.26+.2+lab.length/26, k=Math.min(1,room/need);
      ds.forEach((el,i)=>{const p=clamp((lt-(.3+i*.16)*k)/(.26*k),0,1); el.style.clipPath=`inset(${((1-quint(p))*100).toFixed(1)}% 0 0 0)`;});
      const tx=d.querySelector('.tx'); if(tx){const t0=(.3+ds.length*.16+.2)*k, n=Math.floor(lab.length*clamp((lt-t0)/Math.max(.15,lab.length/26*k),0,1));
        tx.textContent=lab.slice(0,n)||' ';}}});
  Object.values(pins).forEach(e=>{const lt=t-(e.at||0), q=clamp(lt/.24,0,1), wob=lt>.24?9*Math.exp(-8*(lt-.24))*Math.sin(24*(lt-.24)):0;
    e.d.style.opacity=lt>=0?1:0; e.d.style.transform=`translateY(${(-160*(1-q*q)).toFixed(2)}px) rotate(${wob.toFixed(2)}deg)`;});
  lines.forEach(L=>{if(!L.p) return; const lt=t-L.at;
    L.p.style.opacity=lt>=0?1:0;
    if(L.mp){const u=inout(lt/L.dur); L.mp.style.strokeDasharray=L.len; L.mp.style.strokeDashoffset=L.len*(1-u);}
    else {const u=quint(lt/L.dur); L.p.style.strokeDasharray=L.len; L.p.style.strokeDashoffset=L.len*(1-u);}});
  unders.forEach(v2=>{v2.p.style.strokeDashoffset=v2.len*(1-quint((t-v2.at)/.3))});
};
Promise.all([document.fonts.load("100px Anton"),...[...document.images].map(i=>i.decode().catch(()=>0))])
  .then(()=>document.fonts.ready).then(()=>{build();
    return Promise.all([...document.images].map(i=>i.decode().catch(()=>0)))})
  .then(()=>{window.renderFrame(0);window.__ready=true});
</script></body></html>"""


def _scene(beat: dict, span: dict, idx: int, assets: dict, uris: dict) -> dict:
    def uri(ident):
        if ident not in uris:
            uris[ident] = _data_uri(Path(assets[ident]), 1500)
        return uris[ident]
    elements = []
    # words stay in front of the pictures they sit beside — the carried cutout of a cut-in included
    top = max([int(e.get("z", 3)) for e in beat["elements"] if e["el"] in ("cutout", "photo")] or [0])
    ats = list(span["ats"])
    pictured = any(e["el"] in ("cutout", "photo") and e.get("id") in assets for e in beat["elements"]) or \
        (beat["background"].get("type") == "photo" and beat["background"].get("id") in assets)
    if not pictured and ats:
        # a shot whose picture could not be drawn would open on bare paper: its first card is there from the start
        k = min(range(len(ats)), key=lambda i: ats[i] if isinstance(ats[i], (int, float)) else 1e9)
        if isinstance(ats[k], (int, float)) and ats[k] > 0.3:
            ats[k] = 0.15
    for e, at, mat in zip(beat["elements"], ats, span["moves"]):
        d = dict(e, at=at)
        if e["el"] in ("number", "counter", "headline", "label", "stamp"):
            d["z"] = min(9, max(int(e.get("z", 3)), top + 1))
        if mat is not None:
            d["mat"] = mat
        if e["el"] in ("cutout", "photo"):
            if e["id"] not in assets:
                continue
            d["src"], d["ar"] = uri(e["id"])
        elements.append(d)
    bg = dict(beat["background"])
    if bg.get("type") == "photo":
        if bg.get("id") in assets:
            bg["src"] = uri(bg["id"])[0]
        else:
            bg = {"type": "map"}
    # the stage and the pictures are printed at screen size: a camera closer than 2x (1.6x on a photograph)
    # shows their pixels, a blur of paper instead of a close-up
    lim = 1.6 if bg.get("type") == "photo" else 2.0
    camera = {k: dict(v, zoom=min(float(v.get("zoom") or 1.0), lim)) for k, v in beat["camera"].items()}
    return {"dur": span["dur"], "idx": idx, "seed": 1 + idx * 7919, "canvas": beat.get("canvas", 1),
            "stage": uri("_stage")[0], "background": bg, "elements": elements, "camera": camera}


def _render(engine, pages: list, workers: int = 4) -> None:
    """pages = [(html, dur, out_mp4)] — every frame from renderFrame(t), piped to ffmpeg."""
    def do(chunk):
        from playwright.sync_api import sync_playwright
        with _PW_START:
            pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu",
                                               "--font-render-hinting=none"])
            for html, dur, out in chunk:
                if out.exists() and out.stat().st_size > 10_000:
                    continue
                page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                page.set_content(html, wait_until="load")
                page.wait_for_function("window.__ready===true", timeout=60000)
                n = max(1, int(round(dur * FPS)))
                tmp = out.with_suffix(".part.mp4")
                ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
                                       "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                                       "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(FPS), str(tmp)],
                                      stdin=subprocess.PIPE)
                for k in range(n):
                    page.evaluate("(t)=>window.renderFrame(t)", k / FPS)
                    ff.stdin.write(page.screenshot(type="jpeg", quality=95))
                ff.stdin.close()
                if ff.wait() != 0:
                    raise RuntimeError(f"ffmpeg failed on {out.name}")
                tmp.replace(out)
                page.close()
            browser.close()
        finally:
            pw.stop()
    workers = max(1, min(workers, len(pages)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do, [pages[i::workers] for i in range(workers)]))


def assemble(engine, job: Path, mp3: Path, srt: Path, style: str, force: bool, burn_subs: bool) -> Path:
    final = job / "video.mp4"
    if final.exists() and not force:
        engine.log(f"cached: {final.name}")
        return final
    beats = json.loads((job / "vox" / "beats.json").read_text(encoding="utf-8"))
    adir = job / "vox" / "assets"
    assets = {p.stem: str(p) for p in adir.glob("*") if p.is_file() and not p.stem.endswith(("_raw", "_rm"))}
    total = engine._audio_dur(mp3)
    spans = _timeline(engine, beats, srt, total)
    (job / "vox" / "timeline.json").write_text(json.dumps(
        [dict(s, text=b["text"]) for s, b in zip(spans, beats)], indent=2, ensure_ascii=False), encoding="utf-8")
    seg_dir = job / "vox" / "segments"
    if force and seg_dir.exists():
        shutil.rmtree(seg_dir)
    seg_dir.mkdir(parents=True, exist_ok=True)
    pages, uris = [], {}
    for i, (b, s) in enumerate(zip(beats, spans)):
        sc = _scene(b, s, i, assets, uris)
        html = _HTML.replace("__FONTS__", _font_css()).replace("__SCENE__", json.dumps(sc))
        pages.append((html, s["dur"], seg_dir / f"beat_{i:03d}.mp4"))
    engine.log(f"rendering {len(pages)} collage beats (Chromium, {FPS} fps)...")
    t0 = time.time()
    _render(engine, pages)
    engine.log(f"  beats rendered in {time.time() - t0:.0f}s")
    engine._concat_segments([p[2] for p in pages], job / "_visual.mp4")
    # The collage carries the words on paper; this channel ships without subtitles
    # unless its style file turns them back on. subs.srt is still written (chapters).
    subs = burn_subs and _cfg(engine, style).get("subtitles", True) is not False
    engine._final_mux(job, mp3, srt, subs, add_qr=False, vignette=False)
    (job / "_visual.mp4").unlink(missing_ok=True)
    return final


# ── 7. as scenes inside another channel's video (Options → Vox style scenes) ─────────
MOMENTS_PROMPT = """You are the editor of a documentary YouTube video. A few moments of it will be told as a
hand-cut paper collage in the style of Vox: real people as black-and-white halftone cutouts, torn archival
photographs, typewriter labels, big red number cards, pins and red string on an archival map.

Choose the [INSERT N HERE] moments of the narration below this treatment tells best: introducing a real person
and what they did, a key event with its date and place, a turning point with a striking number. Spread them
through the video. Never in the first 20 seconds. The TAKEN times already show a map, a photo or an article:
a moment must start and end at least 3 seconds away from every one of them.

A moment is one to three sentences: 7 to 14 seconds of narration.

Return ONLY a JSON array: [{"start": "the first 4 to 8 words of the moment, copied exactly", "end": "its last 4 to 8 words, copied exactly"}]

TAKEN (seconds): [INSERT TAKEN HERE]

NARRATION (each line starts with the second it is spoken):
[INSERT LINES HERE]"""

SCENE_EVERY_S = 150.0        # one collage moment per this much video
# scenes placed on a word by another module: a collage moment never covers one. A plain graphic gives way.
PROTECTED = ("introcap", "map_", "headline_", "spot_", "objects_", "vox_")


def _word_hit(engine, cues: list, words: str, after: float, last: bool) -> float:
    """The second `words` start (or, with last=True, finish) at or after `after`, or -1."""
    if not hasattr(engine, "_find_line"):
        return -1.0
    stream = engine._spoken_stream(cues)
    got = engine._find_line(stream, words, after, after, 1e9, end=last)
    return float(got) if got is not None and got >= after - 0.5 else -1.0


def add_to_timeline(segs: list, srt: Path, job: Path, style: str, force: bool, workers: int = 2,
                    engine=None, total: float = 0.0) -> list:
    """A few moments of the video told as Vox paper collage, dropped into the graphics timeline."""
    if engine is None:
        import make_video as engine
    log = engine.log
    cues = engine._parse_srt_full(srt.read_text(encoding="utf-8")) if srt.exists() else []
    if not cues:
        return segs
    total = total or cues[-1][1]
    n = max(1, min(6, int(total // float(_cfg(engine, style).get("scene_every_s") or SCENE_EVERY_S))))
    root = job / "vox_scenes"
    root.mkdir(parents=True, exist_ok=True)
    taken = sorted((float(a), float(a) + float(d)) for a, p, d in segs if Path(str(p)).name.startswith(PROTECTED))
    plan_f = root / "moments.json"
    moments = []
    if plan_f.exists() and not force:
        moments = json.loads(plan_f.read_text(encoding="utf-8"))
    else:
        lines = "\n".join(f"{st:.1f} {txt}" for st, _, txt in cues)
        log(f"Claude: choosing {n} moment(s) for Vox collage scenes...")
        items = engine._json_items(MOMENTS_PROMPT.replace("[INSERT N HERE]", str(n + 3))
                                   .replace("[INSERT TAKEN HERE]", ", ".join(f"{a:.0f}-{b:.0f}" for a, b in taken) or "none")
                                   .replace("[INSERT LINES HERE]", lines[:60000]) + engine._extra_block(),
                                   max_tokens=1500)
        for it in items[: n + 5]:
            if not isinstance(it, dict):
                continue
            t0 = _word_hit(engine, cues, it.get("start"), 20.0, False)
            t1 = _word_hit(engine, cues, it.get("end"), max(t0, 0.0), True) if t0 >= 0 else -1.0
            if t0 < 0 or t1 <= t0:
                log(f"  vox: skipped '{str(it.get('start'))[:40]}' — its words were not found in the subtitles")
                continue
            t0 = max(0.0, t0 - 0.12)
            t1 = min(t1 + 0.35, t0 + 16.0, total - 0.5)
            if t1 - t0 < 5.0:
                log(f"  vox: skipped {int(t0) // 60}:{int(t0) % 60:02d} — only {t1 - t0:.1f}s long")
                continue
            if any(t0 < b + 2.0 and t1 > a - 2.0 for a, b in taken + [(m["t0"], m["t1"]) for m in moments]):
                log(f"  vox: skipped {int(t0) // 60}:{int(t0) % 60:02d} — another scene is on screen there")
                continue
            words = " ".join(txt for _, _, txt in (engine._cues_between(cues, t0, t1) if hasattr(engine, "_cues_between")
                                                   else [c for c in cues if c[0] >= t0 - 0.05 and c[1] <= t1 + 0.4]))
            moments.append({"t0": round(t0, 2), "t1": round(t1, 2), "text": words})
            if len(moments) >= n:
                break
        plan_f.write_text(json.dumps(moments, indent=1, ensure_ascii=False), encoding="utf-8")
    if not moments:
        log("vox: no moment fits this time")
        return segs

    subs = []
    for k, m in enumerate(moments):
        sub = root / f"m{k:02d}"
        (sub / "vox").mkdir(parents=True, exist_ok=True)
        # the moment's own narration and subtitles, starting at zero
        rows, idx = [], 1
        inside = (engine._cues_between(cues, m["t0"], m["t1"]) if hasattr(engine, "_cues_between") else
                  [c for c in cues if c[0] >= m["t0"] - 0.05 and c[1] <= m["t1"] + 0.4])
        for st, en, txt in inside:
            rows.append(f"{idx}\n{_srt_time(max(0.0, st - m['t0']))} --> {_srt_time(max(0.01, en - m['t0']))}\n{txt}\n")
            idx += 1
        (sub / "subs.srt").write_text("\n".join(rows), encoding="utf-8")
        subs.append(sub)

    def direct(k):
        try:
            return k, plan_beats(engine, moments[k]["text"], subs[k], style, force)
        except (Exception, SystemExit) as e:                       # noqa: BLE001 - one moment is not the video
            log(f"  vox moment {k + 1}: no shots — {str(e)[:120]}")
            return k, []

    with ThreadPoolExecutor(max_workers=3) as ex:
        beats = dict(ex.map(direct, range(len(moments))))
    # one stage for every moment: drawn with the first, copied to the rest
    first = next((k for k in range(len(moments)) if beats.get(k)), None)
    if first is None:
        return segs
    drawn = {}
    try:
        drawn[first] = make_assets(engine, beats[first], subs[first], style, force)
    except (Exception, SystemExit) as e:                           # noqa: BLE001
        log(f"  vox: pictures failed — {str(e)[:160]}")
        return segs
    stage = subs[first] / "vox" / "assets" / "_stage.jpg"

    def draw(k):
        (subs[k] / "vox" / "assets").mkdir(parents=True, exist_ok=True)
        if stage.exists():
            shutil.copy2(stage, subs[k] / "vox" / "assets" / "_stage.jpg")
        try:
            return k, make_assets(engine, beats[k], subs[k], style, False)
        except (Exception, SystemExit) as e:                       # noqa: BLE001
            log(f"  vox moment {k + 1}: pictures failed — {str(e)[:120]}")
            return k, None

    with ThreadPoolExecutor(max_workers=3) as ex:
        for k, paths in ex.map(draw, [k for k in range(len(moments)) if k != first and beats.get(k)]):
            if paths:
                drawn[k] = paths

    out_dir = job / "motion"
    out_dir.mkdir(exist_ok=True)
    pages, finals, uris_all = [], [], {}
    for k in sorted(drawn):
        m, sub = moments[k], subs[k]
        final = out_dir / f"vox_{k:02d}.mp4"
        if force:
            final.unlink(missing_ok=True)
        dur = m["t1"] - m["t0"]
        if final.exists():
            finals.append((m["t0"], final, dur, []))
            continue
        adir = sub / "vox" / "assets"
        assets = {p.stem: str(p) for p in adir.glob("*") if p.is_file() and not p.stem.endswith(("_raw", "_rm"))}
        spans = _timeline(engine, beats[k], sub / "subs.srt", dur)
        seg_dir = sub / "vox" / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        uris, parts = {}, []
        acc = topic_look(sub).get("accent")
        for i, (b, sp) in enumerate(zip(beats[k], spans)):
            sc = _scene(b, sp, i, assets, uris)
            if acc:
                sc["accent"] = acc
            html = _HTML.replace("__FONTS__", _font_css()).replace("__SCENE__", json.dumps(sc))
            seg = seg_dir / f"beat_{i:03d}.mp4"
            if force:
                seg.unlink(missing_ok=True)
            pages.append((html, sp["dur"], seg))
            parts.append(seg)
        finals.append((m["t0"], final, dur, parts))
    if pages:
        log(f"vox: rendering {len(pages)} collage shots for {len(finals)} moment(s)...")
        _render(engine, pages, workers=max(1, workers))
    added = []
    for t0, final, dur, parts in finals:
        if parts and not final.exists():
            if not all(p.exists() for p in parts):
                continue
            engine._concat_segments(parts, final)
        if final.exists():
            added.append((t0, str(final), dur))
    log(f"vox: {len(added)} collage scene(s) at " + ", ".join(f"{int(a) // 60}:{int(a) % 60:02d}" for a, _, _ in added))
    # a plain graphic under a collage moment gives way to it (a directed video spaced them itself)
    pad = 0.0 if (job / "director.json").exists() else 1.0
    kept = [x for x in segs if Path(str(x[1])).name.startswith(PROTECTED) or
            not any(float(x[0]) < a + d + pad and float(x[0]) + float(x[2]) > a - pad for a, _, d in added)]
    if len(kept) < len(segs):
        log(f"  vox: {len(segs) - len(kept)} graphic(s) gave way")
    return sorted(kept + added, key=lambda x: float(x[0]))

