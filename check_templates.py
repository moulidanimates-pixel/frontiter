#!/usr/bin/env python3
"""check_templates.py — load every motion template in a browser and catch JS errors.

A template that throws never sets `window.__ready`, so the renderer waits the
full 45-second timeout, retries four times, and the whole job dies with
"the browser never started" — a message that blames the browser for a one-line
mistake in a scene. This renders one page per template per skin and reports the
actual error, in seconds, before anything is queued.

    python check_templates.py                # every template, default skins
    python check_templates.py --all-skins    # every template on every skin
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_video as mv  # noqa: E402
import motion  # noqa: E402
import styles  # noqa: E402

styles.apply_to_engine(mv, motion)   # channels are files, so load them first

# Enough of everything that no template falls back for want of content.
SAMPLE_ITEMS = [
    {"label": "First", "text": "the opening move", "icon": "warning"},
    {"label": "Second", "text": "what follows from it", "icon": "book"},
    {"label": "Third", "text": "where it lands", "icon": "shield"},
]


def sample_spec(tpl: str) -> dict:
    """A spec that exercises `tpl` with every field it might read."""
    if tpl == "introcap":
        return {"template": tpl, "groups": [
            {"t0": 0.4, "t1": 4.0, "words": [
                {"w": "This", "t": 0.4, "hero": False, "sz": "s"},
                {"w": "changes", "t": 1.1, "hero": True, "sz": "m"},
                {"w": "everything", "t": 2.2, "hero": False, "sz": "m"}]},
            {"t0": 4.2, "t1": 8.0, "words": [
                {"w": "and", "t": 4.2, "hero": False, "sz": "s"},
                {"w": "nothing", "t": 4.9, "hero": True, "sz": "m"}]},
        ]}
    if tpl in ("subscribe", "comment"):
        return dict(mv._CTA_COPY[(tpl, False)])
    return {"template": tpl, "title": "A statement worth showing on screen",
            "subtitle": "The line underneath that explains it",
            "value": "72%", "count": 5, "highlight": "one",
            "items": SAMPLE_ITEMS,
            "cutouts": ["ancient_pilar", "old_book", "hand"]}


def run(styles: list) -> int:
    from playwright.sync_api import sync_playwright
    bad = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-gpu"])
        for style in styles:
            ground = motion.style_ground(style)
            faces = motion.person_photos(style)
            print(f"\n── style {style} ──")
            for tpl in motion.TEMPLATES:
                sc = motion.plan_scenes([sample_spec(tpl)], 9.0, style=style, seed=3)[0]
                sc["template"] = tpl          # the planner may swap; force it back
                if tpl == "introcap":
                    sc["groups"] = sample_spec(tpl)["groups"]
                if ground:
                    sc["ground_path"] = str(ground)
                sc["card"] = motion.style_is_dark(style)
                if faces:
                    sc["image"] = motion.image_data_uri(faces[0], 700)
                if tpl in ("collage", "scatter", "pillars"):
                    got = motion.pick_cutouts(sc.get("cutouts") or [], 4, 1, strict=False)
                    if got:
                        sc["cutout_names"] = got

                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                errs: list = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                try:
                    page.set_content(motion.build_html(sc, 1920, 1080), wait_until="load")
                    page.wait_for_function("window.__ready===true", timeout=12000)
                    print(f"  ✓ {tpl}")
                except Exception:
                    bad += 1
                    why = errs[0][:150] if errs else "page never became ready (no console error)"
                    print(f"  ✗ {tpl:12} {why}")
                finally:
                    page.close()
        browser.close()
    return bad


def main() -> None:
    # Whatever channels this install actually has — a hard-coded list would
    # test channels the buyer does not own and skip the ones they do.
    have = sorted(motion.STYLE_SKINS) or [""]
    bad = run(sorted(motion.SKINS) if "--all-skins" in sys.argv else have)
    print(f"\n{'ALL TEMPLATES OK' if not bad else f'{bad} TEMPLATES FAILED'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
