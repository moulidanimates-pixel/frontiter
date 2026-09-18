#!/usr/bin/env python3
"""preview.py — see one animation in seconds, without making a whole video.

    python preview.py quotemark "The intensity of your judgment is a map of you"
    python preview.py deckfan "Three things you buried" --items "Desire,Weakness,Talent"
    python preview.py --all                    # every animation, one reel + one sheet
    python preview.py quotemark --style jung --seconds 8

No API keys, no voice, no stock footage: the scene is rendered straight through
Chromium exactly the way a real video renders it, then opened. Next to every
clip it also writes a PNG of a late frame — Claude Code can open that image and
SEE what it built, which is what makes changing an animation by chat workable.

This is the loop for building or changing an animation:
    edit  ->  python check_templates.py  ->  python preview.py <name>  ->  look  ->  repeat
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_video as mv  # noqa: E402
import motion  # noqa: E402
import styles  # noqa: E402

OUT = HERE / "preview"
TEXTS = ["the opening move", "what follows from it", "where it lands",
         "what it costs you", "what is left after"]
ICONS = ["eye", "brain", "shield", "key", "moon"]


def spec_for(tpl: str, title: str, subtitle: str, labels: list) -> dict:
    """A scene spec that gives `tpl` every field it might read."""
    if tpl in ("subscribe", "comment"):
        return dict(mv._CTA_COPY[(tpl, False)])
    if tpl == "introcap":
        words = title.split() or ["Preview"]
        return {"template": tpl, "groups": [{"t0": 0.3, "t1": 5.5, "words": [
            {"w": w, "t": 0.3 + i * 0.35, "hero": i == len(words) // 2, "sz": "m"}
            for i, w in enumerate(words)]}]}
    labels = labels or ["First", "Second", "Third"]
    items = [{"label": lab, "text": TEXTS[i % len(TEXTS)], "icon": ICONS[i % len(ICONS)]}
             for i, lab in enumerate(labels)]
    return {"template": tpl, "title": title, "subtitle": subtitle, "items": items,
            "value": "72%", "count": len(items), "highlight": labels[0]}


def scene(tpl: str, style: str, seconds: float, title: str, subtitle: str, labels: list) -> dict:
    spec = spec_for(tpl, title, subtitle, labels)
    sc = motion.plan_scenes([spec], seconds, style=style, seed=7)[0]
    sc["template"] = tpl                    # the planner swaps "repeats"; this one is wanted
    sc["duration"] = seconds
    if tpl == "introcap":
        sc["groups"] = spec["groups"]
    ground = motion.style_ground(style)
    if ground:
        sc["ground_path"] = str(ground)
    sc["card"] = motion.style_is_dark(style)
    faces = motion.person_photos(style)
    if faces:
        sc["image"] = motion.image_data_uri(faces[0], 700)
    # Stickers chosen the way a real render chooses them: pillars are always the
    # column, the opener carries six, collage and scatter take what they asked for.
    if tpl == "pillars":
        sc["cutout_names"] = motion.pick_cutouts(["ancient_pilar"] * 3, 3, 1, strict=True)
    elif tpl in ("collage", "scatter", "opener"):
        got = motion.pick_cutouts(sc.get("cutouts") or [], {"collage": 3, "scatter": 8, "opener": 6}[tpl], 1)
        if got:
            sc["cutout_names"] = got
    return sc


def snapshot(mp4: Path, at: float, png: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(mp4),
                    "-frames:v", "1", str(png)], check=False)


def open_file(p: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        elif os.name == "nt":
            os.startfile(str(p))                                  # noqa: S606
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception:                                             # noqa: BLE001
        print(f"  open it yourself: {p}")


def sheet(pngs: list, names: list, dest: Path) -> None:
    """Every animation on one image, labelled — one glance instead of a reel."""
    from PIL import Image, ImageDraw, ImageFont
    cols, w, h = 4, 480, 270
    rows = (len(pngs) + cols - 1) // cols
    board = Image.new("RGB", (cols * w, rows * (h + 34)), (12, 11, 14))
    font_file = mv._font_file("Inter Bold") or mv._font_file("Inter Black")
    font = ImageFont.truetype(font_file, 20) if font_file else ImageFont.load_default()
    d = ImageDraw.Draw(board)
    for i, (p, n) in enumerate(zip(pngs, names)):
        x, y = (i % cols) * w, (i // cols) * (h + 34)
        if p.exists():
            board.paste(Image.open(p).convert("RGB").resize((w, h)), (x, y + 34))
        d.text((x + 10, y + 7), n, fill=(255, 190, 90), font=font)
    board.save(dest)


def main() -> None:
    names = styles.apply_to_engine(mv, motion)
    ap = argparse.ArgumentParser(description="Preview one animation, or all of them.")
    ap.add_argument("template", nargs="?", help=f"one of: {', '.join(motion.TEMPLATES)}")
    ap.add_argument("title", nargs="?", default="The intensity of your judgment is a map of you")
    ap.add_argument("--subtitle", default="the line underneath that explains it")
    ap.add_argument("--items", default="", help="comma-separated labels, e.g. 'Desire,Weakness,Talent'")
    ap.add_argument("--style", default=names[0] if names else "", help=f"channel: {', '.join(names)}")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--all", action="store_true", help="render every animation")
    ap.add_argument("--no-open", action="store_true", help="don't open the result (for Claude Code)")
    a = ap.parse_args()

    if a.style not in names:
        sys.exit(f"No channel '{a.style}'. Channels: {', '.join(names) or 'none — see CLAUDE.md'}")
    todo = list(motion.TEMPLATES) if a.all else [a.template]
    if not a.all and a.template not in motion.TEMPLATES:
        sys.exit(f"No animation '{a.template}'. Animations: {', '.join(motion.TEMPLATES)}\n"
                 f"A new one must be in motion.TEMPLATES — see CLAUDE.md, 'Adding an animation'.")
    labels = [x.strip() for x in a.items.split(",") if x.strip()]

    OUT.mkdir(exist_ok=True)
    jobs = [(scene(t, a.style, a.seconds, a.title, a.subtitle, labels), OUT / f"{t}.mp4") for t in todo]
    print(f"  rendering {len(jobs)} animation(s) in '{a.style}'…")
    motion.render_scenes(jobs, workdir=Path(tempfile.mkdtemp(dir=OUT)), workers=min(4, len(jobs)))

    pngs = []
    for t in todo:
        png = OUT / f"{t}.png"
        snapshot(OUT / f"{t}.mp4", a.seconds * 0.72, png)
        pngs.append(png)
        print(f"  preview/{t}.mp4   preview/{t}.png")

    if a.all:
        reel = OUT / "_all.mp4"
        mv._concat_segments([OUT / f"{t}.mp4" for t in todo], reel)
        sheet(pngs, todo, OUT / "_all.png")
        print(f"  preview/_all.mp4  (in order: {', '.join(todo)})\n  preview/_all.png  (every animation on one image)")
        target = reel
    else:
        target = OUT / f"{todo[0]}.mp4"
    if not a.no_open:
        open_file(target)


if __name__ == "__main__":
    main()
