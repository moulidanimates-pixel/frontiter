#!/usr/bin/env python3
"""rerender_timeline.py — rebuild a finished video's timeline without paying twice.

When only the ASSEMBLY changed — clip ordering, segment rendering, the final mux —
there is no reason to regenerate the script, the voiceover, the stills or the
motion scenes. This drops just the timeline artefacts and re-runs the pipeline
with force off, so everything expensive is served from the job folder.

    python rerender_timeline.py <slug> [<slug> ...]
    python rerender_timeline.py --all-today
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_video as mv  # noqa: E402

# Everything here is rebuilt from cached inputs in seconds; nothing paid for.
DROP_FILES = ("plan.json", "plan_meta.json", "video.mp4", "_visual.mp4", "_video_tmp.mp4",
              "_mixed.wav", "subs_burn.ass", "astro_pexels.json", "pexels.json")
# Re-fetching stock footage costs nothing but bandwidth, and the pool is often
# exactly what needed to change — so it is dropped too.
DROP_DIRS = ("segments", "pexels", "clips", "clip_src")
# The rendered motion clips go too — they are the cheap part (Chromium, no API)
# and the whole point of a re-render is usually that they should look different.
# scenes.json stays, so Claude is NOT asked to design them again.
DROP_GLOBS = ("motion/*.mp4",)


def clean(job: Path) -> None:
    for f in DROP_FILES:
        (job / f).unlink(missing_ok=True)
    for d in DROP_DIRS:
        shutil.rmtree(job / d, ignore_errors=True)
    for g in DROP_GLOBS:
        for f in job.glob(g):
            f.unlink(missing_ok=True)
    # any hardlinked title-named copy points at the old render
    for f in job.glob("*.mp4"):
        if f.name not in ("video.mp4",):
            f.unlink(missing_ok=True)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all-today" in sys.argv:
        import time
        cutoff = time.time() - 24 * 3600
        args = [p.name for p in mv.OUTPUT_ROOT.iterdir()
                if p.is_dir() and (p / "video.mp4").exists()
                and (p / "video.mp4").stat().st_mtime > cutoff]
    if not args:
        sys.exit("usage: python rerender_timeline.py <slug> [...] | --all-today")

    for slug in args:
        job = mv.OUTPUT_ROOT / slug
        if not job.is_dir():
            print(f"  ! {slug}: no such folder, skipping")
            continue
        title_file = job / "title.txt"
        if not title_file.exists():
            print(f"  ! {slug}: title.txt missing, skipping")
            continue
        lines = title_file.read_text(encoding="utf-8").splitlines()
        title = lines[0].strip()
        minutes = 20
        if len(lines) > 1:
            try:
                minutes = int(lines[1].split()[0])
            except (ValueError, IndexError):
                pass
        # written by the pipeline; older jobs fall back to a guess from the slug
        sf = job / "style.txt"
        style = sf.read_text(encoding="utf-8").strip() if sf.exists() else ""
        if style not in mv.prompts.PROMPT_SETS:
            style = next((st for st in mv.prompts.PROMPT_SETS if st in slug),
                         next(iter(mv.prompts.PROMPT_SETS), ""))

        print(f"\n=== {slug}\n    {title}  ({minutes} min, styl {style})")
        clean(job)
        out = mv.run_pipeline(title, minutes, force=False, burn_subs=True,
                              sink=lambda s: print("   ", s), style=style,
                              use_motion=True)
        print(f"    -> {out}")


if __name__ == "__main__":
    main()
