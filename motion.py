#!/usr/bin/env python3
"""motion.py — MOTION GRAPHICS scenes rendered to mp4 (Chromium + ffmpeg).

Replaces the old mindmap segments. Every ~60 s of a video gets ONE animated
scene that VISUALISES what the narrator says in that minute: a bar chart, a
crowd of people filling in, cards sliding on, a checklist ticking, a counting
statistic, an icon flow with arrows...

Design notes
------------
* Deterministic: nothing uses CSS animations or Date.now(). The page exposes
  `window.renderFrame(t)` and every element's transform/opacity is computed
  from `t` alone, so Chromium screenshots are reproducible frame by frame.
  (Same contract as mindmap.py, so the render loop is nearly identical.)
* Icons are inline SVG paths authored here — no icon font to install and no
  missing-glyph tofu boxes.
* Two skins: "paper" (beige crumpled paper, flat teal/navy/amber — the
  explainer look) and "clean" (near-white, blue accents, soft shadows — the
  product-UI look). Scenes rotate between them so a video never feels samey.
"""

import difflib
import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FPS = 30
W, H = 1920, 1080

# Playwright's sync API races itself when several threads spin up the driver
# at the same moment ('PlaywrightContextManager has no attribute _playwright').
# Only the START is racy, so serialise just that and let the renders run free.
_PW_START = threading.Lock()

# ════════════════════════════════════════════════════════════════════════════
# SKINS
# ════════════════════════════════════════════════════════════════════════════
SKINS = {
    # The same frosted-glass grammar as "glass", re-lit for the vintage brand:
    # warm charcoal instead of navy, the channel's gold/brick/teal accents. This
    # is what keeps a modern scene from feeling pasted in from another channel.
    "noir": {
        "bg": "#171310", "bg2": "#0A0806", "paper": False, "glass": True,
        "deco": "grid_warm",
        "ink": "#F4EDDF", "ink_soft": "#B3A78F",
        "warn": "#E05545", "good": "#2F8F83",
        "accent": ["#E8A33D", "#E05545", "#2F8F83", "#D9C9A6", "#8A6A2F"],
        "card": "rgba(255,236,200,0.07)", "card_line": "rgba(244,237,223,0.22)",
        "title_font": "'Inter Display Black','Inter Black',Inter,sans-serif",
        "title_weight": "900",
    },
    # The modern dark look from the reference edits: deep navy, frosted glass
    # cards, electric accents. Mixed into every style's rotation so the vintage
    # brand keeps its paper — and every third scene feels like this decade.
    "glass": {
        "bg": "#0B1220", "bg2": "#040A14", "paper": False, "glass": True,
        "deco": "grid",
        "ink": "#F3F6FB", "ink_soft": "#9FB0C8",
        "warn": "#EF4444", "good": "#10B981",
        "accent": ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"],
        "card": "rgba(148,178,255,0.10)", "card_line": "rgba(255,255,255,0.28)",
        "title_font": "'Inter Display Black','Inter Black',Inter,sans-serif",
        "title_weight": "900",
    },
    # The explainer look: warm paper, flat navy/teal/amber shapes.
    "paper": {
        "good": "#2f8f83",
        "warn": "#c4553f",
        "bg": "#e9dcc0", "bg2": "#dccfae", "paper": True,
        "ink": "#1f3a52", "ink_soft": "#5a6f80",
        "accent": ["#1c4b66", "#e8a33d", "#2f8f83", "#c4553f", "#3d6b8c"],
        "card": "#f2e8d2", "card_line": "#c9b894",
        "title_font": "Montserrat, 'Helvetica Neue', sans-serif",
        "title_weight": "800",
    },
    # The product-UI look: bright, blue, soft shadows.
    "clean": {
        "good": "#059669",
        "warn": "#dc2626",
        "bg": "#f4f6fb", "bg2": "#ffffff", "paper": False,
        "ink": "#111827", "ink_soft": "#6b7280",
        "accent": ["#1a56ff", "#3b82f6", "#0ea5e9", "#6366f1", "#111827"],
        "card": "#ffffff", "card_line": "#e5e7eb",
        "title_font": "Montserrat, 'Helvetica Neue', sans-serif",
        "title_weight": "800",
    },
    # ASTROLOGY: deep indigo/violet night, gold starlight, lavender + rose.
    # Celestial decoration (starfield + a slow drifting glow) is drawn behind.
    "astro": {
        "good": "#7fd4e8",
        "warn": "#e0645f",
        "bg": "#150e2b", "bg2": "#2a1a4d", "paper": False, "deco": "stars",
        "ink": "#f2ebff", "ink_soft": "#b9a9d9",
        "accent": ["#e8c46a", "#b79ce8", "#e88fb0", "#7fd4e8", "#c9a3f0"],
        "card": "#241640", "card_line": "#4a3370",
        "title_font": "'Cormorant Garamond', 'EB Garamond', Georgia, serif",
        "title_weight": "700",
    },
    # DIVINE: the warm paper look, lifted by light — soft gold rays
    # behind the content and a brighter, luminous cream ground.
    "divine": {
        "good": "#3f6b5a",
        "warn": "#a5372a",
        "bg": "#efe4cb", "bg2": "#fdf7e8", "paper": True, "deco": "rays",
        "ink": "#3b2f1d", "ink_soft": "#7d6a4c",
        "accent": ["#b8862b", "#3f6b5a", "#a5562f", "#6b5a86", "#8a7231"],
        "card": "#fbf4e2", "card_line": "#d8c49a",
        "title_font": "Montserrat, 'Helvetica Neue', sans-serif",
        "title_weight": "800",
    },
    # MYSTIC: deep teal-black ground, warm brass and
    # ivory — old-teacher authority rather than cosmic sparkle.
    "mystic": {
        "good": "#4fa89a",
        "warn": "#cf5b3a",
        "bg": "#101d21", "bg2": "#1b3239", "paper": False, "deco": "dust",
        "ink": "#f2ece0", "ink_soft": "#a8b6b5",
        "accent": ["#d9a441", "#4fa89a", "#cf7a4a", "#8fb4c9", "#b9905f"],
        "card": "#18292e", "card_line": "#2f4a51",
        "title_font": "Montserrat, 'Helvetica Neue', sans-serif",
        "title_weight": "800",
    },
    # COLLAGE: the real aged-paper scan with black halftone cut-outs
    # pasted on it and handwritten annotation — the scrapbook look.
    "collage": {
        "bg": "#d9c9a6", "bg2": "#e8dcc0", "paper": False, "deco": "",
        "warn": "#8f2f22", "good": "#2f6b4f",
        "ink": "#241c12", "ink_soft": "#6b5a44",
        "accent": ["#1a1a1a", "#8f2f22", "#2f6b4f", "#8a6a2f", "#3a4a6b"],
        "card": "#efe4c8", "card_line": "#b9a37a",
        "title_font": "'Permanent Marker', 'Shadows Into Light', cursive",
        "title_weight": "400",
    },
    # CARL JUNG COLLAGE: the same scrapbook, glued into a black notebook. The
    # cut-outs sit on paper cards so the halftone does not vanish into the page.
    "collage_dark": {
        "bg": "#0d0d10", "bg2": "#17171c", "paper": False, "deco": "dust",
        "warn": "#b5544a", "good": "#7d9a86",
        "ink": "#ece5d6", "ink_soft": "#9a9184",
        "accent": ["#ece5d6", "#b5544a", "#7d9a86", "#c2a35f", "#7f8fa6"],
        "card": "#1a1a20", "card_line": "#33333c",
        # Permanent Marker has no ě/č/ř/ů/ť/ď/ň at all — Czech titles were being
        # rendered with fallback glyphs mid-word. Caveat covers the full set.
        "title_font": "Caveat, 'Shadows Into Light', cursive",
        "title_weight": "700",
        "hand_font": "Caveat, cursive",
    },
    # CARL JUNG: near-black, cream serif, heavy vignette — the shadow-work look.
    "dark": {
        "good": "#6e7f8d",
        "warn": "#9c4038",
        "bg": "#08080a", "bg2": "#15151a", "paper": False, "deco": "dust",
        "ink": "#e8e2d6", "ink_soft": "#9a938a",
        "accent": ["#c9b896", "#8b7355", "#6e7f8d", "#a3564a", "#7d6f5f"],
        "card": "#141418", "card_line": "#2e2e36",
        "title_font": "'EB Garamond', 'Cormorant Garamond', Georgia, serif",
        "title_weight": "600",
    },
}
SKIN_ROTATION = ["paper", "clean", "paper", "paper", "clean", "paper"]
# Each narrator style pins its own look, so a video never mixes palettes.
# ── BRAND ──────────────────────────────────────────────────────────────────
# A SKIN decides the ground: paper, frosted glass, night. A BRAND decides the
# type and the accent colours — and a brand is fixed for a whole channel, so
# every scene of a video reads as one edit.
#
# Before this, a jung video ran two scenes of `collage_dark` (Caveat cursive,
# cream/brick/sage) and then one of `noir` (Inter Black, gold/teal): different
# lettering and a different palette every third graphic, which is exactly what
# "too many fonts and colours" looked like on screen.
BRAND: dict = {}          # type + palette per channel, from styles/<name>.json


def brand_skin(skin: dict, style: str) -> dict:
    """`skin` re-lit in the channel's type and colour. Ground is left alone."""
    b = BRAND.get(style or "")
    return {**skin, **b} if b else skin


STYLE_SKINS: dict = {}    # which grounds a channel rotates through

# ════════════════════════════════════════════════════════════════════════════
# ICONS  —  simple flat SVG, drawn in a 100x100 box, single `currentColor` fill
# ════════════════════════════════════════════════════════════════════════════
ICONS = {
    "person":   "M50 18a16 16 0 1 1 0 32 16 16 0 0 1 0-32zM22 92V74c0-12 12-20 28-20s28 8 28 20v18z",
    "people":   "M32 22a13 13 0 1 1 0 26 13 13 0 0 1 0-26zM68 22a13 13 0 1 1 0 26 13 13 0 0 1 0-26zM6 90V76c0-10 10-16 26-16s26 6 26 16v14zM60 90V76c0-6-3-11-8-14 4-1 9-2 16-2 16 0 26 6 26 16v14z",
    "bank":     "M50 10 92 34v8H8v-8zM18 50h10v30H18zM40 50h10v30H40zM62 50h10v30H62zM84 50h-6v30h6zM8 84h84v8H8z",
    "card":     "M8 24h84a6 6 0 0 1 6 6v6H2v-6a6 6 0 0 1 6-6zM2 46h96v24a6 6 0 0 1-6 6H8a6 6 0 0 1-6-6zM12 58h24v8H12z",
    "money":    "M6 24h88v52H6zM50 36a14 14 0 1 1 0 28 14 14 0 0 1 0-28zM16 34h8v8h-8zM76 58h8v8h-8z",
    "coins":    "M34 20c15 0 27 5 27 11s-12 11-27 11S7 37 7 31 19 20 34 20zM7 42c5 5 15 8 27 8s22-3 27-8v10c0 6-12 11-27 11S7 58 7 52zM66 44c15 0 27 5 27 11s-12 11-27 11-27-5-27-11 12-11 27-11zM39 66c5 5 15 8 27 8s22-3 27-8v10c0 6-12 11-27 11s-27-5-27-11z",
    "chart":    "M10 88V44h18v44zM40 88V20h18v68zM70 88V56h18v32zM6 92h88v6H6z",
    "trend":    "M8 76 36 48l16 16 30-32 12 12V20H62l8 8-18 20-16-16-32 32z",
    "clock":    "M50 8a42 42 0 1 1 0 84 42 42 0 0 1 0-84zm0 12a30 30 0 1 0 0 60 30 30 0 0 0 0-60zm-4 10h8v22l16 10-4 7-20-12z",
    "calendar": "M22 12h10v10H22zM68 12h10v10H68zM10 24h80v10H10zM10 42h80v46H10zm12 10v10h12V52zm22 0v10h12V52zm22 0v10h12V52zM22 68v10h12V68zm22 0v10h12V68z",
    "doc":      "M22 6h38l20 20v68H22zM58 8v20h20zM32 46h36v7H32zM32 60h36v7H32zM32 74h24v7H32z",
    "box":      "M50 8 92 26 50 44 8 26zM8 34l38 17v41L8 74zM92 34v40L54 92V51z",
    "house":    "M50 12 94 48h-12v42H62V64H38v26H18V48H6z",
    "star":     "M50 8l12 26 28 3-21 19 6 28-25-14-25 14 6-28L10 37l28-3z",
    "heart":    "M50 88C22 68 8 54 8 38c0-13 10-22 22-22 8 0 15 4 20 11 5-7 12-11 20-11 12 0 22 9 22 22 0 16-14 30-42 50z",
    "eye":      "M50 22c24 0 42 18 46 28-4 10-22 28-46 28S8 60 4 50c4-10 22-28 46-28zm0 12a16 16 0 1 0 0 32 16 16 0 0 0 0-32z",
    "lock":     "M30 42V32a20 20 0 0 1 40 0v10h8v50H22V42zm12 0h16V32a8 8 0 0 0-16 0z",
    "key":      "M64 8a28 28 0 1 1-26 38l-4 4H22v12H10v12H-2V58l40-40a28 28 0 0 1 26-10zm6 14a8 8 0 1 0 0 16 8 8 0 0 0 0-16z",
    "bulb":     "M50 6c17 0 30 13 30 29 0 11-6 18-11 24-3 4-5 7-5 11H36c0-4-2-7-5-11-5-6-11-13-11-24C20 19 33 6 50 6zM38 78h24v8H38zM42 90h16v6H42z",
    "warning":  "M50 8 96 90H4zm-6 26h12v34H44zm0 42h12v12H44z",
    "check":    "M50 6a44 44 0 1 1 0 88 44 44 0 0 1 0-88zm22 28-8-8-20 20-8-8-8 8 16 16z",
    "cross":    "M50 6a44 44 0 1 1 0 88 44 44 0 0 1 0-88zm16 22L50 44 34 28l-6 6 16 16-16 16 6 6 16-16 16 16 6-6-16-16 16-16z",
    "search":   "M42 8a34 34 0 0 1 26 56l26 26-8 8-26-26A34 34 0 1 1 42 8zm0 12a22 22 0 1 0 0 44 22 22 0 0 0 0-44z",
    "bell":     "M50 6c4 0 7 3 7 7v3c14 3 21 15 21 30 0 14 4 18 8 22v6H14v-6c4-4 8-8 8-22 0-15 7-27 21-30v-3c0-4 3-7 7-7zM38 78h24a12 12 0 0 1-24 0z",
    "chat":     "M10 14h80v52H54L30 86V66H10zM26 32h48v8H26zM26 46h32v8H26z",
    "gear":     "M42 6h16l3 12 9 5 12-5 8 14-9 8v10l9 8-8 14-12-5-9 5-3 12H42l-3-12-9-5-12 5-8-14 9-8V40l-9-8 8-14 12 5 9-5zm8 26a18 18 0 1 0 0 36 18 18 0 0 0 0-36z",
    "shield":   "M50 6 88 20v30c0 22-16 36-38 44C28 86 12 72 12 50V20z",
    "globe":    "M50 6a44 44 0 1 1 0 88 44 44 0 0 1 0-88zm0 10c-6 0-14 12-16 30h32c-2-18-10-30-16-30zM22 46c1-11 5-20 10-25-9 5-16 14-19 25zM16 54c1 11 6 21 14 27-4-6-8-16-9-27zm18 0c2 18 10 30 16 30s14-12 16-30zm44 0c-1 11-4 20-9 26 8-6 13-15 14-26zm3-8c-3-11-10-20-19-25 5 5 9 14 10 25z",
    "moon":     "M58 6a44 44 0 1 0 36 62A38 38 0 0 1 58 6z",
    "sun":      "M50 28a22 22 0 1 1 0 44 22 22 0 0 1 0-44zM46 2h8v16h-8zM46 82h8v16h-8zM2 46h16v8H2zM82 46h16v8H82zM16 22l6-6 11 11-6 6zM67 73l6-6 11 11-6 6zM73 27 84 16l6 6-11 11zM22 78l-6-6 11-11 6 6z",
    "fire":     "M50 4c4 18 22 26 22 44 0 20-14 34-22 46-8-12-22-26-22-46 0-18 18-26 22-44zm0 34c-6 8-10 14-10 22 0 8 5 14 10 20 5-6 10-12 10-20 0-8-4-14-10-22z",
    "bolt":     "M56 4 20 56h20l-8 40 40-56H50z",
    "seed":     "M50 92V52C50 30 68 12 92 10c0 24-18 42-42 42M50 62C50 44 36 30 16 28c0 20 14 34 34 34",
    "brain":    "M50 10c18 0 32 12 32 28 0 8-3 14-8 19v13H26V57c-5-5-8-11-8-19 0-16 14-28 32-28zm-4 14v40h8V24zM34 78h32v8H34zM38 90h24v6H38z",
    "road":     "M30 8h14v84H30zM56 8h14v84H56zM46 8h8v14h-8zM46 34h8v14h-8zM46 60h8v14h-8z",
    "target":   "M50 6a44 44 0 1 1 0 88 44 44 0 0 1 0-88zm0 14a30 30 0 1 0 0 60 30 30 0 0 0 0-60zm0 14a16 16 0 1 1 0 32 16 16 0 0 1 0-32z",
    "trophy":   "M26 10h48v22c0 16-10 28-24 28S26 48 26 32zM14 14h10v14c0 6-10 6-10-2zM86 14H76v14c0 6 10 6 10-2zM44 62h12v14H44zM30 80h40v10H30z",
    "book":     "M14 10h30c6 0 6 6 6 6v70s0-6-6-6H14zM86 10H56c-6 0-6 6-6 6v70s0-6 6-6h30z",
    "phone":    "M28 6h44a8 8 0 0 1 8 8v72a8 8 0 0 1-8 8H28a8 8 0 0 1-8-8V14a8 8 0 0 1 8-8zm14 6v4h16v-4zM42 78h16v6H42z",
    "arrow":    "M6 42h60V24l30 26-30 26V58H6z",
}
ICON_ROTATION = ["person", "chart", "bulb", "clock", "star", "shield", "trend",
                 "target", "heart", "eye", "key", "globe", "brain", "fire"]

