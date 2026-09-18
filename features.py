"""features.py — what a video can be built from: the switches in Options, and the API each one needs.

Every switch is one entry in FEATURES. A channel's style file says which of them it offers and
which are on by default (`"features": {"offered": [...], "on": [...]}`); Options draws exactly
those, ticked, and anyone can untick what they do not want in this video.

`capabilities()` reads `.env` fresh on every call, so a key pasted while Frontier is running shows
up on the next page refresh — and a switch whose API is missing is shown in red with the exact
key to add, instead of failing halfway through a render.
"""

import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

FEATURES = [
    {"key": "youtube", "label": "YouTube footage",
     "hint": "real footage of the actual events, people and places — found on YouTube, trimmed to the shot and cropped clear of logos",
     "needs": "youtube"},
    {"key": "stock", "label": "Stock footage",
     "hint": "Pexels b-roll searched for what the narrator is saying",
     "needs": "pexels"},
    {"key": "ai_images", "label": "AI images",
     "hint": "pictures drawn for the exact words they sit on",
     "needs": "images"},
    {"key": "depth", "label": "Depth pop", "parent": "ai_images",
     "hint": "a person or a car in a picture slowly lifts off it while the background pulls back",
     "needs": "wavespeed"},
    {"key": "motion", "label": "Motion graphics",
     "hint": "animated scenes and text labels for the numbers, dates, names and turning points",
     "needs": ""},
    {"key": "vox", "label": "Vox style scenes",
     "hint": "hand-cut paper collage for the people and the key facts, animated like paper on a table",
     "needs": "wavespeed"},
    {"key": "maps", "label": "Map animations",
     "hint": "a real map whenever the voice names a place that matters — routes, pins, regions lifting out",
     "needs": ""},
    {"key": "spotlight", "label": "Photo spotlight",
     "hint": "a real photo of who the voice names; the rest darkens as the camera pushes in",
     "needs": "wavespeed"},
    {"key": "objects", "label": "Real objects",
     "hint": "the products and things the voice names, cut out and flying in with a shadow",
     "needs": "wavespeed"},
    {"key": "headlines", "label": "Newspaper animations",
     "hint": "the real article behind a claim, on screen, the sentence highlighted — three different page moves",
     "needs": ""},
    {"key": "sound", "label": "Sound design",
     "hint": "whooshes, hits and risers under every scene change and animation, kept under the voice",
     "needs": ""},
]
KEYS = [f["key"] for f in FEATURES]
BY_KEY = {f["key"]: f for f in FEATURES}


def _env() -> dict:
    """The keys as they are right now: the process environment, overlaid with .env read fresh."""
    env = dict(os.environ)
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                env[k.strip()] = v
    return env


def _has(env: dict, *names) -> bool:
    return any((env.get(n) or "").strip() for n in names)


def _yt_dlp(env: dict) -> bool:
    b = (env.get("YTDLP_BIN") or "").strip()
    if (b and Path(b).exists()) or shutil.which("yt-dlp"):
        return True
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def capabilities() -> dict:
    """{"base": {...}, "features": {key: {"ok": bool, "why": str}}} for the UI and the engine."""
    env = _env()
    claude = bool(shutil.which("claude")) or _has(env, "ANTHROPIC_API_KEY", "KIE_API_KEY")
    voice = _has(env, "ALGROW_API_KEY", "WAVESPEED_API_KEY", "AI33_API_KEY")
    images = _has(env, "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY")
    base = {
        "claude": {"ok": claude, "why": "" if claude else
                   "Scripts need Claude: log in to Claude Code, or add ANTHROPIC_API_KEY (or KIE_API_KEY) to .env."},
        "voice": {"ok": voice, "why": "" if voice else
                  "The voiceover needs ALGROW_API_KEY, WAVESPEED_API_KEY or AI33_API_KEY in .env."},
    }
    need = {
        "": (True, ""),
        "pexels": (_has(env, "PEXELS_API_KEY"), "Add PEXELS_API_KEY to .env (free at pexels.com/api)."),
        "images": (images, "Add ALGROW_API_KEY, WAVESPEED_API_KEY or KIE_API_KEY to .env."),
        "wavespeed": (_has(env, "WAVESPEED_API_KEY"), "Add WAVESPEED_API_KEY to .env (wavespeed.ai)."),
    }
    yt_key = _has(env, "ALGROW_API_KEY", "GEMINI_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY")
    if not _yt_dlp(env):
        need["youtube"] = (False, "Install yt-dlp (see README → YouTube footage).")
    else:
        need["youtube"] = (yt_key, "Add ALGROW_API_KEY, or a Gemini key: GEMINI_API_KEY, WAVESPEED_API_KEY or KIE_API_KEY.")
    feats = {}
    for f in FEATURES:
        ok, why = need.get(f["needs"], (True, ""))
        feats[f["key"]] = {"ok": bool(ok), "why": "" if ok else f"API missing — {why}"}
    return {"base": base, "features": feats}


