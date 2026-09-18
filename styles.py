"""styles.py — one JSON file per channel, loaded into the engine at import.

WHY THIS EXISTS
---------------
A channel's identity lives in a dozen different places: the script prompt, the
display font, the accent palette, how often a graphic appears, how the stock
footage is graded, which words light up in a caption. Scattered across the
engine, changing a channel meant editing five files and hoping you found them
all — and two channels could never be told apart cleanly.

Here, one channel is one file: `styles/<name>.json`. This module reads those
files and writes their values into the tables the engine already reads from, so
the engine itself stays untouched and a new channel is a new JSON file.

    styles/jung.json        the worked example, ready to render
    styles/_TEMPLATE.json   copy this to start your own

To add a channel: copy the template, fill it in (or ask Claude Code to fill it
in from a description of your channel), and it appears in the app.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STYLE_DIR = HERE / "styles"
ASSETS = HERE / "assets"

# Every loaded style, keyed by its `name`. Populated by load_all().
STYLES: dict = {}


def _files() -> list:
    """Style files, skipping the template and anything starting with a dot — the channels, then their variants
    (styles/variants/: a look a channel switches to by itself when the title calls for it, never listed)."""
    if not STYLE_DIR.is_dir():
        return []
    ok = lambda f: not f.name.startswith(("_", "."))
    return (sorted(f for f in STYLE_DIR.glob("*.json") if ok(f))
            + sorted(f for f in (STYLE_DIR / "variants").glob("*.json") if ok(f)))


def load_all() -> dict:
    """Read every style file. A broken file is skipped with a message, not a crash."""
    STYLES.clear()
    for f in _files():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            print(f"styles: skipping {f.name} — {e}")
            continue
        name = data.get("name") or f.stem
        data["name"] = name
        STYLES[name] = data
    return STYLES


def apply_to_engine(make_video, motion) -> list:
    """Push every loaded style into the tables the engine reads.

    Called once at startup, before any job runs. Returns the names applied so a
    caller can report what is available.
    """
    if not STYLES:
        load_all()

    # Any prompt that needs to describe the channel reads this.
    make_video.STYLE_INFO.clear()
    make_video.STYLE_INFO.update(STYLES)

    for name, st in STYLES.items():
        look = st.get("look") or {}
        pace = st.get("pacing") or {}

        # ── prompts ────────────────────────────────────────────────────────
        pr = st.get("prompts") or {}
        make_video.prompts.PROMPT_SETS[name] = {
            "label": st.get("label", name),
            "outline": pr.get("outline", ""),
            "script": pr.get("script", ""),
        }

        # ── language ───────────────────────────────────────────────────────
        # Only non-English is recorded; English is the engine's default and
        # writing it here would make every prompt carry a redundant instruction.
        # Recorded for EVERY channel, English included: the prompt states the
        # language outright, so "missing means English" is no longer good
        # enough — a channel has to be able to say so.
        make_video.STYLE_LANGUAGE[name] = (st.get("language") or "English").strip()

        # ── look: type, colour, ground ─────────────────────────────────────
        if look.get("title_font"):
            motion.BRAND[name] = {
                "title_font": look["title_font"],
                "title_weight": str(look.get("title_weight", "900")),
                "accent": look.get("accent") or ["#E8A33D"],
                "warn": look.get("warn", "#C4553F"),
                "good": look.get("good", "#2F8F83"),
            }
        skins = look.get("skins") or []
        if skins:
            motion.STYLE_SKINS[name] = list(skins)
        if look.get("caption_accent"):
            make_video.CHANNEL_ACCENT[name] = look["caption_accent"]
        if look.get("footage_grade"):
            make_video.FOOTAGE_GRADE[name] = look["footage_grade"]

        # Paths in a style file are relative to assets/ so a style stays
        # portable — someone else's machine has a different absolute path.
        ground = (look.get("ground") or "").strip()
        if ground:
            p = ASSETS / ground
            if p.exists():
                motion.STYLE_GROUND[name] = p
            else:
                print(f"styles: {name}: ground '{ground}' not found, using none")
        people = (look.get("people_dir") or "").strip()
        if people:
            p = ASSETS / people
            if p.is_dir():
                motion.STYLE_PEOPLE[name] = p

        # A dark ground changes how stickers and cards are drawn, and some
        # channels want their captions centred rather than sitting low.
        if look.get("dark") or any(k in str(skins) for k in ("dark", "noir", "glass", "astro")):
            motion.DARK_STYLES.add(name)
        if look.get("center_subtitles"):
            make_video.CENTER_SUB_STYLES.add(name)

        # ── pacing ─────────────────────────────────────────────────────────
        if pace.get("graphic_every_min"):
            make_video.STYLE_EVERY_MIN[name] = float(pace["graphic_every_min"])
        if pace.get("wpm"):
            make_video.STYLE_WPM[name] = float(pace["wpm"])
        else:
            make_video.STYLE_WPM.pop(name, None)
        if pace.get("graphic_ratio"):
            make_video.STYLE_MOTION_RATIO[name] = float(pace["graphic_ratio"])
        if pace.get("white_fade") is not None:
            make_video.STYLE_WHITE_FADE[name] = float(pace["white_fade"])

        # ── story channels: one cast, one camera move, one caption look ────
        cast = look.get("character")
        if cast:
            make_video.STYLE_CHARACTER[name] = cast if isinstance(cast, str) else "\n".join(cast)
        if isinstance(look.get("captions"), dict):
            make_video.STYLE_CAPTIONS[name] = dict(look["captions"])
        if look.get("zoom"):
            make_video.STYLE_ZOOM[name] = str(look["zoom"]).lower()
        # Pictures-only story channels: no graphics, a still per beat, placed on its line.
        if pace.get("graphics") is False:
            make_video.STYLE_NO_GRAPHICS.add(name)
        else:
            make_video.STYLE_NO_GRAPHICS.discard(name)
        if pace.get("punch") is False:
            make_video.STYLE_NO_PUNCH.add(name)
        else:
            make_video.STYLE_NO_PUNCH.discard(name)
        if pace.get("photo_every_s"):
            make_video.STYLE_PHOTO_EVERY[name] = float(pace["photo_every_s"])
        if look.get("placement"):
            make_video.STYLE_PLACEMENT[name] = str(look["placement"]).lower()
        if look.get("character_image"):
            make_video.STYLE_CHARACTER_IMAGE[name] = str(look["character_image"])
        # encoder settings for the finished video (flat artwork needs a careful encoder)
        if isinstance(look.get("encode"), dict):
            make_video.STYLE_ENCODE[name] = dict(look["encode"])
        else:
            make_video.STYLE_ENCODE.pop(name, None)
        # a clean drawn look: no film dust, no dark vignette
        for key, table in (("dust", make_video.STYLE_NO_DUST), ("vignette", make_video.STYLE_NO_VIGNETTE),
                           ("leak", make_video.STYLE_NO_LEAK)):
            if look.get(key) is False:
                table.add(name)
            else:
                table.discard(name)
        # A channel can ship its own slider positions; the UI still overrides them.
        m = pace.get("mix") or st.get("mix")
        if isinstance(m, (list, tuple)) and len(m) == 3:
            try:
                make_video.STYLE_MIX[name] = tuple(float(x) for x in m)
            except (TypeError, ValueError):
                pass

        # ── stock footage ──────────────────────────────────────────────────
        q = [x for x in (st.get("stock_queries") or []) if not x.startswith("<<")]
        if q:
            make_video.STYLE_PEXELS_QUERIES[name] = q

        # ── voice ──────────────────────────────────────────────────────────
        v = st.get("voice") or {}
        if v.get("voice_id"):
            make_video.STYLE_VOICE[name] = v["voice_id"]
        # Which service speaks, and — on Algrow — which engine. Cleared when a
        # style drops the field, so an edited file cannot leave a stale choice.
        prov = (v.get("provider") or "").strip().lower()
        if prov in ("algrow", "ai33", "wavespeed"):
            make_video.STYLE_VOICE_PROVIDER[name] = prov
        else:
            make_video.STYLE_VOICE_PROVIDER.pop(name, None)
        if (v.get("model") or "").strip():
            make_video.STYLE_VOICE_MODEL[name] = v["model"].strip()
        else:
            make_video.STYLE_VOICE_MODEL.pop(name, None)
        if v.get("chunk_chars"):
            make_video.STYLE_VOICE_CHUNK[name] = int(v["chunk_chars"])
        else:
            make_video.STYLE_VOICE_CHUNK.pop(name, None)
        eng = (v.get("engine") or "").strip().lower()
        if eng in ("elevenlabs", "stealth", "minimax"):
            make_video.STYLE_VOICE_ENGINE[name] = eng
        else:
            make_video.STYLE_VOICE_ENGINE.pop(name, None)

    # Scene length and its ceiling are global, not per-channel — but a style may
    # still nudge them, so the last word goes to whichever style is rendering.
    # (Set per job in make_video.run_pipeline; these are the defaults.)
    first = next(iter(STYLES.values()), None)
    if first:
        pace = first.get("pacing") or {}
        if pace.get("graphic_dur_s"):
            make_video.MOTION_DEFAULT_DUR = float(pace["graphic_dur_s"])
        if pace.get("graphic_max_s"):
            make_video.MOTION_MAX_DUR = float(pace["graphic_max_s"])

    return sorted(STYLES)


def catalogue() -> list:
    """[{name, label, description, language}] for the UI's channel picker."""
    if not STYLES:
        load_all()
    return [{"name": n,
             "label": s.get("label", n),
             "description": s.get("description", ""),
             # the card shows the tagline and the points as bullets; a style without points shows the description
             "tagline": s.get("tagline", ""),
             "points": [str(x).strip() for x in (s.get("points") or []) if str(x).strip()],
             "language": s.get("language", "English"),
             # what the UI needs to show a channel as itself: its own slider
             # positions, its cast, and a picture of the look it is going for
             "mix": (s.get("pacing") or {}).get("mix") or s.get("mix") or None,
             "character": ((s.get("look") or {}).get("character") or ""),
             "reference": bool(((s.get("look") or {}).get("reference") or "")),
             # a video of what the channel makes (`look.sample`, under assets/): the card plays it
             "sample": bool(((s.get("look") or {}).get("sample") or "")),
             # the channel's own character picture (a path under assets/), and
             # whether it is a pictures-only channel — Options hides the sliders then
             "character_image": ((s.get("look") or {}).get("character_image") or ""),
             "graphics": (s.get("pacing") or {}).get("graphics") is not False,
             "photo_every_s": (s.get("pacing") or {}).get("photo_every_s") or None,
             "options_note": ((s.get("look") or {}).get("options_note") or ""),
             # what Options offers for this channel, what is ticked by default, what it costs
             "features": s.get("features") or {},
             "cost": s.get("cost", ""),
             "render_x": float(s.get("render_x") or 1.6),   # minutes of rendering per minute of video, roughly
             # AI pictures a minute: a story draws one every few seconds, other channels a couple a minute
             "pictures_per_min": round(60.0 / float((s.get("pacing") or {}).get("photo_every_s")), 1)
                                 if (s.get("pacing") or {}).get("photo_every_s") else 2.5,
             "wpm": float((s.get("pacing") or {}).get("wpm") or 140),
             "order": s.get("order", 99)}
            for n, s in sorted(STYLES.items(), key=lambda kv: (kv[1].get("order", 99), kv[1].get("label", kv[0])))
            if not s.get("variant_of")]