# ════════════════════════════════════════════════════════════════════════════
# ASSET LIBRARY  —  the real cut-out stickers + paper grounds in assets/
# ════════════════════════════════════════════════════════════════════════════
_ASSETS = Path(__file__).resolve().parent / "assets"
CUTOUT_DIR = _ASSETS / "cutouts"
PAPER_BG = _ASSETS / "vintage_old_paper_background.jpg"


def cutouts() -> dict:
    """{short name: path} for every keyed sticker. Names are the filenames with
    the 'vintage_' prefix dropped, so a scene can just ask for 'brain' or 'key'."""
    if not CUTOUT_DIR.is_dir():
        return {}
    # Some assets are BACKINGS meant to sit under an icon, not artwork in their
    # own right — pasting them as a sticker just puts a blank card on the page.
    backings = {"blank_vintage_label_sticker", "vintage_paper_clip_sticker", "vintage_pin"}
    out = {}
    for p in sorted(CUTOUT_DIR.glob("*.png")):
        if p.stem in backings:
            continue
        out[p.stem.replace("vintage_", "").replace(" ", "_").lower()] = p
    return out


CUTOUTS = cutouts()


# A channel that wants real portraits on screen points its style file at a
# folder (look.people_dir). Nothing is hard-coded, so adding a channel never
# means editing this file.
STYLE_PEOPLE: dict = {}


def person_photos(style: str) -> list:
    """Portraits / documents for a channel, or [] when it ships none.

    Resolution order: whatever the style file named, then assets/people/<style>/
    as a convention, then nothing. Templates that want a face degrade to their
    no-photo layout, so an empty folder is a valid way to run.
    """
    d = STYLE_PEOPLE.get(style)
    if d is None:
        for c in (f"people/{style}", style):
            if (_ASSETS / c).is_dir():
                d = _ASSETS / c
                break
    d = Path(d) if d else None
    if d is not None and not d.is_dir():
        d = None
    if d is None:
        return []
    files = sorted(p for p in d.iterdir()
                   if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    # Put actual PORTRAITS first: the opener wants the teacher's face, and plain
    # alphabetical order handed it a book cover instead.
    def rank(p):
        n = p.stem.lower()
        if any(w in n for w in ("portrait", "profile", "smiling", "face", "headshot")):
            return 0
        if any(w in n for w in ("book", "cover", "newspaper", "diagram", "redbook")):
            return 2
        return 1
    return sorted(files, key=lambda p: (rank(p), p.name))


# Words the model reaches for that the library holds under another name.
CUTOUT_ALIASES = {
    "mirror": "eye", "reflection": "eye", "face": "man_mad", "mask": "man_mad",
    "person": "man_mad", "man": "man_mad", "woman": "angry_woman_yelling",
    "clock": "alarm", "time": "alarm", "calendar": "alarm", "morning": "alarm",
    "evening": "moon", "night": "moon", "sleep": "moon",
    "book": "open_book", "bible": "holy_bible", "scripture": "holy_bible",
    "word": "holy_bible", "study": "stack_of_books", "knowledge": "stack_of_books",
    "mind": "brain", "thought": "brain", "thinking": "brain", "memory": "brain",
    "door": "key", "access": "key", "authority": "key", "permission": "key",
    "closed": "lock", "resistance": "lock", "blocked": "lock", "prison": "lock",
    "love": "heart", "grief": "heartbroken", "loss": "heartbroken", "pain": "heartbroken",
    "question": "question_mark", "doubt": "question_mark", "confusion": "question_mark",
    "search": "magnifying_glass_hand", "looking": "magnifying_glass_hand",
    "growth": "hand_watering_can", "change": "recycle", "start": "rocket",
    "hope": "star", "sign": "star", "peace": "collage_bird_dove", "spirit": "collage_bird_dove",
    "church": "church", "prayer": "holy_bible", "burden": "sisyphus_pushing_boulder_sketch",
    "struggle": "sisyphus_pushing_boulder_sketch", "effort": "sisyphus_pushing_boulder_sketch",
    "voice": "man_talking_microfon", "speaking": "man_talking_microfon",
    "waste": "bin", "discard": "bin", "letting_go": "bin",
    "fear": "ghost", "shadow": "ghost", "unknown": "ghost",
}

# Stickers that belong to one kind of channel — a faith channel, here. Used when
# a scene names them; never used to fill a gap (see pick_cutouts).
NICHE_CUTOUTS = {"bible", "holy_bible", "jesus", "jesus_and_disciples_drawing",
                 "rising_jesus_drawing", "church"}
# "word" pointed at the Holy Bible (a sermon's "the Word"). In any other script
# it is one of the commonest nouns there is, so it goes.
CUTOUT_ALIASES.pop("word", None)


def pick_cutouts(names: list, n: int, seed: int = 0, strict: bool = False) -> list:
    """Resolve requested sticker names to real files.

    `strict` returns ONLY genuine matches. Padding a scene with whatever was
    left over put a Bible above the caption "Mirror" and a key above "Calendar" —
    a wrong picture is worse than no picture, so callers use strict mode and
    drop the scene to a text layout when too few stickers actually fit.
    """
    got, used = [], set()
    for nm in names or []:
        k = str(nm).strip().lower().replace(" ", "_").replace("-", "_")
        k = k if k in CUTOUTS else CUTOUT_ALIASES.get(k, "")
        if k in CUTOUTS:
            # a deliberate repeat (three identical pillars) must survive
            got.append(k); used.add(k)
    if strict:
        return got[:n]
    # Filler never reaches for a sticker that belongs to one kind of channel.
    # The library is alphabetical, so an unmatched opener was padded with the
    # Bible and three drawings of Jesus — on a psychology channel. They stay
    # available to any scene that asks for them by name.
    pool = [k for k in CUTOUTS if k not in used and k not in NICHE_CUTOUTS]
    i = seed
    while len(got) < n and pool:
        k = pool[i % len(pool)]
        if k not in used:
            got.append(k); used.add(k)
        i += 1
    return got[:n]


# ════════════════════════════════════════════════════════════════════════════
# HTML  —  one self-contained page per scene
# ════════════════════════════════════════════════════════════════════════════
_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>__FONTS__
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px;overflow:hidden;background:__BG__}
#layer{position:absolute;inset:0;transform-origin:50% 50%;will-change:transform}
#stage{position:relative;width:__W__px;height:__H__px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 40%, __BG2__ 0%, __BG__ 78%)}
#ground{position:absolute;inset:0;background-size:cover;background-position:center;
  pointer-events:none}
#grain{position:absolute;inset:0;pointer-events:none}
#deco{position:absolute;inset:0;pointer-events:none;overflow:hidden}
#deco>div{position:absolute}
.el{position:absolute;will-change:transform,opacity}
.tx{font-family:__TFONT__;color:__INK__;line-height:1.14;letter-spacing:-.5px}
.soft{color:__INKSOFT__}
svg{display:block;overflow:visible}
/* the two layers that move get promoted so the GPU slides a texture
   instead of the CPU repainting them at each new subpixel offset */
#ground,#deco{will-change:transform;transform-origin:50% 50%;backface-visibility:hidden}
#layer{transform:none}
</style></head><body>
<div id="stage">
  <svg id="grain" width="__W__" height="__H__">
    <defs>
      <filter id="fgrain"><feTurbulence type="fractalNoise" baseFrequency="0.9"
        numOctaves="4" seed="11" stitchTiles="stitch"/>
        <feColorMatrix type="saturate" values="0"/></filter>
      <filter id="fcrump"><feTurbulence type="fractalNoise" baseFrequency="0.006"
        numOctaves="5" seed="4" stitchTiles="stitch"/>
        <feColorMatrix type="saturate" values="0"/>
        <feComponentTransfer><feFuncA type="linear" slope="1.4" intercept="-.2"/></feComponentTransfer></filter>
    </defs>
    <rect id="crump" width="100%" height="100%" filter="url(#fcrump)" opacity="0"
          style="mix-blend-mode:multiply"/>
    <rect id="gr" width="100%" height="100%" filter="url(#fgrain)" opacity="0"
          style="mix-blend-mode:multiply"/>
  </svg>
  <div id="ground"></div>
  <div id="deco"></div>
  <div id="layer"></div>
</div>
<script>
const SCENE = __SCENE__;
const SK = __SKIN__;
const W = __W__, H = __H__;
const stage = document.getElementById('layer');
/* The camera moves the PICTURE, never the type — see renderFrame. */
const camEls = [document.getElementById('ground'), document.getElementById('deco')];
if(SCENE.ground){
  const g=document.getElementById('ground');
  g.style.backgroundImage=`url(${SCENE.ground})`;
}

/* ── deterministic helpers (no Date.now / no CSS animation) ───────────── */
const cl=(x,a,b)=>Math.max(a,Math.min(b,x));
const seg=(t,s,d)=>cl((t-s)/Math.max(.0001,d),0,1);          // 0..1 window
const eOut=p=>1-Math.pow(1-p,3);                              // easeOutCubic
const eBack=p=>{const c=1.7;return 1+(c+1)*Math.pow(p-1,3)+c*Math.pow(p-1,2)};
const eInOut=p=>p<.5?4*p*p*p:1-Math.pow(-2*p+2,3)/2;
const lerp=(a,b,p)=>a+(b-a)*p;
/* deterministic PRNG so "random" jitter is identical every render */
let _s=SCENE.seed||7;
const rnd=()=>{_s=(_s*1664525+1013904223)%4294967296;return _s/4294967296};

const els=[];                       // {node, fn(t)}
const add=(node,fn)=>{stage.appendChild(node);els.push({node,fn});return node};
const mk=(tag,css,html)=>{const n=document.createElement(tag);n.className='el '+(css||'');
  if(html!==undefined)n.innerHTML=html;return n};

/* Icons arrive in two shapes: the hand-drawn originals are a bare path string
   on a 0 0 100 100 box, Font Awesome's are {d, vb} on their own box. Both are
   normalised here into a square of `size`, centred, so callers never care. */
function iconPath(name){
  const raw=SCENE.icons[name]||SCENE.icons.person;
  if(typeof raw==='string') return {d:raw, x:0, y:0, w:100, h:100};
  const b=(raw.vb||'0 0 512 512').split(/\s+/).map(Number);
  return {d:raw.d, x:b[0]||0, y:b[1]||0, w:b[2]||512, h:b[3]||512};
}
function icon(name,color,size){
  const p=iconPath(name), m=Math.max(p.w,p.h);
  /* centre the glyph inside a square box whatever its native aspect */
  const ox=(m-p.w)/2-p.x, oy=(m-p.h)/2-p.y;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${m} ${m}">
    <path d="${p.d}" fill="${color}" transform="translate(${ox},${oy})"/></svg>`;
}

/* ── Font Awesome's animation set, rebuilt deterministically ───────────────
   Their CSS keyframes run off wall-clock time, which cannot work here: every
   frame is a screenshot at an exact t, so the same t must always look the same.
   Each of these is a pure function of t instead. */
const FX={
  beat:      (t,s)=>`scale(${1+0.14*Math.max(0,Math.sin(t*3.4+s))})`,
  bounce:    (t,s)=>{const u=Math.abs(Math.sin((t*1.6+s)*Math.PI));
                     return `translateY(${-22*u}px) scaleY(${1+0.05*u}) scaleX(${1-0.04*u})`},
  /* every curve here must be CONTINUOUS. The old shake used (t+s)%3, which
     snaps from 0 back to 1 — that was the visible hitch every few seconds. */
  shake:     (t,s)=>`rotate(${Math.sin(t*13+s)*3.2*(0.5+0.5*Math.sin(t*0.7+s))}deg)`,
  flip:      (t,s)=>`rotateY(${((t*80+s*60)%360)}deg)`,
  spin:      (t,s)=>`rotate(${(t*90+s*40)%360}deg)`,
  fade:      (t,s)=>'',                       /* opacity only, see fxAlpha */
  float:     (t,s)=>`translateY(${Math.sin(t*1.5+s*1.3)*7}px)`,
  drift:     (t,s)=>`translate(${Math.sin(t*0.9+s)*6}px,${Math.cos(t*1.1+s*0.7)*5}px)`,
};
const fxAlpha=(kind,t,s)=> kind==='fade' ? 0.55+0.45*Math.abs(Math.sin(t*2.2+s)) : 1;
const FX_NAMES=Object.keys(FX);
/* Nothing on screen is ever completely still — that is what separates an edit
   that feels alive from a slideshow. */
function idle(node,kind,seed){
  const f=FX[kind]||FX.float, s=(seed||0)*0.7;
  /* `amp` fades the motion IN; scaling t instead (as this used to) makes the
     clock itself accelerate, which is what made the icons stutter. */
  return (t,extra,amp)=>{
    const a=(amp===undefined)?1:cl(amp,0,1);
    node.style.transform=`${extra||''} ${a<=0.001?'':f(t,s)}`;
    if(a>0.001&&a<1) node.style.opacity=String(a);
    const o=fxAlpha(kind,t,s);
    if(o!==1) node.style.opacity=String(o*a);
  };
}
/* A backing shape behind each pictogram. Templates used to draw a bare icon, so
   two scenes with completely different content still read as the same picture.
   The backing is what makes each composition look like its own thing — the
   shape is picked per item and per scene, never at random. */
const SHAPES=['circle','ring','square','hex','diamond','arch','blob','slab'];
function badge(name,color,size,k){
  const S=Math.round(size), R=S/2, i=((k|0)%SHAPES.length+SHAPES.length)%SHAPES.length;
  const kind=SHAPES[i];
  const soft=color+'26';               /* 8-digit hex = the same colour, faded */
  let bg='';
  if(kind==='circle')      bg=`<circle cx="${R}" cy="${R}" r="${R}" fill="${soft}"/>`;
  else if(kind==='ring')   bg=`<circle cx="${R}" cy="${R}" r="${R-5}" fill="none" stroke="${color}" stroke-width="6" opacity=".55"/>`;
  else if(kind==='square') bg=`<rect x="0" y="0" width="${S}" height="${S}" rx="${S*.24}" fill="${soft}"/>`;
  else if(kind==='hex')    bg=`<polygon points="${R},0 ${S},${S*.26} ${S},${S*.74} ${R},${S} 0,${S*.74} 0,${S*.26}" fill="${soft}"/>`;
  else if(kind==='diamond')bg=`<polygon points="${R},0 ${S},${R} ${R},${S} 0,${R}" fill="${soft}"/>`;
  else if(kind==='arch')   bg=`<path d="M0 ${S} L0 ${R} A ${R} ${R} 0 0 1 ${S} ${R} L${S} ${S} Z" fill="${soft}"/>`;
  else if(kind==='blob')   bg=`<path d="M${R} 2 C ${S*.92} ${S*.06}, ${S-2} ${S*.34}, ${S*.92} ${R}
                                 C ${S*.84} ${S*.86}, ${S*.6} ${S-2}, ${R} ${S*.96}
                                 C ${S*.2} ${S-4}, ${2} ${S*.74}, ${S*.06} ${R}
                                 C ${S*.1} ${S*.2}, ${S*.28} ${4}, ${R} 2 Z" fill="${soft}"/>`;
  else                     bg=`<path d="M0 ${S*.18} L${S} 0 L${S} ${S*.82} L0 ${S} Z" fill="${soft}"/>`;
  const inner=Math.round(S*0.56), off=Math.round((S-inner)/2);
  /* `k` is already this function's shape index — the glyph scale needs its own
     name or the whole scene dies with "Identifier 'k' has already been declared" */
  const ip=iconPath(name), m=Math.max(ip.w,ip.h), gs=inner/m;
  const gx=off+((m-ip.w)/2-ip.x)*gs, gy=off+((m-ip.h)/2-ip.y)*gs;
  return `<svg width="${S}" height="${S}" viewBox="0 0 ${S} ${S}">${bg}
    <g transform="translate(${gx},${gy}) scale(${gs})">
      <path d="${ip.d}" fill="${color}"/></g></svg>`;
}
/* text auto-size: shrink long headlines so they always fit the safe width */
function fit(text,base,maxw,weight){
  /* The old curve bottomed out at 0.46x, so a long line on a base of 66 came
     out near 30px — legible on a monitor at 100%, illegible on a phone, which
     is where these are watched. There is a floor now: below ~54px a headline
     stops being a headline, so long text wraps to another line instead of
     shrinking further. */
  const n=(text||'').length;
  let s=base;
  if(n>28)s=base*0.88; if(n>46)s=base*0.78; if(n>70)s=base*0.68; if(n>100)s=base*0.60;
  return Math.max(54,s);
}
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

/* ── shared pieces ────────────────────────────────────────────────────── */
/* headline that types/reveals in, sitting near the top */
function headline(txt,y,size,color){
  const fs=fit(txt,size||96);
  const n=mk('div','tx',esc(txt));
  Object.assign(n.style,{left:'140px',top:y+'px',width:(W-280)+'px',
    fontSize:fs+'px',fontWeight:SK.tw,textAlign:'center',color:color||SK.ink});
  return add(n,t=>{const p=eOut(seg(t,.15,.75));
    n.style.opacity=p; n.style.transform=`translateY(${(1-p)*34}px)`;});
}
function sub(txt,y,size,align,left){
  if(!txt)return null;
  const n=mk('div','tx soft',esc(txt));
  const L=(left===undefined)?220:left;
  Object.assign(n.style,{left:L+'px',top:y+'px',width:(W-L-220)+'px',
    fontSize:Math.max(40,size||52)+'px',fontWeight:'600',textAlign:align||'center'});
  return add(n,t=>{const p=eOut(seg(t,.55,.7));
    n.style.opacity=p*.95; n.style.transform=`translateY(${(1-p)*20}px)`;});
}
/* Layout is measured, never guessed: headlines wrap to a different number of
   lines depending on the sentence, so anything placed under one must ask the
   DOM how tall it actually turned out. */