def prices() -> dict:
    """What each switch roughly costs per minute of finished video with the services in .env right now —
    {"voice": {...}, "features": {key: {"per_min": usd, "note": str}}, "picture": usd per AI picture}.
    Estimates for the Options panel, not a bill: the services' own dashboards have the real numbers."""
    env = _env()
    has = lambda *names: _has(env, *names)
    voice = "algrow" if has("ALGROW_API_KEY") else "wavespeed" if has("WAVESPEED_API_KEY") else "ai33" if has("AI33_API_KEY") else ""
    images = "algrow" if has("ALGROW_API_KEY") else "wavespeed" if has("WAVESPEED_API_KEY") else "kie" if has("KIE_API_KEY") else ""
    watcher = ("algrow" if has("ALGROW_API_KEY") else "gemini" if has("GEMINI_API_KEY") else
               "wavespeed" if has("WAVESPEED_API_KEY") else "kie" if has("KIE_API_KEY") else "")
    picture = {"algrow": 0.011, "wavespeed": 0.027, "kie": 0.02}.get(images, 0.02)
    # vox draws on Algrow when there is a key (vox._draw), on WaveSpeed's seedream otherwise
    collage = 0.011 if has("ALGROW_API_KEY") else 0.027 if has("WAVESPEED_API_KEY") else picture
    v = {"algrow": (0.02, "ElevenLabs on Algrow — about $0.02 a minute past your plan's included characters"),
         "wavespeed": (0.18, "ElevenLabs v3 on WaveSpeed — about $0.18 a minute"),
         "ai33": (0.05, "ai33 — about $0.05 a minute"), "": (0.0, "no voice service yet")}[voice]
    yt = {"algrow": (0.25, "a video model watches every candidate video — Algrow, about 1 credit per 4 minutes watched"),
          "gemini": (0.02, "Gemini API watches the candidate videos — free tier, then cents"),
          "wavespeed": (0.05, "Gemini on WaveSpeed watches the candidate videos"),
          "kie": (0.05, "Gemini on kie.ai watches the candidate videos"), "": (0.0, "")}[watcher]
    feats = {
        "youtube": yt,
        "stock": (0.0, "Pexels — free"),
        "ai_images": (2.5 * picture, f"about ${picture:.3f} a picture on {images or 'your image service'}"),
        "depth": (0.004, "WaveSpeed background remover — $0.004 a picture"),
        "motion": (0.0, "drawn on your computer — free"),
        "vox": (4 * collage, f"about 4 pictures a scene at ${collage:.3f}, one scene a minute"),
        "maps": (0.0, "drawn on your computer — free"),
        "spotlight": (0.006, "$0.004 a photo for the cutout"),
        "objects": (0.012, "$0.004 a cutout; a drawn studio photo when no real photo cuts out"),
        "headlines": (0.0, "real pages, captured on your computer — free"),
        "sound": (0.0, "synthesised on your computer — free; the music is yours"),
    }
    return {"voice": {"per_min": v[0], "note": v[1]}, "picture": picture,
            "features": {k: {"per_min": round(c, 4), "note": n} for k, (c, n) in feats.items()}}


def resolve(style_info: dict, asked: dict = None) -> dict:
    """The switches for one job: the channel's defaults, overridden by what Options sent, with
    anything the channel does not offer — or whose API is missing — turned off."""
    fcfg = (style_info or {}).get("features") or {}
    offered = [k for k in (fcfg.get("offered") or KEYS) if k in BY_KEY]
    on = set(fcfg.get("on") or offered)
    caps = capabilities()["features"]
    out = {}
    for k in KEYS:
        want = bool(asked[k]) if isinstance(asked, dict) and k in asked else (k in on)
        out[k] = bool(want and k in offered and caps.get(k, {}).get("ok", True))
    if not out.get("ai_images") and "ai_images" in offered:
        out["depth"] = False            # depth pop lives on AI pictures
    return out
