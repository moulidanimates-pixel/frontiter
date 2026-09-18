#!/usr/bin/env python3
"""director.py — the edit plan of a Documentary video.

The tools each used to read the narration on their own and grab moments: the maps module its places,
the photo module its people, the graphics their numbers. Each chose well, but nobody edited — the
same strong moment was claimed three times, the opening stayed plain, and a date graphic landed
twenty seconds after the date was said.

Now one director reads the whole narration and decides, sentence by sentence, what the viewer sees
on top of the real footage, and hands every tool exactly its moments:

    map         an animated map of a place that matters             (maps.py)
    spotlight   a real photo of a real person, place or moment       (photofx.py)
    object      a real product or thing, cut out and flying in       (photofx.py)
    headline    the real article behind a claim, highlighted         (headlines.py)
    graphic     dateline, number, chart, timeline, quote, pivot, question (docgfx.py)
    vox         a moment told as paper collage                        (vox.py)
    text        a label over the footage: a name, a place, a date, a number, a key phrase

The edit follows how documentaries hold attention: the first minute is the hook — every sentence gets
something, a map, a real photo, a date on the date — and after it the video breathes: real footage
carries it, with a collage scene about once a minute, real photos often, a graphic or a map when the
story turns, and text labels on the footage.

Every tool keeps its own renderer and its own checks; the director only decides where each one is used.
The plan is director.json in the video's folder, with the moments each tool received.
"""

import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()
INTRO_S = 60.0          # the hook
CHUNK_S = 150.0         # the rest is directed in stretches of this length, side by side

TOOLS = {
    "map": """- map — an animated map of a place that matters (7 s):
  {"tool": "map", "cue": 3, "words": "Arlington Heights, Illinois", "shot": "pin",
   "places": [{"name": "Arlington Heights", "names": ["Arlington Heights"], "label": "Arlington Heights", "type": "town",
               "country": "United States", "lat": 42.09, "lon": -87.98}],
   "title": "Arlington Heights", "subtitle": "a Chicago suburb, September 1982", "icon": ""}
  Map field rules:
[INSERT MAP RULES HERE]
""",
    "spotlight": """- spotlight — a real photo of a real public person, place or documented event, camera pushing in (5 s):
  {"tool": "spotlight", "cue": 12, "words": "James Burke", "subject": "James Burke", "query": "James Burke Johnson & Johnson CEO 1982"}
  Public figures, famous places and documented events — and anyone the news has photographed as part of this story:
  a celebrity's partner or rumoured partner, an influencer, a model. When the story is about someone's relationships,
  EVERY partner the narration names gets their photo on the words that name them (put the subject's name and the
  person's name in "query" so the news photo is found). NEVER a private person (a victim, a witness, a relative, an
  ordinary employee, a child) — no public photo of them exists; give them a "name" text label instead.
""",
    "object": """- object — a real product or thing the narration names, cut out of a photo and flying in (5 s):
  {"tool": "object", "cue": 7, "words": "Extra-Strength Tylenol", "layout": "solo", "things": ["Extra Strength Tylenol bottle 1982"]}
  layout: "solo" (one), "pair" (two compared), "row" (3-4), "crown" (one + "accent" object landing on "accent_words").
""",
    "headline": """- headline — the real article or encyclopedia page behind a claim, the line highlighted (6.5 s):
  {"tool": "headline", "cue": 20, "words": "recalled thirty-one million bottles", "kind": "news",
   "query": "Johnson & Johnson Tylenol recall 31 million bottles 1982", "claim": "Johnson & Johnson recalled 31 million bottles of Tylenol in 1982.", "lang": "en"}
  kind "news" for reported events, "reference" for encyclopedia facts (query = the article subject).
  When the narration names a page itself — a subreddit, a forum, a company's post — and you know its exact address, give
  it: {"tool": "headline", "cue": 5, "words": "The GTA 6 subreddit", "kind": "news", "url": "https://www.reddit.com/r/GTA6/", "claim": "..."}
""",
    "graphic": """- graphic — a premium animated graphic (5-8 s), one of:
  {"tool": "graphic", "cue": 1, "words": "September 29th, 1982", "type": "dateline", "place": "ARLINGTON HEIGHTS, ILLINOIS", "date": "September 29, 1982", "note": "a family kitchen"}
  {"tool": "graphic", "cue": 18, "words": "thirty-one million bottles", "type": "number", "value": "31000000", "prefix": "", "suffix": "", "unit": "bottles recalled", "context": "pulled from every shelf in America", "source": "Johnson & Johnson, 1982"}
  {"tool": "graphic", "cue": 25, "words": "...", "type": "chart", "title": "...", "unit": "%", "bars": [{"label": "...", "value": 37}], "highlight": 0, "source": "..."}
  {"tool": "graphic", "cue": 30, "words": "...", "type": "timeline", "title": "...", "events": [{"date": "...", "label": "..."}]}
  {"tool": "graphic", "cue": 33, "words": "...", "type": "quote", "quote": "...", "who": "...", "role": "...", "year": "..."}
  {"tool": "graphic", "cue": 9, "words": "...", "type": "pivot", "line": "at most 7 words"}
  {"tool": "graphic", "cue": 14, "words": "...", "type": "question", "line": "at most 6 words"}
  {"tool": "graphic", "cue": 16, "words": "...", "type": "step", "n": "2", "title": "Leave-in conditioner", "note": "on soaking-wet hair"}
  {"tool": "graphic", "cue": 19, "words": "...", "type": "card", "kicker": "Tell your barber", "text": "the exact words to say", "highlight": ["1 to 3 key phrases"]}
  {"tool": "graphic", "cue": 6, "words": "unfollowed each other", "type": "social", "action": "unfollow", "from": "Lamine Yamal", "to": "Alex Padilla", "mutual": true, "note": "late 2024"}
  social = two named people follow ("action": "follow") or unfollow each other on social media — a split, a new couple,
  a feud going public — on the words that say it ("mutual": true when both did). Real names only.
  step = the title card of each step of a routine, list or how-to (on the words that name the step); card = a practical
  tip or the exact words to say to someone, the key phrases highlighted. Both belong to how-to and advice videos.
  Numbers as plain digits. A chart or number always names its real source. Quotes only if documented word for word.
  A part of a whole ("190 of 200 laps") is a number ("value": "190", "unit": "laps led", "context": "of 200"), never a
  chart of the part against the whole; a chart compares three or more real things. A record or a "first" is said as
  true for its time ("a race record in 1965"), never "fastest ever" unless it still stands today.
""",
    "vox": """- vox — a moment of 7 to 14 seconds told as hand-cut paper collage: halftone cutouts of the real people, torn
  archival photos, typewriter labels, big red number cards, pins and red string. From where "words" start to
  where "end_words" end:
  {"tool": "vox", "cue": 10, "words": "Hours before, in Elk Grove Village", "end_cue": 12, "end_words": "dead before 7 a.m."}
""",
    "text": """- text — a label over the footage itself, about 3.5 s, never during another tool's scene:
  {"tool": "text", "cue": 2, "words": "Adam Janus", "style": "name", "text": "ADAM JANUS", "sub": "27, first victim"}
  style "name" (a person: name + who they are), "place" (a place), "date" (a date), "number" (a figure + what it counts:
  "text": "7", "sub": "deaths in 3 days"), "phrase" (a key term the viewer must remember: "text": "POTASSIUM CYANIDE").
""",
}