const below=(node,gap)=>node?node.offsetTop+node.offsetHeight+(gap||28):0;
/* the on-screen box of the LAST text line — used to underline exactly it */
function lastLine(node){
  const sp=node.querySelector('.mtxt');
  if(!sp)return null;
  const r=sp.getClientRects();
  return r.length?r[r.length-1]:null;
}
/* hand-drawn marker underline that draws on */
function marker(x,y,w,color,delay){
  const h=26;
  const n=mk('div','',`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
     <path id="mk" d="M4 ${h/2+4} Q ${w*.25} ${h/2-5}, ${w*.5} ${h/2} T ${w-4} ${h/2-2}"
       stroke="${color}" stroke-width="14" fill="none" stroke-linecap="round" opacity=".55"/></svg>`);
  Object.assign(n.style,{left:x+'px',top:y+'px'});
  const path=n.querySelector('#mk');
  const L=path.getTotalLength();
  path.style.strokeDasharray=L;
  return add(n,t=>{const p=eOut(seg(t,delay||1.1,.6));
    path.style.strokeDashoffset=L*(1-p);});
}
/* soft card used by several templates */
function cardBox(x,y,w,h,delay,radius){
  const shadow=SK.glass?'0 24px 60px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.18)'
    :SK.paper?'0 3px 0 rgba(0,0,0,.10)':'0 18px 40px rgba(17,24,39,.10)';
  const n=mk('div','','');
  Object.assign(n.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px',
    background:SK.card,border:`${SK.glass?2:3}px solid ${SK.cl}`,borderRadius:(radius||26)+'px',
    boxShadow:shadow});
  if(SK.glass){n.style.backdropFilter='blur(16px) saturate(1.25)';
    n.style.webkitBackdropFilter='blur(16px) saturate(1.25)';}
  add(n,t=>{const p=eBack(seg(t,delay,.65));
    n.style.opacity=cl(p,0,1); n.style.transform=`translateY(${(1-cl(p,0,1))*40}px) scale(${lerp(.94,1,cl(p,0,1))})`;});
  return n;
}

/* ══ TEMPLATES ═══════════════════════════════════════════════════════════ */
const T={};

/* 1. TITLE — a single strong statement, marker-underlined (whiteboard feel) */
/* A statement scene has FOUR layouts. A script naturally produces a lot of
   these, so the variant (picked by the planner) keeps consecutive ones from
   looking like the same slide twice. */
T.title=()=>{
  const v=(SCENE.variant|0)%4;
  const fs=fit(SCENE.title,v===1?96:110);
  const leftAl=(v===1);
  const padL=leftAl?300:160;
  const n=mk('div','tx',`<span class="mtxt">${esc(SCENE.title)}</span>`);
  Object.assign(n.style,{left:padL+'px',width:(W-padL-160)+'px',
    fontSize:fs+'px',fontWeight:SK.tw,textAlign:leftAl?'left':'center'});
  add(n,t=>{const p=eOut(seg(t,.2,.8));
    n.style.opacity=p;
    n.style.transform=leftAl?`translateX(${(1-p)*-34}px)`
                            :`translateY(${(1-p)*40}px) scale(${lerp(.97,1,p)})`;});
  /* vertically centre the block once we know how many lines it wrapped to */
  n.style.top='0px';
  const hgt=n.offsetHeight;
  const top=Math.round((v===3?H*.47:H*.42)-hgt/2);
  n.style.top=top+'px';

  if(v===0){                                  /* marker underline */
    const r=lastLine(n);
    /* anchor to the measured bottom of the last line so the stroke sits UNDER
       the descenders instead of striking through them */
    if(r) marker(r.left+r.width*.04, r.bottom-fs*0.16, r.width*.92, SK.ac[1], 1.15);
  } else if(v===1){                           /* accent rule down the left */
    const bar=mk('div','','');
    Object.assign(bar.style,{left:(padL-56)+'px',top:top+'px',width:'14px',
      height:hgt+'px',background:SK.ac[1],borderRadius:'7px',transformOrigin:'top center'});
    add(bar,t=>{const p=eOut(seg(t,.3,.6));bar.style.opacity=p;bar.style.transform=`scaleY(${p})`;});
  } else if(v===2){                           /* text sitting on a card */
    const pad=54;
    const box=cardBox(padL-pad, top-pad, W-2*(padL-pad), hgt+pad*2, .18, 30);
    stage.insertBefore(box, stage.firstChild);   /* behind the text */
  } else {                                    /* opening quote mark above */
    const q=mk('div','tx','“');
    Object.assign(q.style,{left:'160px',top:(top-fs*1.05)+'px',width:(W-320)+'px',
      fontSize:(fs*1.5)+'px',fontWeight:'800',textAlign:'center',color:SK.ac[1],lineHeight:'1'});
    add(q,t=>{const p=eBack(seg(t,.1,.6));q.style.opacity=cl(seg(t,.1,.35),0,1)*.9;
      q.style.transform=`scale(${cl(p,0,1.05)})`;});
  }
  sub(SCENE.subtitle, below(n,54), 46,
      leftAl?'left':'center', leftAl?padL:220);
};

/* 2. STAT — one big number counting up + label */
T.stat=()=>{
  const val=SCENE.value||'';
  const num=parseFloat(String(val).replace(/[^0-9.\-]/g,''));
  const suffix=String(val).replace(/[0-9.,\-\s]/g,'');
  headline(SCENE.title,H*.14,68);
  const n=mk('div','tx','0');
  Object.assign(n.style,{left:'100px',top:(H*.32)+'px',width:(W-200)+'px',
    fontSize:'260px',fontWeight:'800',textAlign:'center',color:SK.ac[0],letterSpacing:'-8px'});
  add(n,t=>{const p=eOut(seg(t,.7,1.5));
    n.style.opacity=cl(seg(t,.6,.4),0,1);
    n.style.transform=`scale(${lerp(.8,1,eBack(cl(seg(t,.6,.7),0,1)))})`;
    if(isFinite(num)){
      const cur=num*p;
      n.textContent=(num%1?cur.toFixed(1):Math.round(cur).toLocaleString('en-US'))+suffix;
    } else n.textContent=val;
  });
  sub(SCENE.subtitle,H*.70,48);
};

/* 3. BARS — animated bar chart, bars capped with a person head (paper look) */
T.bars=()=>{
  headline(SCENE.title,H*.10,64);
  const items=(SCENE.items||[]).slice(0,8);
  const vals=items.map(i=>Math.max(0.05,parseFloat(i.value)||0.3));
  const mx=Math.max(...vals,0.001);
  const n=items.length||1;
  const bw=Math.min(150,(W-460)/n-34), gap=34;
  const tw=n*bw+(n-1)*gap, x0=(W-tw)/2, base=H*.80, maxh=H*.44;
  /* axis */
  const ax=mk('div','',`<svg width="${tw+120}" height="6"><rect width="${tw+120}" height="6" rx="3" fill="${SK.ink}" opacity=".85"/></svg>`);
  Object.assign(ax.style,{left:(x0-60)+'px',top:base+'px'});
  add(ax,t=>{const p=eOut(seg(t,.25,.5));ax.style.opacity=p;
    ax.style.transform=`scaleX(${p})`;ax.style.transformOrigin='left center'});
  items.forEach((it,i)=>{
    const col=SK.ac[i%SK.ac.length];
    const h=maxh*(vals[i]/mx);
    const x=x0+i*(bw+gap);
    const head=bw*0.46;
    /* The SVG is built at EXACTLY this bar's height. Sizing it to maxh and
       scaling instead let short bars hang below the axis over their labels. */
    const th=head*0.72+h;
    const bar=mk('div','',`<svg width="${bw}" height="${th}" viewBox="0 0 ${bw} ${th}">
        <circle cx="${bw/2}" cy="${head/2}" r="${head/2}" fill="${col}"/>
        <rect x="0" y="${head*0.72}" width="${bw}" height="${h}" rx="${bw*0.34}" fill="${col}"/>
      </svg>`);
    Object.assign(bar.style,{left:x+'px',top:(base-th)+'px',transformOrigin:'center bottom'});
    const lbl=mk('div','tx',esc(it.label||''));
    Object.assign(lbl.style,{left:(x-24)+'px',top:(base+22)+'px',width:(bw+48)+'px',
      fontSize:'38px',fontWeight:'700',textAlign:'center'});
    const d=.55+i*.13;
    add(bar,t=>{const p=eBack(seg(t,d,.75));
      const settle=1+Math.sin(t*2.1+i*0.8)*0.006*cl(seg(t,d+.8,.5),0,1);
      bar.style.opacity=cl(seg(t,d,.25),0,1);
      bar.style.transform=`scaleY(${cl(p,.001,1.06)*settle})`;});
    add(lbl,t=>{const p=eOut(seg(t,d+.25,.5));lbl.style.opacity=p;});
  });
  sub(SCENE.subtitle,H*.885,40);
};

/* 4. CROWD — a grid of small people filling in (scale / "how many" ideas) */
T.crowd=()=>{
  headline(SCENE.title,H*.10,64);
  const total=cl(parseInt(SCENE.count)||48,12,90);
  const hi=cl(parseInt(SCENE.highlight)||Math.round(total/3),0,total);
  const cols=Math.ceil(Math.sqrt(total*1.9)), rows=Math.ceil(total/cols);
  const gap=14, y0=H*.32;
  /* Size from BOTH axes: picking it from width alone overflowed the frame
     bottom on tall grids and buried the subtitle off-screen. */
  const availH=H*.80-y0;
  const s=Math.max(30,Math.min(96,(W-520)/cols-gap,availH/rows-gap));
  const tw=cols*s+(cols-1)*gap, x0=(W-tw)/2;
  for(let i=0;i<total;i++){
    const r=Math.floor(i/cols), c=i%cols;
    const on=i<hi;
    const col=on?SK.ac[1]:SK.ac[0];
    const n=mk('div','',icon('person',col,s));
    Object.assign(n.style,{left:(x0+c*(s+gap))+'px',top:(y0+r*(s+gap))+'px'});
    const d=.5+(i*0.028);
    add(n,t=>{const p=eBack(seg(t,d,.5));
      n.style.opacity=cl(seg(t,d,.25),0,1);
      n.style.transform=`scale(${cl(p,0,1.05)})`;});
  }
  sub(SCENE.subtitle,y0+rows*(s+gap)+30,42);
};

/* 5. FLOW — icons connected by arrows (a process / a transfer) */
T.flow=()=>{
  headline(SCENE.title,H*.12,64);
  const items=(SCENE.items||[]).slice(0,4);
  const n=items.length||1;
  const s=n<=2?250:(n===3?210:180);
  const gapA=n<=2?230:(n===3?170:130);
  const tw=n*s+(n-1)*gapA, x0=(W-tw)/2, y=H*.40;
  items.forEach((it,i)=>{
    const col=SK.ac[i%SK.ac.length];
    const x=x0+i*(s+gapA);
    const ic=mk('div','',badge(it.icon||ICON_R[i%ICON_R.length],col,s,i+(SCENE.variant|0)*3+1));
    Object.assign(ic.style,{left:x+'px',top:y+'px'});
    const d=.5+i*.45;
    add(ic,t=>{const p=eBack(seg(t,d,.6));
      ic.style.opacity=cl(seg(t,d,.3),0,1);
      const bob=Math.sin(t*1.35+i*1.4)*8*cl(seg(t,d+.5,.6),0,1);
      ic.style.transform=`translateY(${(1-cl(p,0,1))*26+bob}px) scale(${cl(p,0,1.04)})`;});
    const lbl=mk('div','tx',esc(it.label||''));
    Object.assign(lbl.style,{left:(x-60)+'px',top:(y+s+26)+'px',width:(s+120)+'px',
      fontSize:'38px',fontWeight:'700',textAlign:'center'});
    add(lbl,t=>{lbl.style.opacity=eOut(seg(t,d+.2,.5))});
    if(i<n-1){
      const aw=gapA-40;
      const ar=mk('div','',`<svg width="${aw}" height="40" viewBox="0 0 ${aw} 40">
        <path id="a${i}" d="M2 20H${aw-26}" stroke="${SK.ink}" stroke-width="9"
          fill="none" stroke-linecap="round"/>
        <path d="M${aw-30} 4 L${aw-2} 20 L${aw-30} 36Z" fill="${SK.ink}" id="h${i}"/></svg>`);
      Object.assign(ar.style,{left:(x+s+20)+'px',top:(y+s/2-20)+'px'});
      const pa=ar.querySelector('#a'+i), hd=ar.querySelector('#h'+i);
      const L=pa.getTotalLength(); pa.style.strokeDasharray=L;
      const dd=d+.32;
      add(ar,t=>{const p=eOut(seg(t,dd,.42));
        pa.style.strokeDashoffset=L*(1-p); hd.style.opacity=cl(seg(t,dd+.3,.2),0,1);});
    }
  });
  sub(SCENE.subtitle,H*.74,44);
};

/* 6. CARDS — 2-4 cards sliding up with icon + label + a line of detail */
T.cards=()=>{
  headline(SCENE.title,H*.11,62);
  const items=(SCENE.items||[]).slice(0,4);
  const n=items.length||1;
  const cw=n<=2?520:(n===3?420:350), gap=44;
  const tw=n*cw+(n-1)*gap, x0=(W-tw)/2, y=H*.32;
  const boxes=[], bottoms=[];
  items.forEach((it,i)=>{
    const col=SK.ac[i%SK.ac.length];
    const x=x0+i*(cw+gap);
    boxes.push(cardBox(x,y,cw,10,.5+i*.2,28));   /* height set after measuring */
    const s=Math.min(120,cw*0.3);
    const ic=mk('div','',badge(it.icon||ICON_R[i%ICON_R.length],col,s,i+(SCENE.variant|0)*3));
    Object.assign(ic.style,{left:(x+cw/2-s/2)+'px',top:(y+46)+'px'});
    const ttl=mk('div','tx',esc(it.label||''));
    Object.assign(ttl.style,{left:(x+22)+'px',top:(y+66+s)+'px',width:(cw-44)+'px',
      fontSize:(n>=4?40:46)+'px',fontWeight:'800',textAlign:'center'});
    const txt=mk('div','tx soft',esc(it.text||''));
    Object.assign(txt.style,{left:(x+30)+'px',top:(y+130+s)+'px',width:(cw-60)+'px',
      fontSize:(n>=4?34:36)+'px',fontWeight:'600',textAlign:'center',lineHeight:'1.35'});
    const d=.62+i*.2;
    /* never still — after it lands the icon keeps one of the FA motions running */
    const fx=FX_NAMES[(i+(SCENE.variant|0)) % FX_NAMES.length];
    const drive=idle(ic,fx,i);
    add(ic,t=>{const p=eBack(seg(t,d,.55));
      ic.style.opacity=cl(seg(t,d,.3),0,1);
      drive(t, `scale(${cl(p,0,1.05)})`, cl(seg(t,d+.5,.6),0,1));});
    add(ttl,t=>{ttl.style.opacity=eOut(seg(t,d+.15,.45))});
    add(txt,t=>{txt.style.opacity=eOut(seg(t,d+.28,.45))*.95});
    bottoms.push(txt.offsetTop+txt.offsetHeight);
  });
  /* One shared height that hugs the tallest card's content — a fixed height
     left a big empty slab under short copy. */
  const ch=Math.max(...bottoms)-y+38;
  boxes.forEach(b=>{b.style.height=ch+'px'});
  sub(SCENE.subtitle,y+ch+40,40);
};

/* 7. CHECKLIST — rows ticking in one by one (the product-UI look) */
T.checklist=()=>{
  headline(SCENE.title,H*.12,64);
  const items=(SCENE.items||[]).slice(0,5);
  const rw=Math.min(1180,W-560), rh=116, gap=26;
  const x0=(W-rw)/2;
  /* centre the stack in the space under the headline instead of pinning it high */
  const blockH=items.length*rh+(items.length-1)*gap;
  const y0=Math.round(H*.56-blockH/2);
  items.forEach((it,i)=>{
    const y=y0+i*(rh+gap);
    const col=SK.ac[i%SK.ac.length];
    const box=cardBox(x0,y,rw,rh,.5+i*.22,22);
    const s=52;
    const tick=mk('div','',`<svg width="${s}" height="${s}" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="44" fill="${col}"/>
      <path id="ck${i}" d="M28 52 L44 68 L72 34" stroke="#fff" stroke-width="11"
        fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`);
    Object.assign(tick.style,{left:(x0+30)+'px',top:(y+rh/2-s/2)+'px'});
    const ck=tick.querySelector('#ck'+i);
    const L=ck.getTotalLength(); ck.style.strokeDasharray=L;
    const lbl=mk('div','tx',esc(it.label||it.text||''));
    Object.assign(lbl.style,{left:(x0+118)+'px',top:(y+rh/2-26)+'px',width:(rw-160)+'px',
      fontSize:'42px',fontWeight:'700',lineHeight:'52px'});
    const d=.62+i*.22;
    add(tick,t=>{const p=eBack(seg(t,d,.45));
      tick.style.opacity=cl(seg(t,d,.25),0,1);
      tick.style.transform=`scale(${cl(p,0,1.06)})`;
      ck.style.strokeDashoffset=L*(1-eOut(seg(t,d+.18,.35)));});
    add(lbl,t=>{const p=eOut(seg(t,d+.1,.45));
      lbl.style.opacity=p; lbl.style.transform=`translateX(${(1-p)*22}px)`;});
  });
  sub(SCENE.subtitle,y0+items.length*(rh+gap)+26,40);
};

/* 8. COMPARE — two columns, a vs b */
T.compare=()=>{
  headline(SCENE.title,H*.10,62);
  const a=(SCENE.items||[])[0]||{label:'Before'}, b=(SCENE.items||[])[1]||{label:'After'};
  const cw=(W-560)/2, ch=H*.50, y=H*.30, gap=120;
  const x1=(W-(cw*2+gap))/2, x2=x1+cw+gap;
  [[a,x1,SK.warn,'cross',.5],[b,x2,SK.good,'check',.75]].forEach(([it,x,col,ico,d],k)=>{
    cardBox(x,y,cw,ch,d,30);
    const s=130;
    const ic=mk('div','',badge(it.icon||ico,col,s,k+(SCENE.variant|0)*3));
    Object.assign(ic.style,{left:(x+cw/2-s/2)+'px',top:(y+56)+'px'});
    const ttl=mk('div','tx',esc(it.label||''));
    Object.assign(ttl.style,{left:(x+24)+'px',top:(y+72+s)+'px',width:(cw-48)+'px',
      fontSize:'54px',fontWeight:'800',textAlign:'center',color:col});
    const txt=mk('div','tx soft',esc(it.text||''));
    Object.assign(txt.style,{left:(x+40)+'px',top:(y+150+s)+'px',width:(cw-80)+'px',
      fontSize:'38px',fontWeight:'600',textAlign:'center',lineHeight:'1.35'});
    add(ic,t=>{const p=eBack(seg(t,d+.12,.55));ic.style.opacity=cl(seg(t,d+.12,.3),0,1);
      ic.style.transform=`scale(${cl(p,0,1.05)})`});
    add(ttl,t=>{ttl.style.opacity=eOut(seg(t,d+.26,.45))});
    add(txt,t=>{txt.style.opacity=eOut(seg(t,d+.38,.45))*.95});
  });
  const vs=mk('div','tx','vs');
  Object.assign(vs.style,{left:(x1+cw)+'px',top:(y+ch/2-46)+'px',width:gap+'px',
    fontSize:'62px',fontWeight:'800',textAlign:'center',color:SK.inks});
  add(vs,t=>{const p=eBack(seg(t,1.0,.5));vs.style.opacity=cl(seg(t,1.0,.3),0,1);
    vs.style.transform=`scale(${cl(p,0,1.08)})`});
};

