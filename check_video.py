#!/usr/bin/env python3
"""check_video.py — verify a finished video actually got the new treatment.

Reads a job folder and reports on the things that are easy to *believe* are
working and hard to see at a glance: whether the graphics opening was built,
whether the CTA landed where it is spoken, how far apart repeated stock clips
end up, and where the audio sits.

    python check_video.py <slug> [<slug> ...]
    python check_video.py --latest 3
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_video as mv  # noqa: E402
import motion  # noqa: E402
import styles  # noqa: E402

styles.apply_to_engine(mv, motion)   # channels are files, so load them first

OK, BAD, MEH = "✓", "✗", "·"


def _probe(path: Path, entries: str) -> str:
    try:
        return subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace").stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def _mean_volume(path: Path) -> str:
    try:
        r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        p = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
        return f"average {m.group(1)} dB, peak {p.group(1)} dB" if m and p else "?"
    except OSError:
        return "?"


def _clip_spacing(plan: list) -> str:
    """Smallest gap between two uses of the same clip, and how many slots repeat.

    A modulo ring gives a constant gap equal to the pool size and a very even
    histogram; a shuffled bag gives varied gaps with a larger minimum. This is
    the number that tells the two apart without watching the video.
    """
    vids = [(i, e[1]) for i, e in enumerate(plan) if e[0] == "video"]
    if len(vids) < 2:
        return "too few clips to judge"
    last, gaps = {}, []
    for pos, (i, f) in enumerate(vids):
        if f in last:
            gaps.append(pos - last[f])
        last[f] = pos
    uniq = len({f for _, f in vids})
    if not gaps:
        return f"{len(vids)} slots, {uniq} distinct clips — no repeats"
    return (f"{len(vids)} slots, {uniq} distinct clips; "
            f"closest repeat {min(gaps)} slots apart (average {sum(gaps)/len(gaps):.1f})")


def style_of(job: Path) -> str:
    f = job / "style.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def check(slug: str) -> None:
    job = mv.OUTPUT_ROOT / slug
    print(f"\n{'=' * 72}\n{slug}")
    if not job.is_dir():
        print(f"  {BAD} folder does not exist")
        return
    video = job / "video.mp4"
    if not video.exists():
        print(f"  {BAD} video.mp4 missing — the job never finished")
        return

    dur = float(_probe(video, "format=duration") or 0)
    print(f"  {OK} video {dur / 60:.1f} min, {video.stat().st_size // 1048576} MB")

    # 1. graphics opening
    intro = job / "motion" / "introcap.mp4"
    if intro.exists():
        idur = float(_probe(intro, "format=duration") or 0)
        clicks = []
        cf = job / "motion" / "intro_clicks.json"
        if cf.exists():
            try:
                clicks = json.loads(cf.read_text(encoding="utf-8"))
            except ValueError:
                pass
        print(f"  {OK} graphics opening: {idur:.0f}s, {len(clicks)} clicks on highlighted words")
    else:
        print(f"  {MEH} no graphics opening (this channel ships no portrait)")

    # 2. the CTA scenes, and whether the script actually asks
    script = (job / "script.txt").read_text(errors="replace", encoding="utf-8") if (job / "script.txt").exists() else ""
    says_sub = bool(mv._CTA_SUB_RE.search(script))
    says_com = bool(mv._CTA_COM_RE.search(script))
    scenes_f = job / "motion" / "scenes.json"
    tpls = []
    if scenes_f.exists():
        try:
            specs = json.loads(scenes_f.read_text(encoding="utf-8"))
            srt = job / "subs.srt"
            if srt.exists():
                total = mv._audio_dur(job / "audio.mp3")
                step = max(20.0, mv.MOTION_EVERY_MIN * 60.0)
                n = max(1, int(-(-total // step)))
                chunks = ["" for _ in range(n)]
                for st, txt in mv._parse_srt(srt.read_text(encoding="utf-8")):
                    m = int(st // step)
                    if 0 <= m < n:
                        chunks[m] += " " + txt
                specs = specs[:n]
                mv._apply_cta_scenes(specs, chunks, (job / "style.txt").read_text(encoding="utf-8").strip()
                                     if (job / "style.txt").exists() else "")
            tpls = [s.get("template") for s in specs]
        except (ValueError, OSError):
            pass
    cta = [t for t in tpls if t in ("subscribe", "comment")]
    mark = OK if cta else (MEH if not (says_sub or says_com) else BAD)
    print(f"  {mark} CTA: script asks for subscribe={says_sub} comment={says_com}; "
          f"scenes {cta or 'none'}")

    # 3. how varied the graphics are
    if tpls:
        c = Counter(tpls)
        top, n_top = c.most_common(1)[0]
        print(f"  {OK if n_top <= max(2, len(tpls) // 2) else MEH} "
              f"{len(tpls)} scenes, {len(c)} distinct templates "
              f"(most used '{top}' {n_top}×)")

    # 4. stock-clip repetition
    plan_f = job / "plan.json"
    if plan_f.exists():
        try:
            print(f"  {OK} clips: {_clip_spacing(json.loads(plan_f.read_text(encoding='utf-8')))}")
        except ValueError:
            pass

    # 5. graphics cadence — one every ~30s, each a short beat, never a slide
    plan_f2 = job / "plan.json"
    if plan_f2.exists():
        try:
            plan = json.loads(plan_f2.read_text(encoding="utf-8"))
            # The graphics OPENING is a different animal from a mid-video
            # graphic: it is the hook, it runs long on purpose, and holding it
            # to the same ten-second cap would report a violation that isn't one.
            at, t, lens, intro_len = [], 0.0, [], 0.0
            for kind, _path, _leak, d in plan:
                if kind == "motion":
                    if "introcap" in str(_path):
                        intro_len = float(d)
                    else:
                        at.append(t); lens.append(float(d))
                t += float(d)
            if lens:
                gaps = [at[i + 1] - at[i] for i in range(len(at) - 1)]
                longest = max(lens)
                avg_gap = sum(gaps) / len(gaps) if gaps else 0
                cap_ok = longest <= mv.MOTION_MAX_DUR + 0.6
                gap_ok = not gaps or 20 <= avg_gap <= 45
                print(f"  {OK if cap_ok else BAD} graphics: {len(lens)}×, "
                      f"length {min(lens):.0f}–{longest:.0f}s "
                      f"(cap {mv.MOTION_MAX_DUR:.0f}s)")
                if intro_len:
                    print(f"  {MEH} graphics opening (extra): {intro_len:.0f}s "
                          f"(outside the cap — it is the hook, not a scene)")
                print(f"  {OK if gap_ok else MEH} spacing: average {avg_gap:.0f}s "
                      f"(target ~{mv.STYLE_EVERY_MIN.get(style_of(job), 0.5) * 60:.0f}s)")
            else:
                print(f"  {BAD} the plan contains NO graphics at all")
        except (ValueError, TypeError) as e:
            print(f"  {MEH} could not read the plan: {e}")

    # 6. do the graphics speak the same language as the narration?
    # This one is invisible to every other check: cadence, spacing, variety and
    # levels were all green on a video whose headlines were Czech over an
    # English voiceover. Only looking at the frames caught it, so it is measured
    # here now.
    sf2 = job / "motion" / "scenes.json"
    if sf2.exists() and (job / "script.txt").exists():
        try:
            txt = " ".join(str(v) for sp in json.loads(sf2.read_text(encoding="utf-8"))
                           for v in sp.values() if isinstance(v, str))
            letters = sum(c.isalpha() for c in txt) or 1
            dens = sum(txt.lower().count(c) for c in mv._CZ_MARKS) / letters
            g_lang = "Czech" if dens > 0.004 else "English"
            s_lang = "Czech" if "CZECH" in mv._job_language(job, style_of(job)).upper() \
                else "English"
            same = g_lang == s_lang
            print(f"  {OK if same else BAD} language: script {s_lang}, graphics {g_lang}"
                  f"{'' if same else '  << MISMATCH'}")
        except (ValueError, OSError):
            pass

    # 7. audio
    print(f"  {OK} audio: {_mean_volume(video)}")
    named = [f.name for f in job.glob("*.mp4") if f.name not in ("video.mp4", "_visual.mp4")]
    print(f"  {OK if named else MEH} named copy: {named[0] if named else 'missing'}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--latest" in sys.argv:
        i = sys.argv.index("--latest")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 3
        jobs = [p for p in mv.OUTPUT_ROOT.iterdir() if (p / "video.mp4").exists()]
        args = [p.name for p in sorted(jobs, key=lambda p: -(p / "video.mp4").stat().st_mtime)[:n]]
    if not args:
        sys.exit("usage: python check_video.py <slug> [...] | --latest N")
    for slug in args:
        check(slug)
    print()


if __name__ == "__main__":
    main()