PROMPT = """You are the director and editor of a premium documentary YouTube video titled "[INSERT TITLE HERE]".
Real footage of the events plays under the whole narration. Your job: decide, sentence by sentence, what else
the viewer sees, using these tools. Be creative — every video should use the tools in fresh ways — but every
choice must SHOW what those exact words say, at the exact moment they are said.

THE TOOLS (one JSON object each, "tool" plus its fields; "cue" and "words" are where it is said — the cue number
and 1 to 6 words copied letter for letter from that cue):

[INSERT TOOLS HERE]
HOW TO EDIT THIS STRETCH ([INSERT RANGE HERE]):
[INSERT BUDGET HERE]
- Match the tool to the words: a person → spotlight or name label; a product or object → object; a place →
  map or place label; a date → dateline graphic or date label; a figure → number graphic or number label; a claim
  a newspaper reported → headline; a turn in the story → pivot or question; a sequence of dates → timeline; a
  documented quote → quote; a scene full of people, places and facts at once → vox.
- Timing is everything: put each tool on the cue and words where its subject is SPOKEN — a date graphic on the date,
  not on the sentence after.
- Scenes (map, spotlight, object, headline, graphic, vox) must not overlap: leave at least 2 seconds between the end
  of one and the start of the next. Text labels go on the footage, or on a spotlight or object (a name on the
  photo) — never on a map, graphic, headline or vox scene, which carry their own words.
- Vary: never the same tool twice in a row, never the same kind of graphic twice in a row, and every scene shows a
  DIFFERENT thing — never two objects or photos of the same car, product or person in one video.
- Premium graphics are the signature of this channel: in every minute, at least one graphic (a number, a timeline,
  a dateline, a chart or a quote) on the figure or date that matters most.
- Maps: "pin" for a city, town, venue, race track or a tiny country (Monaco, Vatican City, Singapore); "region" only
  for a place big enough to see on a map of its surroundings.
- Real facts only: names, dates, numbers, places, quotes you are certain of — best, ones the narration itself says.
[INSERT LOOK HERE][INSERT EXTRA HERE]
Return ONLY a JSON array of tool objects, in time order.

TRANSCRIPT (cue number, [minute:second], text):
[INSERT TRANSCRIPT HERE]"""