/* 9. STEPS — numbered timeline across the frame */
T.steps=()=>{
  headline(SCENE.title,H*.13,62);
  const items=(SCENE.items||[]).slice(0,4);
  const n=items.length||1;
  const r=76, span=Math.min(W-520,n*370), x0=(W-span)/2, y=H*.44;
  const dx=n>1?span/(n-1):0;
  /* connecting rail */
  if(n>1){
    const rail=mk('div','',`<svg width="${span}" height="10" viewBox="0 0 ${span} 10">
      <rect id="rl" width="${span}" height="10" rx="5" fill="${SK.ink}" opacity=".22"/></svg>`);
    Object.assign(rail.style,{left:x0+'px',top:(y-5)+'px',transformOrigin:'left center'});
    add(rail,t=>{const p=eOut(seg(t,.4,.6));rail.style.opacity=p;rail.style.transform=`scaleX(${p})`});
  }
  items.forEach((it,i)=>{
    const col=SK.ac[i%SK.ac.length];
    const x=x0+i*dx;
    const c=mk('div','',`<svg width="${r*2}" height="${r*2}" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="46" fill="${col}"/>
      <text x="50" y="50" text-anchor="middle" dominant-baseline="central"
        font-family="Montserrat,sans-serif" font-size="46" font-weight="800" fill="#fff">${i+1}</text></svg>`);
    Object.assign(c.style,{left:(x-r)+'px',top:(y-r)+'px'});
    const lbl=mk('div','tx',esc(it.label||''));
    Object.assign(lbl.style,{left:(x-190)+'px',top:(y+r+26)+'px',width:'380px',
      fontSize:'38px',fontWeight:'800',textAlign:'center'});
    const txt=mk('div','tx soft',esc(it.text||''));
    Object.assign(txt.style,{left:(x-180)+'px',top:(y+r+80)+'px',width:'360px',
      fontSize:'38px',fontWeight:'600',textAlign:'center',lineHeight:'1.32'});
    const d=.55+i*.32;
    add(c,t=>{const p=eBack(seg(t,d,.5));c.style.opacity=cl(seg(t,d,.25),0,1);
      c.style.transform=`scale(${cl(p,0,1.07)})`});
    add(lbl,t=>{lbl.style.opacity=eOut(seg(t,d+.16,.45))});
    add(txt,t=>{txt.style.opacity=eOut(seg(t,d+.26,.45))*.95});
  });
};

/* ══ COLLAGE FAMILY — real cut-out stickers pasted on the paper ground ═══
   Shared helpers: a taped sticker, a hand-drawn arrow, a scribbled ring. */

/* strip of masking tape, slightly askew */
function tape(x,y,w,rot,delay,parent){
  const n=mk('div','');
  Object.assign(n.style,{left:x+'px',top:y+'px',width:w+'px',height:'42px',
    background:'linear-gradient(180deg, rgba(226,208,160,.86), rgba(206,186,138,.86))',
    boxShadow:'0 2px 5px rgba(0,0,0,.22)',transform:`rotate(${rot}deg)`,
    transformOrigin:'center'});
  const fn=t=>{const p=eOut(seg(t,delay,.3));
    n.style.opacity=p*.95;
    n.style.transform=`rotate(${rot}deg) scale(${lerp(.7,1,p)})`;};
  if(parent){ parent.appendChild(n); els.push({node:n,fn}); return n; }
  return add(n,fn);
}

/* a cut-out sticker that drops onto the page and settles */
function sticker(src,x,y,w,rot,delay,opts){
  opts=opts||{};
  const hh=Math.round(w*(opts.ar||1));
  const img=`<img src="${src}" style="width:${w}px;height:${hh}px;display:block;
    filter:drop-shadow(0 10px 14px rgba(0,0,0,.34))">`;
  /* On a dark ground the black halftone artwork sinks into the background, so
     the cut-out gets pasted onto a small aged-paper card first — the way it
     would actually be glued into a scrapbook. */
  const card=opts.card && SCENE.card_tex;
  const pad=Math.round(w*0.14);
  const n=mk('div','', card
    ? `<div style="padding:${pad}px;background-image:url(${SCENE.card_tex});
         background-size:cover;box-shadow:0 12px 26px rgba(0,0,0,.55)">${img}</div>`
    : img);
  Object.assign(n.style,{left:x+'px',top:y+'px',transformOrigin:'50% 50%'});
  const from=opts.from||'down';
  n.dataset.h=String(w);        /* replaced with the true height once laid out */
  return add(n,t=>{
    const p=eBack(seg(t,delay,.62)), q=cl(p,0,1);
    const settle=cl(seg(t,delay+.7,.8),0,1);
    const bob=Math.sin(t*1.25+delay*3)*4*settle;
    let dx=0,dy=0;
    if(from==='down') dy=(1-q)*150;
    else if(from==='up') dy=-(1-q)*150;
    else if(from==='left') dx=-(1-q)*190;
    else dx=(1-q)*190;
    n.style.opacity=cl(seg(t,delay,.28),0,1);
    n.style.transform=`translate(${dx}px, ${dy+bob}px) rotate(${rot*q}deg) `
                     +`scale(${cl(p,0,1.06)})`;
  });
}

/* hand-drawn dashed arrow that draws itself on, with a head */
function drawArrow(x,y,w,h,curve,delay,color){
  const c=color||SK.ink;
  const n=mk('div','',`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
     <path class="ap" d="M6 ${h-8} C ${w*0.28} ${h-8-curve}, ${w*0.62} ${curve*0.4}, ${w-30} 14"
       stroke="${c}" stroke-width="7" fill="none" stroke-linecap="round"
       stroke-dasharray="2 18"/>
     <path class="ah" d="M${w-42} 6 L${w-8} 16 L${w-36} 34Z" fill="${c}"/></svg>`);
  Object.assign(n.style,{left:x+'px',top:y+'px'});
  const path=n.querySelector('.ap'), head=n.querySelector('.ah');
  let L=0;                       /* measured on first frame, once it is in the DOM */
  return add(n,t=>{const p=eOut(seg(t,delay,.75));
    if(!L) L=path.getTotalLength();
    path.style.strokeDasharray=`${L}`;
    path.style.strokeDashoffset=`${L*(1-p)}`;
    head.style.opacity=cl(seg(t,delay+.6,.25),0,1);
    n.style.opacity=cl(seg(t,delay,.2),0,1);});
}

/* scribbled ring around something, drawn in two loops */
function ring(cx,cy,rx,ry,delay,color){
  const w=rx*2+50, h=ry*2+50, c=color||SK.warn;
  const n=mk('div','',`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <ellipse class="r1" cx="${w/2}" cy="${h/2}" rx="${rx}" ry="${ry}"
      stroke="${c}" stroke-width="6" fill="none" transform="rotate(-4 ${w/2} ${h/2})"/>
    <ellipse class="r2" cx="${w/2}" cy="${h/2}" rx="${rx*1.05}" ry="${ry*0.94}"
      stroke="${c}" stroke-width="4" fill="none" opacity=".7"
      transform="rotate(3 ${w/2} ${h/2})"/></svg>`);
  Object.assign(n.style,{left:(cx-w/2)+'px',top:(cy-h/2)+'px'});
  const e1=n.querySelector('.r1'), e2=n.querySelector('.r2');
  /* getTotalLength() throws on an element that is not in the document yet, so
     the node has to be appended BEFORE its path length can be measured. */
  let L1=0, L2=0;
  const reg=add(n,t=>{
    if(!L1){ L1=e1.getTotalLength(); L2=e2.getTotalLength();
             e1.style.strokeDasharray=L1; e2.style.strokeDasharray=L2; }
    e1.style.strokeDashoffset=L1*(1-eOut(seg(t,delay,.45)));
    e2.style.strokeDashoffset=L2*(1-eOut(seg(t,delay+.28,.45)));});
  return reg;
}

/* handwritten annotation */
function hand(txt,x,y,size,rot,delay,color,width){
  if(!txt)return null;
  const n=mk('div','',esc(txt));
  Object.assign(n.style,{left:x+'px',top:y+'px',width:(width||520)+'px',
    fontFamily:SK.hf||"'Shadows Into Light', cursive",
    fontSize:Math.max(34,size||46)+'px',
    color:color||SK.ink,transform:`rotate(${rot}deg)`,lineHeight:'1.2'});
  return add(n,t=>{const p=eOut(seg(t,delay,.5));
    n.style.opacity=p;
    n.style.transform=`rotate(${rot}deg) translateY(${(1-p)*14}px)`;});
}

/* 14. COLLAGE — 2-4 stickers land on the page, each labelled by hand,
   with arrows and a circled key word. The workhorse annotation scene. */
T.collage=()=>{
  const cut=SCENE.cutouts||[];
  const items=(SCENE.items||[]).slice(0,4);
  const n=Math.max(1,Math.min(cut.length,items.length||cut.length));
  const headFs=fit(SCENE.title,86);
  const ttl=mk('div','',esc(SCENE.title||''));
  Object.assign(ttl.style,{left:'150px',top:'78px',width:(W-300)+'px',
    fontFamily:SK.tf,fontSize:headFs+'px',color:SK.ink,textAlign:'center'});
  add(ttl,t=>{const p=eOut(seg(t,.15,.55));ttl.style.opacity=p;
    ttl.style.transform=`translateY(${(1-p)*-18}px) rotate(${-.6+.6*p}deg)`;});

  /* Normalise on HEIGHT, not width: the cut-outs range from a squat book to a
     tall alarm clock, and matching widths left the captions on a ragged line
     with the tallest one colliding with the footer. */
  const col=n===1?760:(n===2?520:(n===3?400:320)), gap=n<=2?120:60;
  const tw=n*col+(n-1)*gap, x0=(W-tw)/2;
  const y=Math.round(n===1?H*.20:H*.30);
  const rots=[-4.5,3.2,-2.4,4.1], dirs=['down','left','up','right'];
  /* Size the artwork from the room that is actually left. A fixed height plus
     the paper card's padding pushed a single big sticker's captions off the
     bottom of the frame and into the footer line. */
  const capBlock=n===1?150:118;                 /* label + detail */
  const pad=SCENE.card?0.16:0;                  /* card padding, both sides */
  const room=H*0.86-y-capBlock;
  const targetH=Math.round(Math.min(n===1?460:(n===2?420:(n===3?330:280)),
                                    room/(1+2*pad)));
  const cardPad=Math.round(targetH*pad);
  const ly=y+targetH+cardPad*2+30;
  for(let i=0;i<n;i++){
    const ar=(SCENE.cut_ar||[])[i]||1;
    const sw=Math.round(targetH/Math.max(.25,ar));      /* width from the aspect */
    const cx=x0+i*(col+gap)+col/2;
    const x=Math.round(cx-sw/2), d=.5+i*.34;
    tape(cx-sw*0.17, y-26, sw*0.34, rots[i]*2.2, d-.1);
    sticker(cut[i], x, y, sw, rots[i], d, {from:dirs[i%4], ar:ar, card:SCENE.card});
    const it=items[i]||{};
    hand(it.label||'', cx-col/2, ly, n===1?58:44, rots[i]*0.6, d+.45, SK.ink, col);
    if(it.text) hand(it.text, cx-col/2, ly+(n===1?76:62), n===1?40:32, rots[i]*0.5, d+.6, SK.inks, col);
  }
  /* one big sticker sits lower and its caption is larger, so the footer has to
     clear the ACTUAL bottom of the captions, not a fixed offset */
  const capBottom = ly + (n===1 ? 76+52 : 62+40);
  if(SCENE.subtitle) hand(SCENE.subtitle, 200, Math.min(H-96, capBottom+34), 44, -1.2, 1.5,
                          SK.warn, W-400);
};

/* 15. PHOTO-NOTE — a real photograph taped to the page, annotated by hand
   with an arrow and a circled phrase. Built for the portraits in assets/. */
T.photonote=()=>{
  const src=SCENE.image||(SCENE.cutouts||[])[0];
  if(!src){ T.title(); return; }
  const pw=Math.round(W*0.40), ph=Math.round(pw*1.16);
  const x=Math.round(W*0.09), y=Math.round(H*.5-ph/2);
  /* The photo, its paper border and the tape are ONE pinned object: the print
     is clipped inside its border so the Ken Burns push happens within the
     picture, and the tape rides along with the frame instead of being left
     behind (and overrun) as the image grew. */
  const group=mk('div','');
  Object.assign(group.style,{left:x+'px',top:y+'px',width:pw+'px',height:ph+'px',
    transformOrigin:'50% 50%'});
  const frame=document.createElement('div');
  Object.assign(frame.style,{position:'absolute',inset:'0',background:'#f6efdd',
    padding:'16px',boxShadow:'0 16px 34px rgba(0,0,0,.4)',overflow:'hidden'});
  frame.innerHTML=`<div style="width:100%;height:100%;overflow:hidden">
      <img src="${src}" style="width:100%;height:100%;object-fit:cover;display:block;
      filter:grayscale(.35) contrast(1.06)"></div>`;
  group.appendChild(frame);
  const img=frame.querySelector('img');
  add(group,t=>{const p=eBack(seg(t,.25,.7)),q=cl(p,0,1);
    group.style.opacity=cl(seg(t,.25,.3),0,1);
    group.style.transform=`rotate(${-2.4*q}deg) translateY(${(1-q)*70}px) scale(${cl(p,0,1.04)})`;
    img.style.transform=`scale(${lerp(1.0,1.10,eInOut(cl(t/(SCENE.duration||12),0,1)))})`;});
  tape(pw*0.30-40, -24, pw*0.4, -7, .15, group);
  tape(pw*0.34-40, ph-16, pw*0.36, 5, .35, group);

  const tx=x+pw+120;
  const ttl=mk('div','',esc(SCENE.title||''));
  Object.assign(ttl.style,{left:tx+'px',top:(y+30)+'px',width:(W-tx-140)+'px',
    fontFamily:SK.tf,fontSize:fit(SCENE.title,80)+'px',color:SK.ink,lineHeight:'1.18'});
  add(ttl,t=>{const p=eOut(seg(t,.7,.6));ttl.style.opacity=p;
    ttl.style.transform=`translateX(${(1-p)*26}px)`;});
  drawArrow(tx-150, y+ph*0.36, 150, 130, 70, 1.05, SK.warn);
  if(SCENE.subtitle) hand(SCENE.subtitle, tx, below(ttl,26), 42, -1, 1.35, SK.inks, W-tx-160);
};

/* 16. SCATTER — many small stickers fly in around one circled statement.
   Uses the whole library, so no two scenes look alike. */
T.scatter=()=>{
  const cut=SCENE.cutouts||[];
  const fs=fit(SCENE.title,86);
  const ttl=mk('div','',esc(SCENE.title||''));
  Object.assign(ttl.style,{left:'320px',width:(W-640)+'px',fontFamily:SK.tf,
    fontSize:fs+'px',color:SK.ink,textAlign:'center',lineHeight:'1.2'});
  add(ttl,t=>{const p=eBack(seg(t,.9,.7));ttl.style.opacity=cl(seg(t,.9,.4),0,1);
    ttl.style.transform=`scale(${cl(p,0,1.04)})`;});
  ttl.style.top='0px';
  const hgt=ttl.offsetHeight;
  ttl.style.top=Math.round(H*.5-hgt/2)+'px';
  ring(W/2, H*.5, Math.min(W*0.3,fs*7), hgt*0.72, 1.6, SK.warn);

  /* ring of stickers around the words, dropping in from the edges */
  const N=Math.min(8,cut.length);
  for(let i=0;i<N;i++){
    const a=(i/N)*6.283+0.4;
    /* push the ring of stickers outside the circled headline instead of
       letting them sit on top of it */
    const rx=W*0.40, ry=H*0.36;
    const sw=112+ (i%3)*26;
    const x=W/2+Math.cos(a)*rx-sw/2, y=H/2+Math.sin(a)*ry-sw/2;
    sticker(cut[i], x, y, sw, (i%2?1:-1)*(4+i*1.6), .25+i*.14,
            {from:(Math.cos(a)<0?'left':'right'), ar:(SCENE.cut_ar||[])[i]||1,
             card:SCENE.card});
  }
  if(SCENE.subtitle) hand(SCENE.subtitle, 260, H-150, 40, -.8, 2.0, SK.inks, W-520);
};

/* 18. OPENER — the title card that starts a video. The teacher's photograph
   drops in taped to the page, a ring is scribbled round it, cut-outs fly in
   from the edges and the title writes itself in. Deliberately busier than any
   other scene: it is the three seconds that decide whether anyone stays. */
T.opener=()=>{
  const cut=SCENE.cutouts||[];
  const hasPhoto=!!SCENE.image;

  /* the portrait, pinned slightly off-centre */
  let px=0, py=0, pw=0, ph=0;
  if(hasPhoto){
    pw=Math.round(W*0.26); ph=Math.round(pw*1.2);
    px=Math.round(W*0.5-pw/2); py=Math.round(H*0.20);
    const group=mk('div','');
    Object.assign(group.style,{left:px+'px',top:py+'px',width:pw+'px',height:ph+'px',
      transformOrigin:'50% 50%'});
    const frame=document.createElement('div');
    Object.assign(frame.style,{position:'absolute',inset:'0',background:'#f4ecd8',
      padding:'14px',boxShadow:'0 20px 40px rgba(0,0,0,.55)',overflow:'hidden'});
    frame.innerHTML=`<div style="width:100%;height:100%;overflow:hidden">
      <img src="${SCENE.image}" style="width:100%;height:100%;object-fit:cover;
      display:block;filter:grayscale(.45) contrast(1.08)"></div>`;
    group.appendChild(frame);
    const img=frame.querySelector('img');
    add(group,t=>{
      const p=eBack(seg(t,.18,.8)), q=cl(p,0,1);
      const settle=cl(seg(t,1.1,1.2),0,1);
      group.style.opacity=cl(seg(t,.18,.35),0,1);
      group.style.transform=`rotate(${lerp(-7,-2.2,q)}deg) translateY(${(1-q)*-160}px) `
                           +`scale(${cl(p,0,1.05)})`;
      img.style.transform=`scale(${lerp(1.14,1.0,eInOut(settle))})`;
    });
    tape(pw*0.30-40, -22, pw*0.42, -8, .5, group);
    ring(px+pw/2, py+ph/2, pw*0.62, ph*0.56, 1.5, SK.warn);
  }

  /* cut-outs sweep in from both edges toward the middle */
  const N=Math.min(6, cut.length);
  for(let i=0;i<N;i++){
    const left=i%2===0;
    const sw=104+(i%3)*22;
    const ar=(SCENE.cut_ar||[])[i]||1;
    const yy=H*0.16+ (i%3)*H*0.24 + (i%2)*40;
    const xx=left ? W*0.06+(i%2)*40 : W*0.94-sw-(i%2)*40;
    sticker(cut[i], Math.round(xx), Math.round(yy), sw, (left?-1:1)*(5+i*2),
            .35+i*.16, {from:left?'left':'right', ar:ar, card:SCENE.card});
  }

  /* the title writes on under the photo */
  const fs=fit(SCENE.title, hasPhoto?76:104);
  const ttl=mk('div','',`<span class="mtxt">${esc(SCENE.title||'')}</span>`);
  Object.assign(ttl.style,{left:'200px',width:(W-400)+'px',fontFamily:SK.tf,
    fontSize:fs+'px',color:SK.ink,textAlign:'center',lineHeight:'1.16'});
  add(ttl,t=>{const p=eOut(seg(t,1.05,.8));
    ttl.style.opacity=p;
    ttl.style.transform=`translateY(${(1-p)*30}px) scale(${lerp(.95,1,p)})`;});
  ttl.style.top='0px';
  const th=ttl.offsetHeight;
  const ty=hasPhoto ? Math.round(py+ph+58) : Math.round(H*0.40-th/2);
  ttl.style.top=ty+'px';
  const r=lastLine(ttl);
  if(r) marker(r.left+r.width*.04, r.bottom-fs*0.14, r.width*.92, SK.ac[1], 1.95);
  sub(SCENE.subtitle, ty+th+62, 44);
};

/* 17. PILLARS — three ancient columns rise from the floor, each carrying one
   of the big load-bearing points. Built for "the three things this rests on". */
T.pillars=()=>{
  const cut=SCENE.cutouts||[];
  const items=(SCENE.items||[]).slice(0,3);
  const n=Math.max(1,Math.min(3, items.length||cut.length||3));
  const fs=fit(SCENE.title,82);
  const ttl=mk('div','',esc(SCENE.title||''));
  Object.assign(ttl.style,{left:'150px',top:'70px',width:(W-300)+'px',
    fontFamily:SK.tf,fontSize:fs+'px',color:SK.ink,textAlign:'center'});
  add(ttl,t=>{const p=eOut(seg(t,.12,.5));ttl.style.opacity=p;
    ttl.style.transform=`translateY(${(1-p)*-16}px)`;});

  const ar=(SCENE.cut_ar||[])[0]||2.2;
  const ph=Math.round(H*0.40);                 /* column height */
  const pw=Math.round(ph/Math.max(.3,ar));
  const gap=Math.round(W*0.075);
  const tw=n*pw+(n-1)*gap, x0=(W-tw)/2;
  const base=Math.round(H*0.70);               /* the floor they stand on */

  /* floor line drawn across, so they read as standing on something */
  const fl=mk('div','',`<svg width="${tw+220}" height="8">
     <rect width="${tw+220}" height="6" rx="3" fill="${SK.ink}" opacity=".5"/></svg>`);
  Object.assign(fl.style,{left:(x0-110)+'px',top:base+'px',transformOrigin:'left center'});
  add(fl,t=>{const p=eOut(seg(t,.3,.5));fl.style.opacity=p*.8;fl.style.transform=`scaleX(${p})`;});

  for(let i=0;i<n;i++){
    const x=x0+i*(pw+gap), d=.55+i*.3;
    const src=cut[i]||cut[0];
    const col=mk('div','',`<img src="${src}" style="width:${pw}px;height:${ph}px;
       display:block;object-fit:contain;object-position:bottom;
       filter:drop-shadow(0 14px 20px rgba(0,0,0,.45))">`);
    Object.assign(col.style,{left:x+'px',top:(base-ph)+'px',transformOrigin:'50% 100%'});
    add(col,t=>{
      const p=eOut(seg(t,d,.85));                       /* rises from the floor */
      const sway=Math.sin(t*0.8+i)*0.25*cl(seg(t,d+.9,.6),0,1);
      col.style.opacity=cl(seg(t,d,.3),0,1);
      col.style.transform=`translateY(${(1-p)*ph*0.55}px) rotate(${sway}deg) `
                         +`scaleY(${lerp(.72,1,p)})`;
    });
    const it=items[i]||{};
    const cx=x+pw/2;
    /* caption box = the column pitch, otherwise neighbours run into each other */
    const cw=Math.min(380, pw+gap-24);
    hand(it.label||'', cx-cw/2, base+30, 46, (i-1)*1.4, d+.5, SK.ink, cw);
    if(it.text) hand(it.text, cx-cw/2, base+92, 38, (i-1)*1.1, d+.66, SK.inks, cw);
  }
  if(SCENE.subtitle) hand(SCENE.subtitle, 220, base+186, 40, -1, 1.9, SK.warn, W-440);
};

/* 13. TYPING — the intro beat: one line typed out over a still, with a caret.
   Deterministic: the character count is a pure function of t. */
T.typing=()=>{
  const txt=String(SCENE.title||'');
  if(SCENE.image){                       /* the still sits behind, dimmed */
    const bg=mk('div','');
    Object.assign(bg.style,{left:'0px',top:'0px',width:W+'px',height:H+'px',
      backgroundImage:`url(${SCENE.image})`,backgroundSize:'cover',
      backgroundPosition:'center'});
    add(bg,t=>{const p=eOut(seg(t,0,1.6));
      bg.style.opacity=(0.30*p).toFixed(3);
      bg.style.transform=`scale(${lerp(1.10,1.0,eInOut(cl(t/(SCENE.duration||12),0,1)))})`;});
    const sh=mk('div','');
    Object.assign(sh.style,{left:'0px',top:'0px',width:W+'px',height:H+'px',
      background:'radial-gradient(ellipse 64% 60% at 50% 50%, rgba(0,0,0,.42) 0%, rgba(0,0,0,.88) 100%)'});
    add(sh,()=>{});
  }
  const fs=fit(txt,86);
  const n=mk('div','tx',esc(txt));
  Object.assign(n.style,{left:'200px',width:(W-400)+'px',fontSize:fs+'px',
    fontWeight:SK.tw,textAlign:'center',lineHeight:'1.3'});
  const lead=0.5, cps=Math.max(9, txt.length/Math.max(2.2,(SCENE.duration||12)*0.52));
  /* Append FIRST — offsetHeight is 0 for a node that is not in the document yet,
     which silently collapsed the layout and dropped the subtitle onto the title. */
  add(n,t=>{
    const k=cl(Math.floor((t-lead)*cps),0,txt.length);
    const caret=(t>lead-0.2 && (Math.floor(t*1.9)%2===0) && k<txt.length)?'|':'';
    n.textContent=txt.slice(0,k)+caret;
    n.style.opacity=cl(seg(t,.15,.5),0,1);
  });
  /* measure the finished line so the block never jumps while it types */
  n.style.top='0px';
  const hgt=n.offsetHeight;
  n.style.top=Math.round(H*.5-hgt/2)+'px';
  n.textContent='';
  sub(SCENE.subtitle, Math.round(H*.5+hgt/2+54), 42);
};

/* 11. CARDS-REVEAL — tarot-style cards that fly in, flip up and settle.
   Each card is an arcana-like panel with an icon + name. */
T.reveal=()=>{
  headline(SCENE.title,H*.10,60);
  const items=(SCENE.items||[]).slice(0,4);
  const n=items.length||1;
  const cw=n<=2?390:(n===3?330:270), ch=cw*1.62, gap=n<=2?90:52;
  const tw=n*cw+(n-1)*gap, x0=(W-tw)/2, y=H*.30;
  items.forEach((it,i)=>{
    const col=SK.ac[i%SK.ac.length];
    const x=x0+i*(cw+gap);
    const wrap=mk('div','');
    Object.assign(wrap.style,{left:x+'px',top:y+'px',width:cw+'px',height:ch+'px',
      perspective:'1400px'});
    const face=document.createElement('div');
    Object.assign(face.style,{position:'absolute',inset:'0',borderRadius:'20px',
      background:SK.card,border:`3px solid ${col}`,
      boxShadow:`0 22px 46px rgba(0,0,0,.45), inset 0 0 60px ${col}22`,
      display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
      gap:'22px',transformStyle:'preserve-3d'});
    const s=cw*0.40;
    face.innerHTML=
      `<div style="position:absolute;inset:12px;border:1px solid ${col}66;border-radius:13px"></div>`
      +badge(it.icon||ICON_R[i%ICON_R.length],col,s,i+(SCENE.variant|0)*3)
      +`<div style="font-family:${getComputedStyle(document.body).fontFamily};
          color:${SK.ink};font-size:${Math.round(cw*0.115)}px;font-weight:700;
          letter-spacing:1px;text-align:center;padding:0 14px">${esc(it.label||'')}</div>`
      +(it.text?`<div style="color:${SK.inks};font-size:${Math.max(34,Math.round(cw*0.095))}px;
          text-align:center;padding:0 20px;line-height:1.32">${esc(it.text)}</div>`:'');
    wrap.appendChild(face);
    const d=.45+i*.34;
    add(wrap,t=>{
      const p=eOut(seg(t,d,.75));                 /* fly in from below */
      const f=eOut(seg(t,d+.30,.6));              /* then flip face-up  */
      wrap.style.opacity=cl(seg(t,d,.3),0,1);
      const rise=(1-p)*170, tilt=(1-p)*16;
      const settled=cl(seg(t,d+.9,.6),0,1);
      const sway=Math.sin(t*1.1+i*0.9)*1.6*settled, lift=Math.sin(t*0.9+i*1.3)*7*settled;
      face.style.transform=`translateY(${rise+lift}px) rotateZ(${tilt-8*(1-f)+sway}deg) `
                          +`rotateY(${(1-f)*82}deg)`;
      const glow=cl(seg(t,d+.75,.5),0,1);
      face.style.boxShadow=`0 22px 46px rgba(0,0,0,.45), inset 0 0 ${40+50*glow}px ${col}${glow>.5?'3a':'22'}`;
    });
  });
  sub(SCENE.subtitle,y+ch+34,40);
};

/* 12. IMAGE — a real generated still (kie) framed on the scene, with a caption.
   `SCENE.image` is a data: URI injected by the pipeline. Falls back to `title`
   when no picture was available, so a missing file can never blank a scene. */
T.image=()=>{
  if(!SCENE.image){ T.title(); return; }
  const v=(SCENE.variant|0)%2;
  const iw=v===0?Math.round(W*0.44):Math.round(W*0.62);
  const ih=Math.round(iw*0.62);
  const x=v===0?Math.round(W*0.08):Math.round((W-iw)/2);
  const y=v===0?Math.round(H*.5-ih/2):Math.round(H*.20);
  const col=SK.ac[0];
  const frame=mk('div','');
  Object.assign(frame.style,{left:x+'px',top:y+'px',width:iw+'px',height:ih+'px',
    borderRadius:'18px',overflow:'hidden',border:`3px solid ${col}`,
    boxShadow:'0 26px 60px rgba(0,0,0,.5)'});
  frame.innerHTML=`<img src="${SCENE.image}" style="width:100%;height:100%;object-fit:cover;display:block">`;
  const img=frame.querySelector('img');
  add(frame,t=>{const p=eOut(seg(t,.2,.9));
    frame.style.opacity=cl(seg(t,.2,.4),0,1);
    frame.style.transform=`translateY(${(1-p)*36}px)`;
    img.style.transform=`scale(${lerp(1.14,1.0,eInOut(cl(t/(SCENE.duration||12),0,1)))})`;});
  /* copy sits beside the picture (v0) or under it (v1) */
  const tx=v===0?x+iw+64:Math.round(W*.14);
  const tw=v===0?W-(x+iw)-140:Math.round(W*.72);
  const ty=v===0?y+20:y+ih+46;
  const ttl=mk('div','tx',esc(SCENE.title||''));
  const fs=fit(SCENE.title,v===0?62:64);
  Object.assign(ttl.style,{left:tx+'px',top:ty+'px',width:tw+'px',fontSize:fs+'px',
    fontWeight:SK.tw,textAlign:v===0?'left':'center',lineHeight:'1.16'});
  add(ttl,t=>{const p=eOut(seg(t,.55,.7));ttl.style.opacity=p;
    ttl.style.transform=`translateY(${(1-p)*24}px)`;});
  sub(SCENE.subtitle, below(ttl,26), 40, v===0?'left':'center', v===0?tx:Math.round(W*.14));
};

/* 10. WARNING — triangle + caps headline (the "NOT IN THE BOX" beat) */
T.warning=()=>{
  const col=SK.warn;
  const s=250;
  const ic=mk('div','',icon('warning',col,s));
  Object.assign(ic.style,{left:(W/2-s/2)+'px',top:(H*.20)+'px'});
  add(ic,t=>{const p=eBack(seg(t,.2,.6));
    ic.style.opacity=cl(seg(t,.2,.3),0,1);
    const sh=Math.sin(t*9)*(1-cl(seg(t,.9,.8),0,1))*5;
    ic.style.transform=`translateX(${sh}px) scale(${cl(p,0,1.05)})`;});
  const ttl=mk('div','tx',esc((SCENE.title||'').toUpperCase()));
  const fs=fit(SCENE.title,84);
  Object.assign(ttl.style,{left:'160px',top:(H*.20+s+50)+'px',width:(W-320)+'px',
    fontSize:fs+'px',fontWeight:'800',textAlign:'center',letterSpacing:'2px',lineHeight:'1.1'});
  add(ttl,t=>{const p=eOut(seg(t,.7,.6));ttl.style.opacity=p;
    ttl.style.transform=`translateY(${(1-p)*22}px)`});
  sub(SCENE.subtitle, below(ttl,30), 44);   /* measured — the caps title wraps */
};

/* 19. SUBSCRIBE — the ask as its own scene: bell rings, button fills, stat lands.
   Written in code rather than generated, so the wording is always right and the
   visual always matches what the narrator is saying at that exact moment. */
T.subscribe=()=>{
  const col=SK.ac[0], s=190;
  headline(SCENE.title,H*.10,72);
  /* the bell swings from its pivot instead of sliding — that reads as ringing */
  const ic=mk('div','',icon('bell',col,s));
  Object.assign(ic.style,{left:(W/2-s/2)+'px',top:(H*.26)+'px',transformOrigin:'50% 14%'});
  add(ic,t=>{
    const inP=eBack(seg(t,.25,.55));
    ic.style.opacity=cl(seg(t,.25,.3),0,1);
    const ring=cl(seg(t,1.0,.25),0,1)*(1-cl(seg(t,3.0,1.1),0,1));
    ic.style.transform=`rotate(${Math.sin(t*15)*13*ring}deg) scale(${cl(inP,0,1.06)})`;
  });
  /* outline first, then the colour sweeps across it left to right */
  const bw=660,bh=124,bx=(W-bw)/2,by=H*.26+s+56;
  const btn=mk('div','','');
  Object.assign(btn.style,{left:bx+'px',top:by+'px',width:bw+'px',height:bh+'px',
    border:`5px solid ${col}`,borderRadius:(bh/2)+'px',overflow:'hidden'});
  const fill=mk('div','','');
  Object.assign(fill.style,{left:'0',top:'0',width:'100%',height:'100%',
    background:col,transformOrigin:'0% 50%'});
  btn.appendChild(fill);
  const lbl=mk('div','tx',esc(SCENE.value||'SUBSCRIBE'));
  Object.assign(lbl.style,{left:bx+'px',top:(by+bh*.22)+'px',width:bw+'px',
    fontSize:'64px',fontWeight:'800',textAlign:'center',letterSpacing:'3px'});
  add(btn,t=>{const p=cl(eBack(seg(t,1.5,.6)),0,1);
    btn.style.opacity=p; btn.style.transform=`scale(${lerp(.9,1,p)})`;
    fill.style.transform=`scaleX(${eOut(seg(t,2.2,.75))})`;});
  add(lbl,t=>{lbl.style.opacity=cl(seg(t,1.8,.3),0,1);
    lbl.style.color=seg(t,2.6,.35)>.5?SK.card:col;});
  sub(SCENE.subtitle,by+bh+56,46);
};

/* 20. COMMENT — bubbles land in from alternating sides with a live caret,
   so the "tell me below" line has something moving behind it. */
T.comment=()=>{
  const col=SK.ac[0];
  headline(SCENE.title,H*.10,72);
  const lines=(SCENE.items||[]).slice(0,3);
  const s=150;
  const ic=mk('div','',icon('chat',col,s));
  Object.assign(ic.style,{left:(W/2-s/2)+'px',top:(H*.24)+'px'});
  add(ic,t=>{const p=cl(eBack(seg(t,.3,.6)),0,1);
    ic.style.opacity=cl(seg(t,.3,.3),0,1);
    ic.style.transform=`translateY(${Math.sin(t*2.2)*7}px) scale(${p})`;});
  let y=H*.24+s+46;
  lines.forEach((it,i)=>{
    const txt=(it&&(it.label||it.text))||String(it||'');
    const right=i%2===1;
    const bw=Math.min(W-360,420+String(txt).length*17);
    const bh=104;
    const bx=right?(W-220-bw):220;
    const box=cardBox(bx,y,bw,bh,1.1+i*.55,30);
    /* the last bubble keeps a gap at its trailing edge so the caret has its own
       room — otherwise it lands on top of the final letter */
    const gap=(i===lines.length-1)?46:0;
    const tx=mk('div','tx',esc(txt));
    Object.assign(tx.style,{left:(bx+34)+'px',top:(y+bh*.26)+'px',width:(bw-68-gap)+'px',
      fontSize:'46px',fontWeight:'700',textAlign:right?'right':'left'});
    add(tx,t=>{const p=eOut(seg(t,1.35+i*.55,.5));
      tx.style.opacity=p; tx.style.transform=`translateX(${(1-p)*(right?24:-24)}px)`;});
    /* caret blinks in the last bubble — deterministic, driven by t */
    if(i===lines.length-1){
      const car=mk('div','','');
      Object.assign(car.style,{left:(bx+bw-66)+'px',top:(y+bh*.24)+'px',
        width:'7px',height:(bh*.5)+'px',background:col});
      add(car,t=>{car.style.opacity=(t>1.9&&Math.floor(t*1.8)%2===0)?.9:0;});
    }
    y+=bh+26;
  });
  if(!lines.length) sub(SCENE.subtitle,H*.60,50);
  else sub(SCENE.subtitle,y+22,44);
};