INTRO_BUDGET = """- This is the HOOK — the first minute decides whether anyone keeps watching. Make it the richest, most edited
  stretch of the video: something on screen for EVERY sentence besides the footage.
- Use, wherever the narration gives you the material: a MAP of the first place that matters (always, when a real
  place is named — it tells the viewer this is a quality documentary); a dateline graphic on the first date (not on
  top of the map — the map's subtitle can carry the date instead); a spotlight on the first public figure or
  documented event; an object for the product at the centre of the story; a vox collage of 7 to 11 seconds inside
  the first 30 seconds (and, in a video longer than three minutes, a second one before 1:00); a headline — the real
  article, or the real page the narration names — on the first reported fact;
  and text labels on the footage between the scenes for every new name, place, date and figure — at least five
  in this minute when the narration names that many.
- Scenes can follow each other closely here (2 seconds of footage between them is enough).
- A video under 90 seconds opens on its strongest real picture — footage, a spotlight or the real article — never on
  a map; a map comes later and short."""

BODY_BUDGET = """- After the hook the video breathes: real footage carries most of it. For this stretch of about [INSERT MIN HERE]
  minutes use roughly: one vox collage per minute; one or two spotlights or objects per minute; one graphic per
  minute (datelines when time or place changes, numbers for the figures that matter, a timeline for a sequence,
  a pivot or question at a real turn); a map when a new place matters; one or two headlines per minute on the
  reported facts — a statement, a court decision, a lawsuit, a firing, a record — and the real page (a subreddit, a
  forum, a post) when the narration names one; a social graphic whenever people follow or unfollow each other; and
  three to five text labels per minute on the footage between scenes.
- Leave at least 6 seconds of plain footage between scenes."""

LOOK_ASK = """
Also return, as the FIRST object, the look of this story's collage scenes:
{"tool": "look", "accent": "#hex — one strong accent colour that belongs to this story (a brand's colour, a flag, an era)",
 "stage": "one sentence: the paper-collage background of this story — the map, documents and textures it is laid on",
 "paper": "one sentence: the paper and print tone (e.g. yellowed 1980s newsprint, cold clinical white)"}
"""

DURATION = {"map": 7.0, "spotlight": 5.0, "object": 4.8, "headline": 6.5, "graphic": 6.5}
# how long before its words each module starts its scene (maps.LEAD_S, photofx SPOT_LEAD/OBJ_LEAD, headlines.LEAD_S):
# the plan keeps the real intervals apart, not the moments the words are said
LEAD = {"map": 1.6, "spotlight": 0.9, "object": 0.5, "headline": 1.3, "graphic": 0.15}
GRAPHIC_S = {"dateline": 5.0, "number": 6.0, "chart": 8.0, "timeline": 8.5, "quote": 7.5, "pivot": 4.6, "question": 4.2,
             "card": 5.6, "step": 3.2, "social": 5.2}


def tools_on(engine) -> set:
    """The tools Options left ticked for this video (engine._FEATURES), each only if its module is here."""
    f = getattr(engine, "_FEATURES", {}) or {}
    use = lambda k: bool(f.get(k, True)) if f else True
    on = set()
    if use("maps") and (HERE / "maps.py").exists():
        on.add("map")
    if use("spotlight") and (HERE / "photofx.py").exists():
        on.add("spotlight")
    if use("objects") and (HERE / "photofx.py").exists():
        on.add("object")
    if use("headlines") and (HERE / "headlines.py").exists():
        on.add("headline")
    if use("motion"):
        on.update({"graphic", "text"})
    if use("vox") and (HERE / "vox.py").exists():
        on.add("vox")
    return on