/* 21. INTROCAP — the opening minute, built as graphics instead of burnt-in subs.
   The teacher's portrait is pinned left; the narration lands on the right as a
   composed block of words, one SRT cue at a time, sized by emphasis. Each cue is
   its own flex box, so the browser does the wrapping and nothing has to be
   measured or guessed — and only the cue being spoken is ever visible. */
T.introcap=()=>{
  /* SK.ac[0] is near-ink on the paper skins, so the emphasis would not read as a
     second colour at all — the warm accent is the one that always contrasts. */
  const col=SK.warn||SK.ac[0];
  const groups=SCENE.groups||[];
  /* the page itself breathes — a 30s hold on a static ground reads as a freeze */
  const gEl=document.getElementById('ground');
  if(gEl){gEl.style.transformOrigin='50% 40%';
    els.push({node:gEl,fn:t=>{gEl.style.transform=`scale(${1+0.05*cl(t/(SCENE.duration||30),0,1)})`}});}
  /* ── left: the portrait, pinned like a print on the page ── */
  const src=SCENE.image||'';
  const pw=Math.round(W*0.34), ph=Math.round(pw*1.22);
  const px=Math.round(W*0.055), py=Math.round(H*.5-ph/2);
  if(src){
    const group=mk('div','');
    Object.assign(group.style,{left:px+'px',top:py+'px',width:pw+'px',height:ph+'px',
      transformOrigin:'50% 50%'});
    const frame=document.createElement('div');
    Object.assign(frame.style,{position:'absolute',inset:'0',background:'#f6efdd',
      padding:'15px',boxShadow:'0 18px 40px rgba(0,0,0,.45)',overflow:'hidden'});
    frame.innerHTML=`<div style="width:100%;height:100%;overflow:hidden">
        <img src="${src}" style="width:100%;height:100%;object-fit:cover;display:block;
        filter:grayscale(.4) contrast(1.08)"></div>`;
    group.appendChild(frame);
    const img=frame.querySelector('img');
    const D=SCENE.duration||30;
    add(group,t=>{const p=eBack(seg(t,.2,.8)),q=cl(p,0,1);
      group.style.opacity=cl(seg(t,.2,.4),0,1);
      const fl=Math.sin(t*1.15)*6*q;          /* never a still photograph */
      group.style.transform=`rotate(${-1.8*q+Math.sin(t*.6)*0.5*q}deg) `+
        `translateY(${(1-q)*60+fl}px) scale(${cl(p,0,1.03)})`;
      /* a slow push for the whole scene so a 30s hold never sits still */
      img.style.transform=`scale(${lerp(1.0,1.14,eInOut(cl(t/D,0,1)))})`;});
    if(typeof tape==='function'){
      tape(pw*0.30-40,-24,pw*0.40,-7,.15,group);
      tape(pw*0.34-40,ph-16,pw*0.36,5,.35,group);
    }
  }
  /* ── right: one composed word-block per spoken cue ── */
  /* A narrow column is what makes the words STACK — at full width the sentence
     lays out as one flat line and the block never becomes a shape. */
  const cx=Math.round(W*0.47), cw=Math.round(W*0.40);
  groups.forEach((g,gi)=>{
    const box=mk('div','tx','');
    Object.assign(box.style,{left:cx+'px',top:'0px',width:cw+'px',height:H+'px',
      display:'flex',flexWrap:'wrap',alignContent:'center',alignItems:'baseline',
      justifyContent:(gi%2?'flex-end':'flex-start'),
      rowGap:'2px',columnGap:'22px',lineHeight:'0.98'});
    const ws=[];
    (g.words||[]).forEach(w=>{
      const sp=document.createElement('span');
      sp.textContent=w.w||'';
      const hero=!!w.hero, small=(w.sz==='s');
      /* three sizes and two colours — that is what makes a block of words read
         as a shape instead of a paragraph */
      const fs=hero?132:(small?46:78);
      Object.assign(sp.style,{display:'inline-block',opacity:'0',
        fontSize:fs+'px',fontWeight:hero?'800':(small?'600':'700'),
        color:hero?col:(small?SK.inks:SK.ink),
        letterSpacing:hero?'-3px':'-.5px',
        willChange:'transform,opacity'});
      box.appendChild(sp);
      ws.push({sp,hero,at:(w.t||0)});
    });
    const t0=g.t0||0, t1=g.t1||(t0+2);
    add(box,t=>{
      /* the block slides up on entry and clears out when its cue is over */
      const inn=eOut(seg(t,t0-0.05,.4));
      const out=cl((t-(t1-0.28))/0.28,0,1);
      box.style.opacity=(1-out);
      box.style.transform=`translateY(${(1-inn)*24-out*26}px)`;
      if(t<t0-0.35||t>t1+0.35){box.style.opacity=0;return;}
      ws.forEach(o=>{
        const p=eOut(seg(t,o.at,.26));
        o.sp.style.opacity=p;
        o.sp.style.transform=`translateY(${(1-p)*(o.hero?24:13)}px) `+
          `scale(${lerp(o.hero?.84:.94,1,cl(eBack(cl(seg(t,o.at,.38),0,1)),0,1))})`;
        if(o.u){o.u.style.transform=`scaleX(${eOut(seg(t,o.at+0.14,.32))})`;}
      });
    });
    /* hand-drawn underline under each hero word — measured from the real
       layout, so it fits the word whatever the font decided */
    ws.forEach(o=>{
      if(!o.hero) return;
      const u=document.createElement('div');
      Object.assign(u.style,{position:'absolute',
        left:o.sp.offsetLeft+'px',top:(o.sp.offsetTop+o.sp.offsetHeight-10)+'px',
        width:o.sp.offsetWidth+'px',height:'10px',background:col,opacity:.85,
        borderRadius:'5px',transform:'scaleX(0)',transformOrigin:'0 50%'});
      box.appendChild(u);
      o.u=u;
    });
  });
};

/* 22. STRIKELIST — rows land one by one, then a red line strikes each through.
   The "everything you tried and none of it worked" scene from the reference:
   each item gets its moment, then is crossed out while the next arrives. */
T.strikelist=()=>{
  const items=(SCENE.items||[]).slice(0,5);
  headline(SCENE.title,H*.08,72);
  const n=items.length||1;
  const rh=Math.min(126,(H*.60)/n-16), gap=16;
  const rw=Math.min(1240,W-560);
  const x0=(W-rw)/2, y0=H*.235;
  items.forEach((it,i)=>{
    const y=y0+i*(rh+gap);
    const row=mk('div','','');
    Object.assign(row.style,{left:x0+'px',top:y+'px',width:rw+'px',height:rh+'px',
      background:SK.card,border:`2px solid ${SK.cl}`,borderRadius:'20px',
      boxShadow:SK.glass?'0 18px 44px rgba(0,0,0,.4)':'0 10px 26px rgba(0,0,0,.18)'});
    if(SK.glass){row.style.backdropFilter='blur(14px)';row.style.webkitBackdropFilter='blur(14px)';}
    const s=Math.round(rh*.55);
    const ic=mk('div','',badge(it.icon||ICON_R[i%ICON_R.length],SK.ac[i%SK.ac.length],s,i+(SCENE.variant|0)));
    Object.assign(ic.style,{left:(x0+24)+'px',top:(y+(rh-s)/2)+'px'});
    const tx=mk('div','tx',esc(it.label||''));
    Object.assign(tx.style,{left:(x0+24+s+26)+'px',top:(y+rh/2-32)+'px',width:(rw-s-170)+'px',
      fontSize:Math.min(52,rh*.42)+'px',fontWeight:'800',lineHeight:'1.05'});
    const st=mk('div','','');
    Object.assign(st.style,{left:(x0+16)+'px',top:(y+rh/2-6)+'px',width:(rw-32)+'px',
      height:'11px',background:SK.warn,borderRadius:'6px',transformOrigin:'0 50%',
      boxShadow:`0 0 18px ${SK.warn}66`,transform:'scaleX(0) rotate(-1.6deg)'});
    const d=.45+i*.55, ds=d+.85;             /* land, then strike */
    const fl=idle(row,'float',i);
    add(row,t=>{const p=eBack(seg(t,d,.5));row.style.opacity=cl(p,0,1);
      fl(t,`scale(${cl(p,0,1.03)})`);});
    add(ic,t=>{const p=eBack(seg(t,d+.08,.5));ic.style.opacity=cl(seg(t,d+.08,.25),0,1);
      ic.style.transform=`scale(${cl(p,0,1.05)})`;});
    add(tx,t=>{const p=eOut(seg(t,d+.14,.4));
      tx.style.opacity=p*(1-0.45*cl(seg(t,ds,.3),0,1));   /* dims once struck */
      tx.style.transform=`translateX(${(1-p)*18}px)`;});
    add(st,t=>{st.style.transform=`scaleX(${eOut(seg(t,ds,.34))}) rotate(-1.6deg)`;});
  });
  sub(SCENE.subtitle, y0+n*(rh+gap)+24, 42);
};

/* 23. ICONGRID — 3-6 icons on a dark pane, each landing with its own motion.
   Built for the "list of things" beats where a sticker collage would be too
   slow: the icons arrive fast, then never stop moving. */
T.icongrid=()=>{
  const items=(SCENE.items||[]).slice(0,6);
  const n=items.length||1;
  headline(SCENE.title,H*.11,74);
  const cols=n<=3?n:3, rows=Math.ceil(n/cols);
  const cw=Math.min(430,(W-320)/cols), ch=Math.min(300,(H*.52)/rows);
  const tw=cols*cw, x0=(W-tw)/2, y0=H*.30;
  items.forEach((it,i)=>{
    const cx=x0+(i%cols)*cw, cy=y0+Math.floor(i/cols)*ch;
    const col=SK.ac[i%SK.ac.length];
    const s=Math.round(Math.min(cw,ch)*0.44);
    const ring=mk('div','','');
    const rs=s+52;
    Object.assign(ring.style,{left:(cx+cw/2-rs/2)+'px',top:(cy+18)+'px',
      width:rs+'px',height:rs+'px',borderRadius:'50%',
      border:`3px solid ${col}`,opacity:'0',
      background:`radial-gradient(circle, ${col}22 0%, transparent 70%)`});
    const ic=mk('div','',icon(it.icon||ICON_R[i%ICON_R.length],col,s));
    Object.assign(ic.style,{left:(cx+cw/2-s/2)+'px',top:(cy+18+(rs-s)/2)+'px'});
    const lb=mk('div','tx',esc(it.label||''));
    Object.assign(lb.style,{left:cx+'px',top:(cy+28+rs)+'px',width:cw+'px',
      fontSize:Math.min(44,cw*0.13)+'px',fontWeight:'800',textAlign:'center'});
    const d=.35+i*.22;
    const fx=FX_NAMES[(i+(SCENE.variant|0)*2)%FX_NAMES.length];
    const drive=idle(ic,fx,i*1.6);
    add(ring,t=>{const p=cl(eBack(seg(t,d,.5)),0,1);
      ring.style.opacity=String(p*0.85);
      ring.style.transform=`scale(${lerp(.7,1,p)}) rotate(${Math.sin(t*.5+i)*4}deg)`;});
    add(ic,t=>{const p=cl(eBack(seg(t,d+.06,.5)),0,1);
      ic.style.opacity=String(cl(seg(t,d+.06,.3),0,1));
      drive(t,`scale(${lerp(.6,1,p)})`,cl(seg(t,d+.55,.5),0,1));});
    add(lb,t=>{const p=eOut(seg(t,d+.18,.4));lb.style.opacity=String(p);
      lb.style.transform=`translateY(${(1-p)*14}px)`;});
  });
  sub(SCENE.subtitle,y0+rows*ch+34,42);
};

/* 24. QUOTEMARK — one line of text held inside a huge quotation mark that
   draws itself. For the beats that are a single sentence worth sitting with. */
T.quotemark=()=>{
  const col=SK.ac[0];
  const q=mk('div','','“');
  Object.assign(q.style,{left:(W*.10)+'px',top:(H*.10)+'px',
    fontSize:'420px',lineHeight:'0.8',color:col,opacity:'0',
    fontFamily:SK.tf,fontWeight:'900'});
  add(q,t=>{const p=cl(eBack(seg(t,.15,.7)),0,1);
    q.style.opacity=String(p*0.28);
    q.style.transform=`translateY(${(1-p)*40+Math.sin(t*0.8)*8}px) scale(${lerp(.8,1,p)})`;});
  const txt=mk('div','tx',esc(SCENE.title||''));
  const fs=fit(SCENE.title,96);
  Object.assign(txt.style,{left:(W*.16)+'px',top:(H*.34)+'px',width:(W*.68)+'px',
    fontSize:fs+'px',fontWeight:'800',lineHeight:'1.18',color:SK.ink});
  add(txt,t=>{const p=eOut(seg(t,.55,.8));txt.style.opacity=String(p);
    txt.style.transform=`translateY(${(1-p)*26}px)`;});
  const rule=mk('div','','');
  Object.assign(rule.style,{left:(W*.16)+'px',top:(H*.34-34)+'px',width:'220px',
    height:'8px',background:col,borderRadius:'4px',transformOrigin:'0 50%',
    transform:'scaleX(0)'});
  add(rule,t=>{rule.style.transform=`scaleX(${eOut(seg(t,.35,.5))})`;});
  sub(SCENE.subtitle,H*.72,44);
};

/* 25. DECKFAN — 3-5 cards fanned out in perspective, dealt one at a time.
   The video analysis ranked this shape the single biggest contributor to a
   "premium" feel, and nothing else here works in depth: every other template
   is flat. Each card keeps drifting in 3D once it lands. */
T.deckfan=()=>{
  const items=(SCENE.items||[]).slice(0,5);
  const n=items.length||1;
  headline(SCENE.title,H*.10,70);
  const stage3=mk('div','','');
  Object.assign(stage3.style,{left:'0px',top:'0px',width:W+'px',height:H+'px',
    perspective:'1500px',perspectiveOrigin:'50% 45%'});
  add(stage3,()=>{});
  const cw=Math.min(430,(W-260)/n*1.32), chh=Math.round(cw*1.28);
  const cy=H*.30;
  const spread=Math.min((W-cw-220)/Math.max(1,n-1), cw*0.92);
  const x0=(W-((n-1)*spread+cw))/2;
  items.forEach((it,i)=>{
    const mid=(n-1)/2, off=i-mid;
    const card=mk('div','','');
    Object.assign(card.style,{left:(x0+i*spread)+'px',top:cy+'px',
      width:cw+'px',height:chh+'px',borderRadius:'26px',
      background:SK.card,border:`2px solid ${SK.cl}`,
      boxShadow:'0 30px 70px rgba(0,0,0,.55)',
      transformStyle:'preserve-3d',opacity:'0',
      overflow:'hidden'});
    if(SK.glass){card.style.backdropFilter='blur(18px) saturate(1.2)';
      card.style.webkitBackdropFilter='blur(18px) saturate(1.2)';}
    stage3.appendChild(card);
    const col=SK.ac[i%SK.ac.length];
    const s=Math.round(cw*0.40);
    const ic=document.createElement('div');
    ic.innerHTML=icon(it.icon||ICON_R[i%ICON_R.length],col,s);
    Object.assign(ic.style,{position:'absolute',left:((cw-s)/2)+'px',top:(chh*0.20)+'px'});
    card.appendChild(ic);
    const lb=document.createElement('div');
    lb.textContent=it.label||'';
    Object.assign(lb.style,{position:'absolute',left:'18px',top:(chh*0.62)+'px',
      width:(cw-36)+'px',textAlign:'center',fontFamily:SK.tf,color:SK.ink,
      fontSize:Math.min(46,cw*0.13)+'px',fontWeight:'800',lineHeight:'1.1'});
    card.appendChild(lb);
    if(it.text){
      const tt=document.createElement('div');
      tt.textContent=it.text;
      Object.assign(tt.style,{position:'absolute',left:'22px',top:(chh*0.76)+'px',
        width:(cw-44)+'px',textAlign:'center',fontFamily:SK.tf,color:SK.inks,
        fontSize:Math.max(34,Math.min(38,cw*0.10))+'px',fontWeight:'600',lineHeight:'1.3'});
      card.appendChild(tt);
    }
    const d=.40+i*.16;                       /* dealt, 5 frames apart */
    els.push({node:card,fn:t=>{
      const p=cl(eBack(seg(t,d,.62)),0,1);
      card.style.opacity=String(cl(seg(t,d,.3),0,1));
      /* fans out from a stacked centre into its slot, tilting as it goes */
      const ry=lerp(0,off*13,p), rz=lerp(0,off*4.5,p);
      const dx=lerp(-off*spread,0,p), dy=lerp(44,0,p);
      const fl=Math.sin(t*0.9+i*1.3)*7*p;    /* keeps floating after landing */
      card.style.transform=
        `translate3d(${dx}px,${dy+fl}px,${lerp(-260,-Math.abs(off)*46,p)}px) `+
        `rotateY(${ry}deg) rotateZ(${rz}deg) scale(${lerp(.82,1,p)})`;
    }});
  });
  sub(SCENE.subtitle,cy+chh+46,42);
};

/* 26. TIMELINE — a horizontal spine that draws itself, nodes lighting up in
   order. For anything sequential where "steps" would look like a list. */
T.timeline=()=>{
  const items=(SCENE.items||[]).slice(0,5);
  const n=items.length||1;
  headline(SCENE.title,H*.12,70);
  const y=H*.50, x0=W*.12, x1=W*.88, span=x1-x0;
  const spine=mk('div','','');
  Object.assign(spine.style,{left:x0+'px',top:(y-5)+'px',width:span+'px',height:'10px',
    background:SK.inks,opacity:'.35',borderRadius:'5px',
    transformOrigin:'0 50%',transform:'scaleX(0)'});
  add(spine,t=>{spine.style.transform=`scaleX(${eOut(seg(t,.25,.8))})`;});
  const lit=mk('div','','');
  Object.assign(lit.style,{left:x0+'px',top:(y-6)+'px',width:span+'px',height:'12px',
    background:SK.ac[0],borderRadius:'6px',boxShadow:`0 0 22px ${SK.ac[0]}88`,
    transformOrigin:'0 50%',transform:'scaleX(0)'});
  add(lit,t=>{lit.style.transform=`scaleX(${eInOut(cl(seg(t,.55,2.6),0,1))})`;});
  items.forEach((it,i)=>{
    const cx=n===1?(x0+span/2):(x0+span*(i/(n-1)));
    const col=SK.ac[i%SK.ac.length];
    const r=54;
    const node=mk('div','','');
    Object.assign(node.style,{left:(cx-r)+'px',top:(y-r)+'px',width:(r*2)+'px',
      height:(r*2)+'px',borderRadius:'50%',background:SK.bg2||SK.card,
      border:`5px solid ${col}`,opacity:'0',boxShadow:`0 0 26px ${col}77`});
    const num=document.createElement('div');
    num.textContent=String(i+1);
    Object.assign(num.style,{position:'absolute',inset:'0',display:'flex',
      alignItems:'center',justifyContent:'center',fontFamily:SK.tf,
      fontSize:'42px',fontWeight:'900',color:col});
    node.appendChild(num);
    const up=(i%2===0);
    const lb=mk('div','tx',esc(it.label||''));
    Object.assign(lb.style,{left:(cx-170)+'px',top:(up?y-r-120:y+r+34)+'px',
      width:'340px',textAlign:'center',fontSize:'40px',fontWeight:'800',lineHeight:'1.12'});
    const d=.6+i*.34;
    add(node,t=>{const p=cl(eBack(seg(t,d,.5)),0,1);
      node.style.opacity=String(cl(seg(t,d,.26),0,1));
      node.style.transform=`scale(${lerp(.4,1,p)}) translateY(${Math.sin(t*1.2+i)*4}px)`;});
    add(lb,t=>{const p=eOut(seg(t,d+.14,.42));lb.style.opacity=String(p);
      lb.style.transform=`translateY(${(1-p)*(up?-18:18)}px)`;});
  });
  sub(SCENE.subtitle,H*.80,42);
};

/* 27. SPLITREVEAL — two halves slam in from opposite edges and a bright seam
   wipes down the join. The hardest-hitting way to put two things against
   each other; `compare` is the calm version of the same idea. */
T.splitreveal=()=>{
  const a=(SCENE.items||[])[0]||{label:'Before'}, b=(SCENE.items||[])[1]||{label:'After'};
  const mid=W/2;
  [[a,-1,SK.warn],[b,1,SK.good]].forEach(([it,dir,col],i)=>{
    const half=mk('div','','');
    Object.assign(half.style,{left:(i?mid:0)+'px',top:'0px',width:mid+'px',height:H+'px',
      background:i?`linear-gradient(160deg, ${col}1f, transparent 62%)`
                  :`linear-gradient(200deg, ${col}1f, transparent 62%)`,
      opacity:'0'});
    const d=.2+i*.12;
    add(half,t=>{const p=cl(eOut(seg(t,d,.5)),0,1);
      half.style.opacity=String(p);
      half.style.transform=`translateX(${(1-p)*dir*mid}px)`;});
    const s=190;
    const ic=mk('div','',icon(it.icon||(i?'check':'xmark'),col,s));
    Object.assign(ic.style,{left:(i?mid+(mid-s)/2:(mid-s)/2)+'px',top:(H*.24)+'px'});
    const drive=idle(ic,i?'beat':'shake',i*2);
    add(ic,t=>{const p=cl(eBack(seg(t,d+.18,.55)),0,1);
      ic.style.opacity=String(cl(seg(t,d+.18,.3),0,1));
      drive(t,`translateX(${(1-p)*dir*90}px) scale(${lerp(.7,1,p)})`,cl(seg(t,d+.7,.5),0,1));});
    const lb=mk('div','tx',esc(it.label||''));
    Object.assign(lb.style,{left:(i?mid+30:30)+'px',top:(H*.24+s+40)+'px',
      width:(mid-60)+'px',textAlign:'center',fontSize:'72px',fontWeight:'900',
      color:col,lineHeight:'1.05'});
    add(lb,t=>{const p=eOut(seg(t,d+.3,.45));lb.style.opacity=String(p);
      lb.style.transform=`translateY(${(1-p)*22}px)`;});
    if(it.text){
      const tx=mk('div','tx soft',esc(it.text));
      Object.assign(tx.style,{left:(i?mid+60:60)+'px',top:(H*.24+s+130)+'px',
        width:(mid-120)+'px',textAlign:'center',fontSize:'38px',fontWeight:'600',
        lineHeight:'1.32'});
      add(tx,t=>{tx.style.opacity=String(eOut(seg(t,d+.42,.45)));});
    }
  });
  const seam=mk('div','','');
  Object.assign(seam.style,{left:(mid-4)+'px',top:'0px',width:'8px',height:H+'px',
    background:SK.ink,opacity:'.55',transformOrigin:'50% 0',transform:'scaleY(0)'});
  add(seam,t=>{seam.style.transform=`scaleY(${eOut(seg(t,.42,.6))})`;});
  headline(SCENE.title,H*.06,62);
  sub(SCENE.subtitle,H*.86,42);
};

/* ── background decoration (per skin) ─────────────────────────────────── */
const deco=document.getElementById('deco');
const decoEls=[];
function buildDeco(){
  const kind=SK.deco||'';
  if(kind==='grid'||kind==='grid_warm'){
    const gl=kind==='grid_warm'?'rgba(232,163,61,0.07)':'rgba(140,170,255,0.09)';
    /* faint blueprint grid that drifts, plus three glow blobs orbiting slowly —
       the backdrop itself is alive before a single element lands */
    const g=mk('div','');
    Object.assign(g.style,{left:'-140px',top:'-140px',width:(W+280)+'px',height:(H+280)+'px',
      backgroundImage:`linear-gradient(${gl} 1px, transparent 1px),`+
                      `linear-gradient(90deg, ${gl} 1px, transparent 1px)`,
      backgroundSize:'120px 120px'});
    deco.appendChild(g);
    decoEls.push(t=>{g.style.transform=`translate(${(t*5)%120}px,${(t*3)%120}px)`});
    [[W*.16,H*.22,SK.ac[0]],[W*.86,H*.74,SK.ac[4]],[W*.58,H*.12,SK.ac[1]]].forEach(([x,y,c],i)=>{
      const r=480, b=mk('div','');
      Object.assign(b.style,{left:(x-r)+'px',top:(y-r)+'px',width:r*2+'px',height:r*2+'px',
        borderRadius:'50%',background:`radial-gradient(circle, ${c}2e 0%, transparent 66%)`});
      deco.appendChild(b);
      decoEls.push(t=>{b.style.transform=`translate(${Math.sin(t*.3+i*2.1)*34}px,${Math.cos(t*.22+i)*26}px)`});
    });
  }
  if(kind==='stars'){
    /* a deterministic starfield + two slow nebula glows */
    for(let i=0;i<120;i++){
      const s=1+rnd()*3.4, x=rnd()*W, y=rnd()*H;
      const n=mk('div','');
      Object.assign(n.style,{left:x+'px',top:y+'px',width:s+'px',height:s+'px',
        borderRadius:'50%',background:i%7===0?SK.ac[0]:'#fff',
        boxShadow:`0 0 ${s*3}px rgba(255,255,255,.7)`});
      deco.appendChild(n);
      const ph=rnd()*6.28, sp=.6+rnd()*1.1;
      decoEls.push(t=>{n.style.opacity=(.25+.55*(0.5+0.5*Math.sin(t*sp+ph))).toFixed(3)});
    }
    [[W*.22,H*.28,SK.ac[1]],[W*.80,H*.68,SK.ac[2]]].forEach(([x,y,c],i)=>{
      const r=560;
      const n=mk('div','');
      Object.assign(n.style,{left:(x-r)+'px',top:(y-r)+'px',width:(r*2)+'px',height:(r*2)+'px',
        borderRadius:'50%',background:`radial-gradient(circle, ${c}2e 0%, transparent 68%)`});
      deco.appendChild(n);
      decoEls.push(t=>{n.style.transform=`translateY(${Math.sin(t*.25+i)*16}px)`});
    });
  } else if(kind==='rays'){
    /* soft shafts of light from above — the "divine light" ground */
    const g=mk('div','');
    Object.assign(g.style,{left:'0px',top:'0px',width:W+'px',height:H+'px',
      background:`radial-gradient(ellipse 74% 62% at 50% 0%, ${SK.ac[0]}3e 0%, transparent 72%)`});
    deco.appendChild(g);
    for(let i=0;i<7;i++){
      const x=W*(.12+i*.13)+rnd()*60, w=90+rnd()*150;
      const n=mk('div','');
      Object.assign(n.style,{left:x+'px',top:'-140px',width:w+'px',height:(H+280)+'px',
        background:`linear-gradient(to bottom, ${SK.ac[0]}38, transparent 78%)`,
        transform:`rotate(${-9+i*3}deg)`,transformOrigin:'top center',filter:'blur(38px)'});
      deco.appendChild(n);
      const ph=rnd()*6.28;
      decoEls.push(t=>{n.style.opacity=(.5+.26*Math.sin(t*.5+ph)).toFixed(3)});
    }
  } else if(kind==='dust'){
    /* slow motes drifting up through the dark */
    for(let i=0;i<60;i++){
      const s=1.5+rnd()*3, x=rnd()*W, y=rnd()*H, sp=6+rnd()*14;
      const n=mk('div','');
      Object.assign(n.style,{left:x+'px',top:y+'px',width:s+'px',height:s+'px',
        borderRadius:'50%',background:SK.inks,opacity:.28});
      deco.appendChild(n);
      decoEls.push(t=>{n.style.transform=`translateY(${-((t*sp)%(H+40))}px)`});
    }
    const v=mk('div','');
    Object.assign(v.style,{left:'0px',top:'0px',width:W+'px',height:H+'px',
      background:'radial-gradient(ellipse 62% 58% at 50% 48%, transparent 32%, rgba(0,0,0,.82) 100%)'});
    deco.appendChild(v);
  }
}
buildDeco();

/* ── build + drive ────────────────────────────────────────────────────── */
const ICON_R=__ICONR__;
(T[SCENE.template]||T.title)();

/* paper texture strength (grain always, crumple only on the paper skin) */
const grainEl=document.getElementById('gr'), crumpEl=document.getElementById('crump');
grainEl.setAttribute('opacity', SK.paper?'0.16':'0.05');
crumpEl.setAttribute('opacity', SK.paper?'0.13':'0.0');