def _stamp(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def sentences(script: str, wpm: float = 140.0) -> list:
    """[(start estimate, end estimate, sentence)] — the script as the voice will speak it, timed by word
    count. The director plans from these while the voice is still being recorded; the real times come
    from the subtitles afterwards (plan)."""
    import re as _re
    parts = [x.strip() for x in _re.split(r"(?<=[.!?…])\s+|\n+", script or "") if x.strip()]
    out, t = [], 0.0
    for x in parts:
        d = len(x.split()) / wpm * 60.0
        out.append((round(t, 2), round(t + d, 2), x))
        t += d
    return out


def _chunks(cues: list, total: float) -> list:
    """[(lo, hi, intro)] — the hook, then stretches of CHUNK_S."""
    out = [(0.0, min(total, INTRO_S), True)]
    t = INTRO_S
    while t < total - 5:
        out.append((t, min(total, t + CHUNK_S), False))
        t += CHUNK_S
    return out


def _ask(engine, title: str, cues: list, lo: float, hi: float, intro: bool, style: str, job: Path) -> list:
    import maps
    rules = maps.MOMENTS_PROMPT.split("Field rules:", 1)[1].split("TRANSCRIPT:", 1)[0]
    rules = rules.replace("[INSERT LANGUAGE HERE]", engine._job_language(job, style))
    rows = [f"#{i + 1} [{_stamp(st)}] {txt}" for i, (st, en, txt) in enumerate(cues) if lo - 0.01 <= st < hi]
    if not rows:
        return []
    budget = INTRO_BUDGET if intro else BODY_BUDGET.replace("[INSERT MIN HERE]", f"{(hi - lo) / 60:.1f}")
    on = tools_on(engine)
    tool_block = "".join(TOOLS[k] for k in ("map", "spotlight", "object", "headline", "graphic", "vox", "text") if k in on)
    missing = [k for k in ("map", "spotlight", "object", "headline", "graphic", "vox", "text") if k not in on]
    if missing:
        budget += "\n- Not available in this video, never use: " + ", ".join(missing) + "."
    prompt = (PROMPT.replace("[INSERT TITLE HERE]", title or "")
              .replace("[INSERT TOOLS HERE]", tool_block)
              .replace("[INSERT MAP RULES HERE]", "\n".join("  " + l for l in rules.strip().splitlines()))
              .replace("[INSERT RANGE HERE]", f"{_stamp(lo)} to {_stamp(hi)}")
              .replace("[INSERT BUDGET HERE]", budget)
              .replace("[INSERT LOOK HERE]", LOOK_ASK if intro and "vox" in on else "")
              .replace("[INSERT EXTRA HERE]", engine._extra_block())
              .replace("[INSERT TRANSCRIPT HERE]", "\n".join(rows)))
    try:
        items = engine._json_items(prompt, max_tokens=max(4000, len(rows) * 300))
    except SystemExit as e:
        engine.log(f"  director: {_stamp(lo)}-{_stamp(hi)} did not parse — {str(e)[:100]}")
        return []
    return [x for x in items if isinstance(x, dict) and x.get("tool")]


def _time(engine, stream, rows: list, it: dict, clock: float = 1.0, end: bool = False):
    """Seconds where an item's words are spoken (or, end=True, where its end words finish). `rows` are the
    lines the director was shown — subtitle cues, or script sentences timed by word count, which `clock`
    turns into real seconds."""
    key_cue, key_words = ("end_cue", "end_words") if end else ("cue", "words")
    try:
        k = int(it.get(key_cue) or it.get("cue") or 0) - 1
    except (TypeError, ValueError):
        k = -1
    near = rows[k][0] / clock if 0 <= k < len(rows) else 0.0
    far = rows[k][1] / clock if 0 <= k < len(rows) else 1e9
    slack = 3.0 if clock == 1.0 else 12.0
    t = engine._find_line(stream, str(it.get(key_words) or ""), near, near - slack, far + slack, end=end)
    if t is None and 0 <= k < len(rows) and clock == 1.0:
        t = rows[k][1] if end else rows[k][0]
    return t


def draft(engine, script: str, job: Path, style: str, title: str = "") -> list:
    """The director's raw plan, made from the script alone — so it runs while the voice is recorded.
    Cached in director_raw.json; plan() times it against the subtitles."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(job), threading.Lock())
    with lock:                    # the draft started beside the voice: a second caller waits for it
        return _draft(engine, script, job, style, title)


def _draft(engine, script: str, job: Path, style: str, title: str = "") -> list:
    raw = job / "director_raw.json"
    if raw.exists():
        try:
            return json.loads(raw.read_text(encoding="utf-8"))
        except ValueError:
            pass
    rows = sentences(script, float(getattr(engine, "WPM", 140)))
    if not rows:
        return []
    total = rows[-1][1]
    if not title and (job / "title.txt").exists():
        title = (job / "title.txt").read_text(encoding="utf-8").splitlines()[0]
    chunks = _chunks(rows, total)
    engine.log(f"Claude: directing the edit from the script — the first minute as the hook, "
               f"then {len(chunks) - 1} stretch(es) side by side...")
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
        parts = list(ex.map(lambda c: _ask(engine, title, rows, c[0], c[1], c[2], style, job), chunks))
    items = [x for part in parts for x in part]
    items = [dict(x, _rows="script") for x in items]
    raw.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    return items


def as_label(it: dict):
    """The text label a scene becomes when there is no room for the scene itself — or None."""
    tool, kind = it.get("tool"), str(it.get("type") or "")
    def clean(v, n):
        t = re.sub(r"\s+", " ", str(v or "")).strip()
        return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(",;:-")
    if tool == "graphic" and kind == "number":
        try:
            v = float(str(it.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            return None
        dec = len(str(it.get("value")).split(".")[1]) if "." in str(it.get("value")) else 0
        txt = f"{clean(it.get('prefix'), 4)}{v:,.{dec}f}{clean(it.get('suffix'), 6)}"
        return {"style": "number", "text": txt, "sub": clean(" ".join(x for x in (it.get("unit"), it.get("context")) if x), 44)}
    if tool == "graphic" and kind == "social" and it.get("from") and it.get("to"):
        verb = "UNFOLLOWED" if str(it.get("action") or "unfollow") == "unfollow" else "FOLLOWED"
        return {"style": "phrase", "text": verb, "sub": clean(f"{it.get('from')} · {it.get('to')}", 44)}
    if tool == "graphic" and kind == "chart":
        bars = [b for b in (it.get("bars") or []) if isinstance(b, dict)]
        hl = it.get("highlight") if isinstance(it.get("highlight"), int) else 0
        if not bars:
            return None
        b = bars[hl if 0 <= hl < len(bars) else 0]
        unit = clean(it.get("unit"), 10)
        return {"style": "number", "text": f"{clean(b.get('value'), 12)}{' ' if unit[:1].isalpha() else ''}{unit}",
                "sub": clean(f"{b.get('label') or ''} · {it.get('title') or ''}".strip(" ·"), 44)}
    if tool == "graphic" and kind == "dateline":
        return {"style": "date", "text": clean(it.get("date") or it.get("place"), 30),
                "sub": clean(it.get("place") if it.get("date") else it.get("note"), 44)} if (it.get("date") or it.get("place")) else None
    if tool == "graphic" and kind in ("pivot", "question") and it.get("line"):
        line = re.sub(r"\s+", " ", str(it.get("line"))).strip()
        return {"style": "phrase", "text": line.upper(), "sub": ""} if len(line) <= 34 else None
    if tool == "spotlight" and it.get("subject"):
        return {"style": "name", "text": clean(it.get("subject"), 30).upper(), "sub": ""}
    if tool == "map" and it.get("title"):
        return {"style": "place", "text": clean(it.get("title"), 30).upper(), "sub": clean(it.get("subtitle"), 44)}
    return None


def plan(engine, srt: Path, job: Path, style: str, title: str = "") -> dict:
    """Direct the video and write every tool's moments. Cached in director.json."""
    out = job / "director.json"
    if out.exists():
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            pass
    cues = engine._parse_srt_full(srt.read_text(encoding="utf-8"))
    if not cues:
        return {}
    total = cues[-1][1]
    script = (job / "script.txt").read_text(encoding="utf-8") if (job / "script.txt").exists() else ""
    items = draft(engine, script, job, style, title) if script else []
    rows = sentences(script, float(getattr(engine, "WPM", 140)))
    clock = (rows[-1][1] / total) if rows and total > 0 else 1.0
    clock = clock if 0.5 <= clock <= 2.0 else 1.0
    clock = clock if clock != 1.0 else 1.0000001     # script rows: always the wide search
    stream = engine._spoken_stream(cues)
    look = next((x for x in items if x.get("tool") == "look"), {})
    # time every item, keep scenes apart, labels off the scenes
    scenes, labels = [], []
    for it in items:
        tool = str(it.get("tool")).lower()
        if tool == "look":
            continue
        t = _time(engine, stream, rows, it, clock)
        if t is None:
            continue
        t = float(t)
        it["_w"] = round(t, 2)                       # when its words are said — what the modules time on
        if tool == "vox":
            t1 = _time(engine, stream, rows, it, clock, end=True)
            if t1 is None or t1 - t < 5.0:
                continue
            it["_t"] = round(max(0.0, t - 0.12), 2)
            # a collage in the hook is a beat, not a chapter: the minute has more to show
            it["_end"] = round(min(float(t1) + 0.35, t + (11.0 if t < INTRO_S else 16.0), total - 0.5), 2)
        elif tool == "text":
            it["_t"] = it["_w"]
            labels.append(it)
            continue
        elif tool in DURATION:
            dur = GRAPHIC_S.get(str(it.get("type") or ""), DURATION[tool]) if tool == "graphic" else DURATION[tool]
            if tool == "graphic" and it.get("type") == "quote" and len(str(it.get("quote") or "")) <= 40:
                dur = 4.6                              # three words need no seven seconds
            it["_t"] = round(max(0.0, t - LEAD[tool]), 2)
            it["_end"] = round(it["_t"] + dur, 2)
        else:
            continue
        scenes.append(it)
    scenes.sort(key=lambda x: x["_t"])
    # a dateline on the same moment as a map: the map says the date in its subtitle, and the dateline goes
    for dl in [x for x in scenes if x["tool"] == "graphic" and x.get("type") == "dateline"]:
        mp = next((x for x in scenes if x["tool"] == "map" and abs(x["_w"] - dl["_w"]) <= 6.0), None)
        if mp is not None and (dl.get("date") or "").strip():
            years = set(re.findall(r"\b\d{4}\b", str(dl.get("date"))))
            if not (years and years & set(re.findall(r"\b\d{4}\b", str(mp.get("subtitle") or "")))):
                mp["subtitle"] = " · ".join(v for v in (str(dl.get("date") or "").strip(),
                                                        str(mp.get("subtitle") or "").strip()) if v)[:60]
            scenes.remove(dl)
            engine.log(f"  director: the date {dl.get('date')!r} rides on the map at {_stamp(mp['_w'])}")
    # overlapping scenes: the more telling tool keeps its moment (a map before a dateline on the same
    # words, a collage before a photo); the other may slide a few seconds later if its sentence runs on
    # a real photo of the person is worth as much as a graphic (viewers stay for faces); objects and pages a little less
    rank = {"map": 4.5, "vox": 4.5, "graphic": 4, "spotlight": 4, "object": 3.5, "headline": 3.5}

    def what(x):
        """The thing a scene shows, to catch the same thing twice (three objects of one car)."""
        key = x.get("things") or x.get("subject") or ""
        if not key and isinstance(x.get("places"), list) and x["places"] and isinstance(x["places"][0], dict):
            key = x["places"][0].get("name") or ""
        if isinstance(key, list):
            key = " ".join(str(k) for k in key[:1])
        stop = {"the", "and", "with", "photo", "car", "race", "racing", "number", "real", "old", "new", "image"}
        return {w for w in re.findall(r"[a-z0-9]+", str(key or "").lower()) if len(w) > 1 and w not in stop}

    def same(a, b):
        return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.6

    near_s = max(15.0, min(60.0, total / 4.0))      # "nearby" in a one-minute video is a quarter of it

    def worth(x):
        # variety: every scene of the same kind already kept nearby is worth less than a new kind — except the
        # step cards of a routine, which are its structure: every step gets its card
        near = [k for k in kept if abs(k["_t"] - x["_t"]) < near_s]
        if x["tool"] == "graphic" and x.get("type") in ("step", "card"):
            return (5.0 if x.get("type") == "step" else 4.6) - 1.6 * sum(
                1 for k in near if k["tool"] == "graphic" and k.get("type") == x.get("type") == "card")
        score = rank.get(x["tool"], 1) - 1.6 * sum(1 for k in near if k["tool"] == x["tool"])
        if any(same(what(k), what(x)) for k in kept if k["tool"] in ("object", "spotlight") or x["tool"] == k["tool"]):
            score -= 4.0                      # the same thing again
        return score

    # real footage and text labels need room too: scenes may cover at most this share of each stretch
    # how much of the video scenes may cover (style pacing: a footage-rich gaming edit wants less than a documentary)
    pace = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style, {}) or {}).get("pacing") or {}
    cap_hook, cap_body = float(pace.get("scene_share_hook") or 0.62), float(pace.get("scene_share_body") or 0.42)

    def share_cap(t):
        return cap_hook if t < INTRO_S or total <= INTRO_S + 40.0 else cap_body

    def covered(t):
        if total <= INTRO_S + 40.0:          # a short video is all hook: one stretch
            lo, hi = 0.0, total
        else:
            lo = 0.0 if t < INTRO_S else INTRO_S + CHUNK_S * math.floor((t - INTRO_S) / CHUNK_S)
            hi = min(total, INTRO_S if t < INTRO_S else lo + CHUNK_S)
        span = max(1.0, hi - lo)
        used = sum(max(0.0, min(k["_end"], hi) - max(k["_t"], lo)) for k in kept)
        return used / span, span

    kept, dropped, todo, lost = [], [], list(scenes), []
    if total < 90.0:
        # a short video opens on its strongest real picture: a map in its first seconds becomes a place label
        for x in [x for x in todo if x["tool"] == "map" and x["_t"] < 5.0]:
            todo.remove(x)
            lost.append(x)
            dropped.append(f"map@{_stamp(x['_t'])} (a short video opens on real footage)")
    while todo:
        it = max(todo, key=lambda x: (worth(x), -x["_t"]))
        todo.remove(it)
        if worth(it) < -1.5:
            dropped.append(f"{it['tool']}@{_stamp(it['_t'])}")
            continue
        length = it["_end"] - it["_t"]
        share, span = covered(it["_t"])
        if share + length / span > share_cap(it["_t"]) + 0.02:
            dropped.append(f"{it['tool']}@{_stamp(it['_t'])} (room for footage)")
            lost.append(it)
            continue
        # the hook cuts from scene to scene; later the footage breathes between them
        gap = 0.4 if it["_t"] < INTRO_S else 1.0
        for shift in (0.0, 1.0, 2.0, 3.0, 4.5):
            a, b = it["_t"] + shift, it["_end"] + shift
            if b <= total - 0.3 and not any(a < k["_end"] + gap and k["_t"] < b + gap for k in kept):
                if shift:
                    it = dict(it, _t=round(a, 2), _end=round(a + length, 2), _w=round(it["_w"] + shift, 2))
                kept.append(it)
                break
        else:
            dropped.append(f"{it['tool']}@{_stamp(it['_t'])}")
            lost.append(it)
    kept.sort(key=lambda x: x["_t"])
    toks = lambda v: set(re.findall(r"[a-z0-9]+", engine._fold(str(v or "")))) - {"the", "of", "and"}
    # a spotlight names its person, as a documentary does, unless a label already says who it is
    for sp in [x for x in kept if x["tool"] == "spotlight" and x.get("subject")]:
        name = re.sub(r"\s+", " ", str(sp["subject"])).strip()
        if len(name.split()) <= 4 and not any(abs(x["_t"] - sp["_w"]) < 4.0 and x.get("style") == "name"
                                              and toks(x.get("text")) & toks(name) for x in labels):
            labels.append({"tool": "text", "style": "name", "text": name.upper(), "sub": "", "_t": sp["_w"] + 0.4,
                           "_w": sp["_w"] + 0.4, "_rank": 1})
    # a scene left out for room still had words worth seeing: its figure, date, name or place becomes a label
    have = {str(x.get("text") or "").strip().lower() for x in labels}
    for it in lost:
        lab = as_label(it)
        if lab and lab["text"].lower() not in have:
            have.add(lab["text"].lower())
            labels.append(dict(lab, tool="text", _rank=2, _t=it.get("_w", it["_t"]), _w=it.get("_w", it["_t"])))
    if dropped:
        engine.log(f"  director: {len(dropped)} overlapping scene(s) left out — {', '.join(dropped[:8])}")
    # a label may sit on a real photo or object (a name on the spotlight); never on a scene with its own words
    busy = [(x["_t"] - 0.5, x["_end"] + 0.5) for x in kept if x["tool"] not in ("spotlight", "object")]
    # ...but a name only on the photo of that person: "FATI VÁZQUEZ" still on screen as Bad Gyal's photo comes in
    # names the wrong woman
    pictured = [(x["_t"] - 0.3, x["_end"] + 0.3, toks(x.get("subject") or x.get("things") or x.get("words")))
                for x in kept if x["tool"] in ("spotlight", "object")]
    placed_labels = []

    def sentence_end(t):
        # when the sentence spoken at t ends: its words timed through their cues by their letters
        for st, en, txt in cues:
            if en <= t:
                continue
            ws = (txt or "").split()
            weights = [len(re.sub(r"\W", "", w)) + 1 for w in ws]
            tot, acc = float(sum(weights) or 1), 0
            for word, wt in zip(ws, weights):
                acc += wt
                done = st + (en - st) * acc / tot
                if done > t + 0.05 and re.search(r"[.!?][\"')\]]*$", word):
                    return done
        return None

    def free(t, d, x):
        if x.get("style") == "name" and any(a < t + d and t < b and not (who & toks(x.get("text")))
                                            for a, b, who in pictured):
            return False
        return (t >= 0 and t + d <= total - 0.2 and not any(a < t + d and t < b for a, b in busy)
                and not any(t < y["_t"] + y["dur"] + 0.3 and y["_t"] < t + d + 0.3 for y in placed_labels))

    # the director's own labels first, then the names on spotlights, then the ones made from scenes left out
    for x in sorted(labels, key=lambda x: (x.get("_rank", 0), x["_t"])):
        # on its words if there is room; else a little early, or shorter, or just after the scene in the way —
        # still within a few seconds of its words
        w, spot = x["_t"], None
        after = [b for a, b in busy if a < w + 3.6 and w < b]
        tries = [(w + off, d) for d in (3.6, 3.2, 2.8) for off in (0.0, -0.8, 0.4, 0.8, -1.6, 1.4)] + \
            [(b, 3.2) for b in sorted(after)]
        for t, d in tries:
            if t - w <= 8.0 and free(t, d, x):
                spot = (t, d)
                break
        if not spot:
            # squeezed into the room between the label before and the scene after, never shorter than 2.2 s
            room = [(round(w + k * 0.1, 2), d) for d in (3.4, 3.0, 2.6, 2.2) for k in range(-20, 11)]
            spot = next(((t, d) for t, d in sorted(room, key=lambda td: (-td[1], abs(td[0] - (w - 0.4))))
                         if free(t, d, x)), None)
        if spot:
            t, d = spot
            # a label is gone soon after its sentence: "UNCONFIRMED" must not stay on screen into "Then it gets
            # official", nor a name over the next story's footage (a name keeps a moment longer to be read)
            end, grace = sentence_end(x.get("_w", w)), (1.2 if x.get("style") == "name" else 0.3)
            if end is not None and t + d > end + grace:
                d = round(max(2.2, end + grace - t), 2)
            placed_labels.append(dict(x, _t=round(t, 2), dur=d))
    labels = placed_labels
    # hand each tool its moments, in the shape its own planner writes
    def cue_at(t):
        return next((i + 1 for i, (a, b, _) in enumerate(cues) if a - 0.05 <= t < b + 0.05), len(cues))

    def strip(x):
        d = {k: v for k, v in x.items() if k not in ("tool", "_t", "_end", "_rows")}
        if "_t" in x:
            w = x.get("_w", x["_t"])
            d.pop("_w", None)
            d["cue"] = cue_at(w)
            d["at_s"] = w
            if "end_cue" in d:
                te = _time(engine, stream, rows, x, clock, end=True)
                d["end_cue"] = cue_at(te) if te is not None else d["cue"]
        return d
    # a short video cannot spend a quarter of itself on a map: it runs a fifth of the video, 4.5 seconds at least
    maps_m = [dict(strip(x), **({"dur": round(max(4.5, min(7.0, total * 0.2)), 1)} if total < 90 else {}))
              for x in kept if x["tool"] == "map"]
    photo_m = [dict(strip(x), kind="spotlight" if x["tool"] == "spotlight" else "object")
               for x in kept if x["tool"] in ("spotlight", "object")]
    head_m = [strip(x) for x in kept if x["tool"] == "headline"]
    doc_m = [strip(x) for x in kept if x["tool"] == "graphic"]
    vox_m = [{"t0": x["_t"], "t1": x["_end"],
              "text": " ".join(txt for _, _, txt in engine._cues_between(cues, x["_t"], x["_end"]))}
             for x in kept if x["tool"] == "vox"]
    vox_m = [m for m in vox_m if len(m["text"].split()) >= 6]
    text_m = [{"t": x["_t"], "dur": x.get("dur", 3.6), "style": str(x.get("style") or "phrase"),
               "text": str(x.get("text") or "")[:60], "sub": str(x.get("sub") or "")[:70]} for x in labels if x.get("text")]
    for folder, name, data in (("maps", "moments.json", maps_m), ("photofx", "moments.json", photo_m),
                               ("headlines", "moments.json", head_m), ("vox_scenes", "moments.json", vox_m)):
        (job / folder).mkdir(parents=True, exist_ok=True)
        (job / folder / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    (job / "motion").mkdir(exist_ok=True)
    (job / "motion" / "doc_scenes.json").write_text(json.dumps({"director": True, "items": doc_m}, indent=1,
                                                              ensure_ascii=False), encoding="utf-8")
    if look:
        (job / "vox_scenes" / "look.json").write_text(json.dumps(strip(look), indent=1, ensure_ascii=False),
                                                     encoding="utf-8")
    (job / "overlays.json").write_text(json.dumps(text_m, indent=1, ensure_ascii=False), encoding="utf-8")
    result = {"scenes": [{"tool": x["tool"], "t": x["_t"], "end": x["_end"], "words": x.get("words")} for x in kept],
              "labels": text_m, "look": strip(look)}
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = {}
    for x in kept:
        counts[x["tool"]] = counts.get(x["tool"], 0) + 1
    engine.log("  director: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + f", {len(text_m)} text labels")
    return result