window.renderFrame=function(t){
  /* global fade-in / fade-out so segments cut cleanly into the video */
  const D=SCENE.duration||12;
  const fin=cl(t/0.45,0,1), fout=cl((D-t)/0.5,0,1);
  stage.style.opacity=Math.min(fin,fout);
  /* slow drifting push on the whole composition, so a scene is never a static
     slide once its elements have landed */
  const pr=cl(t/D,0,1), dr=SCENE.drift||0;
  /* CAMERA — it moves the PICTURE, and the picture only.
     The drift used to be applied to #layer, which is the type. Eleven pixels
     spread over ten seconds is about a fifth of a pixel per frame, and Chromium
     re-rasterises glyphs at every new subpixel offset, so the letters crawled
     and shimmered instead of sliding. Reading the used matrix back frame by
     frame: the transform did not change AT ALL on 60% of frames and then moved
     on the rest — the stutter you could see twice a second.
     A photograph and a texture have no glyph grid to fight, so the move lives
     on the ground and the decoration, and the type stays pixel-locked. */
  const wob=Math.sin(t*0.62+dr)*0.5+Math.sin(t*0.23+dr*1.7)*0.5;   /* -1..1 */
  const wob2=Math.sin(t*0.41+dr*2.3)*0.5+Math.sin(t*0.17+dr)*0.5;
  const panX=wob*18, panY=wob2*13;
  if(SCENE.intro){
    /* The opening beat earns a real move: a push out of a tight crop that
       settles. It is fast enough that resampling never reads as crawl, so the
       type is carried along with it — that is the point of the shot. */
    const p=eOut(cl(t/2.2,0,1));
    /* The type rides the settle and then STOPS. Carrying it on with the slow
       residual drift is the same fifth-of-a-pixel crawl as everywhere else —
       measured, it left the intro's text frozen on 60% of frames. */
    stage.style.transform = p>=0.99 ? 'none'   /* snap, don't asymptote */
      : `scale(${lerp(1.16,1.0,p)}) translate(${(1-p)*-26}px, ${(1-p)*18}px) `
       +`rotate(${(1-p)*-1.1}deg)`;
    const tf=`scale(${lerp(1.16,1.0,p)*lerp(1.0,1.05,eInOut(pr))}) `
            +`translate(${(1-p)*-26+panX*0.6}px, ${(1-p)*18+panY*0.6+(0.5-pr)*10}px) `
            +`rotate(${(1-p)*-1.1}deg)`;
    for(const c of camEls){ if(c) c.style.transform=tf; }
  } else {
    stage.style.transform='none';          /* type does not drift. ever. */
    const tf=`scale(${lerp(1.09,1.15,eInOut(pr))}) `   /* headroom for pan+rotate */
            +`translate(${panX}px, ${panY+(0.5-pr)*10}px) `
            +`rotate(${Math.sin(pr*2.6+dr)*0.3}deg)`;
    for(const c of camEls){ if(c) c.style.transform=tf; }
  }
  for(const d of decoEls){ try{ d(t); }catch(err){} }
  for(const e of els){ try{ e.fn(t); }catch(err){} }
  /* a slow brightness breath, continuous like everything else */
  stage.style.filter=`brightness(${(1.0+0.018*Math.sin(t*0.45+dr)).toFixed(3)})`;
};
window.renderFrame(0);
window.__ready=true;
</script></body></html>"""


def _ac(skin: dict, i: int) -> str:
    """Accent number `i`, wrapping round when a style lists fewer than the engine asks for."""
    a = skin.get("accent") or ["#C9B896"]
    return a[i % len(a)]


def _skin_js(skin: dict) -> str:
    return json.dumps({
        "ink": skin["ink"], "inks": skin["ink_soft"], "ac": skin["accent"],
        "card": skin["card"], "cl": skin["card_line"], "paper": skin["paper"],
        # A hand-written style may list fewer accents than the five the shipped ones
        # carry. Wrapping round beats crashing: a missing fourth colour used to take
        # down the whole render at the graphics step, minutes into a paid job.
        "deco": skin.get("deco", ""), "glass": skin.get("glass", False),
        "warn": skin.get("warn") or _ac(skin, 3), "good": skin.get("good") or _ac(skin, 2),
        "tw": skin["title_weight"], "tf": skin["title_font"],
        "hf": skin.get("hand_font", ""),
    })


# ── Font Awesome ────────────────────────────────────────────────────────────
# 1400 solid icons. The whole table is 800 kB, far too much to inline into every
# scene, so a scene only ever carries the handful it actually draws.
_FA_FILE = _ASSETS / "icons_fa.json"
_FA: dict = {}
# What a writer is likely to ask for, pointed at the icon that actually exists.
_FA_ALIASES = {
    "broken chain": "link-slash", "chain": "link", "unlocked": "lock-open",
    "locked": "lock", "open door": "door-open", "closed door": "door-closed",
    "mountain": "mountain-sun", "voice": "microphone-lines", "speak": "comment",
    "prayer": "hands-praying", "hands": "hands", "bible": "book-bible",
    "light": "lightbulb", "sun": "sun", "moon": "moon", "star": "star",
    "money": "sack-dollar", "bill": "file-invoice-dollar", "coin": "coins",
    "debt": "file-invoice-dollar", "gate": "dungeon", "wall": "block-brick",
    "mirror": "clone", "seed": "seedling", "growth": "arrow-trend-up",
    "fear": "triangle-exclamation", "mind": "brain", "heart": "heart",
    "eye": "eye", "clock": "clock", "night": "cloud-moon", "sleep": "bed",
    "path": "route", "road": "road", "key": "key", "crown": "crown",
    "shield": "shield-halved", "fire": "fire", "water": "droplet",
    "storm": "cloud-bolt", "wind": "wind", "chart": "chart-line",
    "person": "user", "people": "users", "house": "house", "bank": "building-columns",
    "phone": "mobile-screen", "letter": "envelope", "signature": "signature",
    "trophy": "trophy", "target": "bullseye", "check": "check", "cross": "xmark",
    "warning": "triangle-exclamation", "search": "magnifying-glass",
    "bell": "bell", "chat": "comment-dots", "book": "book-open", "doc": "file-lines",
    "bulb": "lightbulb", "trend": "arrow-trend-up", "globe": "earth-americas",
    "bolt": "bolt", "card": "credit-card", "box": "box", "gear": "gear",
    "calendar": "calendar-days", "arrow": "arrow-right",
    "sound wave": "wave-square",
    "wave": "wave-square",
    "chains": "link-slash",
    "door": "door-open",
    "scroll": "scroll",
    "crown of thorns": "crown",
    "lightning": "bolt",
    "sword": "khanda",
    "anchor": "anchor",
    "footsteps": "shoe-prints",
    "hourglass": "hourglass-half",
    "mask": "mask",
    "ladder": "stairs",
    "bridge": "bridge",
    "compass": "compass",
    "map": "map",
    "tree": "tree",
    "cloud": "cloud",
}


def _fa_table() -> dict:
    if not _FA and _FA_FILE.exists():
        try:
            _FA.update(json.loads(_FA_FILE.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return _FA


def fa_icon(name: str):
    """Best Font Awesome match for a free-text name, or None.

    Writers name a concept ("broken chain", "open door"), not an icon slug, so
    an exact lookup would miss almost every time. Aliases catch the common ones
    and difflib takes the rest — a near miss still draws something sensible,
    which beats falling back to the same generic pictogram over and over.
    """
    tbl = _fa_table()
    if not tbl:
        return None
    q = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower()).strip()
    if not q:
        return None
    for cand in (q, q.replace(" ", "-"), _FA_ALIASES.get(q, "")):
        if cand and cand in tbl:
            return tbl[cand]
    # last word often carries the noun: "a broken chain" -> "chain"
    tail = q.split()[-1] if q.split() else ""
    if tail in _FA_ALIASES and _FA_ALIASES[tail] in tbl:
        return tbl[_FA_ALIASES[tail]]
    hit = difflib.get_close_matches(q.replace(" ", "-"), tbl.keys(), n=1, cutoff=0.62)
    if not hit and tail:
        hit = difflib.get_close_matches(tail, tbl.keys(), n=1, cutoff=0.72)
    return tbl[hit[0]] if hit else None


def _scene_icons(scene: dict) -> dict:
    """Only the icons this scene names — the full table is far too big to ship."""
    out = dict(ICONS)
    wanted = set()
    for it in (scene.get("items") or []):
        if isinstance(it, dict) and it.get("icon"):
            wanted.add(str(it["icon"]))
    for k in ("icon", "center_icon"):
        if scene.get(k):
            wanted.add(str(scene[k]))
    for name in wanted:
        if name in out:
            continue
        got = fa_icon(name)
        if got:
            out[name] = got          # dict form: {"d":…, "vb":…}
    return out


_FONT_CSS_CACHE = {"css": None}


def _bundled_font_css() -> str:
    """@font-face rules for assets/fonts/, inlined as base64.

    Chromium resolves font-family against the SYSTEM, and Inter is not on a
    stock Mac or Windows — the page would quietly render in whatever generic
    sans is around, with different metrics from the ones every layout here was
    measured against. Embedding sidesteps the machine entirely.
    """
    if _FONT_CSS_CACHE["css"] is not None:
        return _FONT_CSS_CACHE["css"]
    d = _ASSETS / "fonts"
    out = []
    if d.is_dir():
        # Only the faces a loaded channel actually asks for. Embedding the whole
        # folder put 2.7MB of base64 into every scene page, and most of it was
        # for the caption weights, which libass renders — not Chromium.
        wanted = " ".join(str(b.get("title_font", "")) + " " + str(b.get("hand_font", ""))
                          for b in BRAND.values()).lower()
        wanted = re.sub(r"[^a-z ]", "", wanted)
        for f in sorted(d.glob("*.ttf")) + sorted(d.glob("*.otf")):
            # lower BEFORE stripping, or every capital becomes a gap and
            # "Inter-Black" turns into " nter lack"
            plain = re.sub(r"[^a-z]+", " ",
                           re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f.stem).lower()).strip()
            if wanted and plain not in wanted:
                continue
            # "InterDisplay-Black" -> family "Inter Display Black", so a style
            # file naming the face the way a designer would just works.
            fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f.stem).replace("-", " ")
            b64 = base64.b64encode(f.read_bytes()).decode()
            mime = "font/otf" if f.suffix == ".otf" else "font/ttf"
            out.append(f"@font-face{{font-family:'{fam}';font-weight:1 1000;"
                       f"src:url(data:{mime};base64,{b64});font-display:block}}")
    _FONT_CSS_CACHE["css"] = "".join(out)
    return _FONT_CSS_CACHE["css"]


def build_html(scene: dict, w: int = W, h: int = H) -> str:
    """One self-contained page for a single scene.

    Chromium renders from a string with no file access, so every sticker,
    photograph and paper ground has to be inlined as a data: URI.
    """
    skin = brand_skin(SKINS.get(scene.get("skin", "paper"), SKINS["paper"]),
                      scene.get("brand", ""))
    sc = dict(scene)
    sc["icons"] = _scene_icons(scene)
    names = sc.pop("cutout_names", None)
    if names is not None:
        keep = [n for n in names if n in CUTOUTS]
        sc["cutouts"] = [image_data_uri(CUTOUTS[n], 400) for n in keep]
        # Data-URI images decode asynchronously, so the page cannot measure a
        # sticker's height while it is being built. Ship the aspect ratio so the
        # layout is exact from the first frame.
        sc["cut_ar"] = [_aspect(CUTOUTS[n]) for n in keep]
    if sc.get("ground_path"):
        sc["ground"] = image_data_uri(sc.pop("ground_path"), 1920)
    # A dark ground needs the paper card behind each sticker to hold contrast.
    if sc.get("card") and PAPER_BG.exists():
        sc["card_tex"] = image_data_uri(PAPER_BG, 560)
    return (_HTML
            .replace("__SCENE__", json.dumps(sc))
            .replace("__SKIN__", _skin_js(skin))
            .replace("__ICONR__", json.dumps(ICON_ROTATION))
            .replace("__FONTS__", _bundled_font_css())
            .replace("__TFONT__", skin["title_font"])
            .replace("__INKSOFT__", skin["ink_soft"])
            .replace("__INK__", skin["ink"])
            .replace("__BG2__", skin["bg2"])
            .replace("__BG__", skin["bg"])
            .replace("__W__", str(w))
            .replace("__H__", str(h)))


# ════════════════════════════════════════════════════════════════════════════
# RENDER  (same deterministic Chromium loop as mindmap.py)
# ════════════════════════════════════════════════════════════════════════════
def _frames_to_mp4(frames_dir: Path, out_mp4: Path, fps: int) -> None:
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                    "-i", str(frames_dir / "f_%05d.jpg"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(fps),
                    str(out_mp4)], check=True, capture_output=True)


def render_scenes(jobs: list, fps: int = FPS, w: int = W, h: int = H,
                  workdir: Path = None, workers: int = 4) -> list:
    """jobs = [(scene_dict, out_path)]. Renders each scene to its own mp4."""
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    prepared = [(build_html(sc, w, h), float(sc.get("duration", 12)), Path(out),
                 tmp / f"frames_{i:03d}") for i, (sc, out) in enumerate(jobs)]

    def _sweep():
        """Frames are a scratch product — a 46-scene video writes ~20k JPEGs.
        They were only removed on the happy path, so every crashed run left its
        frames behind until the disk filled and Chromium could no longer start."""
        shutil.rmtree(tmp, ignore_errors=True)
    workers = max(1, min(workers, len(prepared)))
    chunks = [prepared[i::workers] for i in range(workers)]

    def do(chunk):
        """Render one chunk. The Playwright driver occasionally fails to come up
        under parallel load ('PlaywrightContextManager has no attribute
        _playwright'), and a browser that will not start must never throw away a
        job that already cost 15 minutes of image generation — so retry it."""
        from playwright.sync_api import sync_playwright
        last = None
        for attempt in range(4):
            pw = None
            try:
                with _PW_START:
                    pw = sync_playwright().start()
                _render_chunk(pw, chunk, w, h, fps)
                return
            except Exception as e:                      # noqa: BLE001 - driver flakiness
                last = e
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:                   # noqa: BLE001
                        pass
                time.sleep(4.0 * (attempt + 1))   # let the machine breathe
        raise RuntimeError(f"motion: the browser never started, after 4 tries "
                           f"(the machine is probably overloaded): {last}")

    def _render_chunk(pw, chunk, w, h, fps):
        try:
            browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu",
                                               "--font-render-hinting=none"])
            clip = {"x": 0, "y": 0, "width": w, "height": h}
            for html, dur, out, fdir in chunk:
                # a retry must resume, not redo what already rendered
                if out.exists() and out.stat().st_size > 10_000:
                    continue
                fdir.mkdir(parents=True, exist_ok=True)
                # Fresh page per scene — reusing one leaves window.__ready true
                # from the previous scene and the new one renders empty.
                page = browser.new_page(viewport={"width": w, "height": h},
                                        device_scale_factor=1)
                page.set_content(html, wait_until="load")
                page.wait_for_function("window.__ready===true", timeout=45000)
                n = max(1, int(round(dur * fps)))
                for k in range(n):
                    page.evaluate("(t)=>window.renderFrame(t)", k / fps)
                    page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"),
                                    type="jpeg", quality=92, clip=clip)
                _frames_to_mp4(fdir, out, fps)
                shutil.rmtree(fdir, ignore_errors=True)   # keep the footprint flat
                page.close()
            browser.close()
        finally:
            pw.stop()

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(do, chunks))
    finally:
        _sweep()                       # runs on success AND on failure
    return [Path(out) for _, out in jobs]


# ════════════════════════════════════════════════════════════════════════════
# SPEC NORMALISATION  —  turn loose Claude JSON into a safe scene
# ════════════════════════════════════════════════════════════════════════════
TEMPLATES = ["title", "stat", "bars", "crowd", "flow", "cards",
             "checklist", "compare", "steps", "warning", "reveal", "image", "typing",
             "collage", "photonote", "scatter", "pillars", "opener",
             "subscribe", "comment", "introcap", "strikelist",
             "icongrid", "quotemark", "deckfan", "timeline", "splitreveal"]
# Templates that carry a list of items — used to repair a bad template choice.
_NEEDS_ITEMS = {"bars", "flow", "cards", "checklist", "compare", "steps", "reveal",
                "strikelist", "icongrid", "deckfan", "timeline", "splitreveal"}


def scene_from_spec(spec: dict, index: int, duration: float, seed: int = 0) -> dict:
    """Validate/repair one Claude scene spec so the renderer can never blow up."""
    spec = spec if isinstance(spec, dict) else {}
    tpl = str(spec.get("template", "")).strip().lower()
    items = [i for i in (spec.get("items") or []) if isinstance(i, dict)][:8]
    for it in items:                                   # keep text short + safe
        it["label"] = str(it.get("label", ""))[:42]
        it["text"] = str(it.get("text", ""))[:90]
        # keep any name Font Awesome can resolve, not just the 40 originals —
        # this is what lets the model ask for "hourglass" or "broken chain"
        ic = it.get("icon")
        it["icon"] = ic if (ic in ICONS or fa_icon(ic)) else None
    if tpl not in TEMPLATES:
        tpl = TEMPLATES[index % len(TEMPLATES)]
    if tpl in _NEEDS_ITEMS and len(items) < 2:         # not enough to draw it
        tpl = "stat" if spec.get("value") else "title"
    if tpl == "image" and not spec.get("image"):      # no picture reached this scene
        tpl = "title"
    if tpl == "compare" and len(items) > 2:
        items = items[:2]
    return {
        "template": tpl,
        "skin": spec.get("skin") if spec.get("skin") in SKINS
                else SKIN_ROTATION[(index + seed) % len(SKIN_ROTATION)],
        "title": str(spec.get("title", ""))[:110],
        "subtitle": str(spec.get("subtitle", ""))[:130],
        "value": str(spec.get("value", ""))[:12],
        "count": spec.get("count"),
        "highlight": spec.get("highlight"),
        "items": items,
        # The model's sticker choices were being dropped here, so the pipeline
        # always saw an empty list and padded with arbitrary artwork — which is
        # why captions never matched their pictures.
        "cutouts": [str(c) for c in (spec.get("cutouts") or [])][:8],
        "image": spec.get("image") or "",     # data: URI, attached by the pipeline
        # timed word blocks for `introcap`, built from the real subtitle file
        "groups": spec.get("groups") or [],
        "variant": (index + seed) % 4,
        "drift": ((index + seed) % 5) * 1.1,
        "duration": float(duration),
        "seed": 7 + index * 13 + seed * 101,
    }


_URI_CACHE: dict = {}


def image_data_uri(path, max_w: int = 0) -> str:
    """Inline a picture as a data: URI. Chromium renders each scene from a
    string with no file access, so it has to travel inside the HTML.

    `max_w` downscales first. The stickers are ~850px wide but get drawn at a
    third of that; inlining them full size made a single scene's HTML 2.3 MB
    and an eight-sticker scene timed out before it could even finish loading.
    """
    import base64
    import io
    import mimetypes
    p = Path(path)
    if not p.exists():
        return ""
    key = (str(p), max_w)
    if key in _URI_CACHE:
        return _URI_CACHE[key]
    data, mime = p.read_bytes(), (mimetypes.guess_type(p.name)[0] or "image/jpeg")
    if max_w:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            if im.width > max_w:
                im = im.resize((max_w, round(im.height * max_w / im.width)),
                               Image.LANCZOS)
                buf = io.BytesIO()
                if im.mode in ("RGBA", "LA", "P"):
                    # The cut-outs are black-and-white halftone, so grey+alpha is
                    # visually identical and about a fifth of the bytes — the
                    # colour channels were pure overhead in the inlined HTML.
                    im = im.convert("RGBA")
                    rgb = im.convert("RGB")
                    grey = max(rgb.getextrema(), key=lambda c: c[1] - c[0])
                    spread = max(abs(a[0] - b[0]) + abs(a[1] - b[1])
                                 for a in rgb.getextrema() for b in rgb.getextrema())
                    im.convert("LA" if spread < 26 else "RGBA").save(
                        buf, "PNG", optimize=True)
                    mime = "image/png"
                else:
                    im.convert("RGB").save(buf, "JPEG", quality=86, optimize=True)
                    mime = "image/jpeg"
                data = buf.getvalue()
        except Exception:                       # noqa: BLE001 - fall back to full size
            pass
    uri = f"data:{mime};base64," + base64.b64encode(data).decode()
    _URI_CACHE[key] = uri
    return uri


def _aspect(path) -> float:
    """height / width of an image, so a caption can be placed under it."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return round(im.height / max(1, im.width), 4)
    except Exception:                            # noqa: BLE001
        return 1.0


# Per-style page: which ground a scene is pasted onto, and whether it is dark.
STYLE_GROUND: dict = {}   # the page a channel's scenes are built on
DARK_STYLES: set = set()  # channels whose ground is dark (set by styles.py)


def style_ground(style: str):
    """Path to the page a style's scenes are built on, or None.

    A style file may give either a bare filename (resolved under assets/) or a
    ready-made Path — styles.py hands over the latter.
    """
    name = STYLE_GROUND.get(style)
    if not name:
        return None
    p = Path(name) if Path(name).is_absolute() else _ASSETS / name
    return p if p.exists() else None


def style_is_dark(style: str) -> bool:
    return style in DARK_STYLES


def plan_scenes(specs: list, duration: float, style: str = "", seed: int = 0) -> list:
    """Normalise a whole run of scene specs and keep neighbours from matching.

    Scripts naturally yield long stretches of one template ("title" especially),
    and the model will not reliably vary it on instruction alone. So the run is
    balanced here instead: a scene that repeats its predecessor gets a different
    layout variant AND the opposite skin, so back-to-back scenes never read as
    the same slide twice.
    """
    palette = STYLE_SKINS.get(style)          # a style pins its own look
    out = []
    for i, sp in enumerate(specs):
        # `seed` is derived from the video title, so the same scene laid out in
        # two different videos gets different variants, rotations and jitter —
        # otherwise every episode animates in exactly the same order.
        sc = scene_from_spec(sp if isinstance(sp, dict) else {}, i, duration, seed)
        if palette:
            sc["skin"] = palette[i % len(palette)]
        sc["brand"] = style
        prev = out[-1] if out else None
        if prev and prev["template"] == sc["template"]:
            sc["variant"] = (prev.get("variant", 0) + 1 + seed) % 4
            if palette and len(palette) > 1:
                sc["skin"] = palette[(i + 1) % len(palette)]
            elif not palette:
                sc["skin"] = "clean" if prev["skin"] == "paper" else "paper"
        # Runs LAST on purpose: the neighbour-variant branch above can reassign
        # the skin, which used to hand a dark pane back to a sticker template
        # after this check had already passed.
        if sc.get("skin") in ("glass", "noir"):
            if sc["template"] in ("opener", "introcap", "photonote"):
                sc["skin"] = (palette or ["paper"])[0]
            elif sc["template"] in ("collage", "scatter", "pillars"):
                items = sc.get("items") or []
                pick = ["icongrid", "deckfan", "timeline"]
                sc["template"] = (pick[(i + seed) % len(pick)] if len(items) >= 3
                                  else "splitreveal" if len(items) == 2 else "quotemark")
                sc.pop("cutouts", None)
        out.append(sc)
    if out:
        out[0]["intro"] = True            # the opening beat animates differently
    return out


if __name__ == "__main__":
    # Smoke test: render one of every template to ./_motion_demo
    out = Path("_motion_demo")
    out.mkdir(exist_ok=True)
    demo = {
        "title": {"title": "Two eclipses, fourteen days apart", "subtitle": "and you are standing in between"},
        "stat": {"title": "The window is closing", "value": "7", "subtitle": "days until the second eclipse"},
        "bars": {"title": "Where the pressure lands", "items": [
            {"label": "Money", "value": 3}, {"label": "Work", "value": 5},
            {"label": "Love", "value": 2}, {"label": "Home", "value": 4}]},
        "crowd": {"title": "Most people feel it", "count": 60, "highlight": 22,
                  "subtitle": "but only a few know why"},
        "flow": {"title": "How the energy moves", "items": [
            {"label": "Eclipse", "icon": "moon"}, {"label": "You", "icon": "person"},
            {"label": "Change", "icon": "bolt"}]},
        "cards": {"title": "Three things to watch", "items": [
            {"label": "Endings", "text": "Something quietly finishes", "icon": "clock"},
            {"label": "Signals", "text": "Repeated numbers and names", "icon": "eye"},
            {"label": "Openings", "text": "A door you stopped knocking on", "icon": "key"}]},
        "checklist": {"title": "Do this before the 7th", "items": [
            {"label": "Write down what you keep avoiding"},
            {"label": "Say the thing you have been holding"},
            {"label": "Let one old plan die"}]},
        "compare": {"title": "Before and after the eclipse", "items": [
            {"label": "Before", "text": "Holding on to a version that already ended"},
            {"label": "After", "text": "Room for the thing that was waiting"}]},
        "steps": {"title": "The next seven days", "items": [
            {"label": "Notice", "text": "What keeps repeating"},
            {"label": "Release", "text": "The story around it"},
            {"label": "Move", "text": "One real step"}]},
        "warning": {"title": "Do not start something new", "subtitle": "eclipse portals hide the full picture"},
    }
    jobs = []
    for i, (tpl, spec) in enumerate(demo.items()):
        spec["template"] = tpl
        jobs.append((scene_from_spec(spec, i, 6.0), out / f"{i:02d}_{tpl}.mp4"))
    print("rendering", len(jobs), "demo scenes ->", out)
    render_scenes(jobs, workdir=out / "_frames", workers=4)
    print("done")
