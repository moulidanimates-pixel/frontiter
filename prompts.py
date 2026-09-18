"""Every Claude prompt the pipeline sends.

These are the ENGINE's prompts — art direction, stock-footage searches,
thumbnails, metadata, motion-graphics design. They are channel-neutral: each
one receives the running channel through [INSERT CHANNEL HERE].

A CHANNEL's own voice — its outline prompt and its script prompt — is not here.
It lives in styles/<name>.json, so a channel can be rewritten without touching
code. See styles/_TEMPLATE.json.

Placeholders are replaced with str.replace() in make_video.py:
  [INSERT TITLE HERE] / [INSERT LENGTH ...] / [INSERT OUTLINE HERE] /
  [INSERT TARGET LENGTH] / [INSERT SRT HERE] / [INSERT SCRIPT HERE]
"""

# ──────────────────────────────────────────────────────────────────────────
# 1) OUTLINE + SCRIPT  ->  see styles/<channel>.json
# ──────────────────────────────────────────────────────────────────────────

# The retention craft every channel's outline and script get on top of their own prompts (a style can pin the
# framework with "script_craft": "story" / "teaching", or switch it off with "off"). The channel's voice, format
# and rules always win; this only makes whatever the channel writes hold the viewer.
SCRIPT_CRAFT_OUTLINE = """

RETENTION CRAFT — apply it inside this channel's own format and voice (the channel's rules above win where they differ):
1. Framework. Decide what this video is, and plan only that:
   - STORY (the video is about someone or something else — a documentary, a history, a scandal, a mystery): the hook
     sets up CHARACTER (who), CONCEPT (the situation) and STAKES (why it matters). Hook types: in medias res, or a
     genuine contradiction.
   - TEACHING (the viewer comes away able to do or understand something): the hook sets up TARGET (who this is for),
     TRANSFORMATION (what they get) and STAKES (what it costs them not to). Hook types: a direct question, or the
     viewer's problem made sharp.
   Mixing the two loses the viewer within seconds.
2. The hook — the first ~50 words, under 15 seconds. Plan for 2 or 3 of these, never all of them:
   - one specific open question the viewer cannot answer without watching;
   - confirmation that this is exactly the video they clicked (the title's promise, said concretely);
   - at least three hard specifics — a date, a number, a name, a place;
   - a contradiction that is true (never a forced one the video cannot pay);
   - stakes that grow: what is lost, and how much.
3. A chain, not a list. Each beat pays off one open question and, within ~10 seconds, opens the next one — so there is
   never a moment where the viewer has what they came for and nothing new to wait for.
4. Order. Strongest material in the middle, second-strongest first, third-strongest last. Never spend the best
   first.
5. The grand payoff — the thing the title promises. Foreshadow it in the hook, about a third of the way in and about
   two-thirds of the way in; deliver it clearly, and let it tie the smaller payoffs together.
6. Context and action. Never more than ~30 seconds of background before something concrete happens again: context,
   event, context, event.
7. Truth first. Never invent, bend or reorder a fact to make a hook or a twist work: every date, time, number and
   contradiction is true and in the order it really happened. When you are not sure, use another mechanism."""

SCRIPT_CRAFT_SCRIPT = """

RETENTION CRAFT — write it inside this channel's own voice and format (the channel's rules above win where they differ):
- No delay: the first sentence is already the video. Never "welcome back", "in this video", "today we will", a
  subscribe request or credentials before the hook; never a generic opening ("this is the story of…", "have you ever
  wondered…").
- Write the hook, then check it before you go on, and rewrite it until it passes:
  1. GAP — a specific question the viewer cannot close without watching?
  2. CONFIRMATION — within the first ~40 words, is it unmistakably the video they clicked?
  3. MECHANISMS — two or three of: the open question, the confirmation, hard specifics, a true contradiction,
     escalating stakes. One is weak; stacking every trick at once reads as desperate.
  4. DENSITY — at least one number, date or name that makes the viewer think?
  5. STAKES — does leaving now cost the viewer something?
  Weak: "This is the story of one of history's strangest disappearances." Strong: the date, the place, the one
  impossible detail, and the question nobody has answered — in the first two sentences.
- Hook killers: the wrong framework, credential dumping, jargon, a generic opening, overexplaining, stakes too small
  to matter.
- After every payoff, open the next question within a sentence or two ("That cost them the case. The next mistake
  cost them far more.").
- Foreshadow the title's big payoff where the plan puts it, then pay it in full — never bury it or leave it out.
- Plain spoken words; explain any term the moment it appears; cut every sentence that only repeats.
- Truth first: no fact is invented, bent or reordered for the hook or a twist — times and dates in the order they
  happened. When unsure of a detail, leave it out rather than guess."""


# ──────────────────────────────────────────────────────────────────────────

# A faceless cosmic-guidance voice speaking in the SECOND person to the viewer
# a different proven structure — reverse-engineered from
# the top performers (tighter ~20 min runtime, dated urgency, comment-driven).
# ──────────────────────────────────────────────────────────────────────────


MINDMAP_BIG_PROMPT = """You turn a whole spoken video SCRIPT into ONE big mindmap that will be animated across the ENTIRE video — the camera walks through it topic by topic as the narrator speaks, and it must help the viewer follow and understand the whole video.

Build a mindmap of the WHOLE script:
- "title": a SHORT 2–4 word central label (the core idea of the entire video)
- "topics": 7–12 main branches IN THE ORDER they are discussed, each:
  - "label": 2–4 word Title Case node label
  - "cue": an EXACT short phrase (3–7 words) copied VERBATIM from the script marking where this topic begins (used to time the camera; it MUST appear word-for-word in the script)
  - "points": 2–4 sub-nodes, each {"label": a punchy 2–4 word label, "detail": a SHORT supporting phrase (max ~7 words), not a sentence} breaking down that topic's actual content

Cover the whole script start-to-end, evenly. Everything must reflect what is actually said. Labels human-readable and punchy.

Return ONLY a JSON array of exactly ONE object:
[{"title":"...","topics":[{"label":"...","cue":"...","points":[{"label":"...","detail":"..."}, ...]}, ...]}]

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


MINDMAP_MINUTES_PROMPT = """You are creating a SEQUENCE of small mindmaps — one per ~1-minute chunk of a spoken video — that break down EXACTLY what the narrator says in that chunk, so the viewer understands it better as they listen.

Below is the transcript split into minute chunks (MINUTE 1, MINUTE 2, ...). For EACH minute, build a clean, scannable mindmap of what is ACTUALLY said in THAT minute:
- "central": a punchy 2–4 word label naming the core idea of that minute
- "points": 3–5 sub-nodes, each {"label": a punchy 2–4 word node label, "detail": a SHORT supporting phrase (max ~7 words) — NOT a sentence, just a quick clarifier} breaking down the real content of that minute. Keep everything short and scannable (this is a visual mindmap, not paragraphs). Pull it from the transcript, not generic filler.

RULES:
- Every minute's mindmap must be DIFFERENT and specific to that minute's transcript. Never repeat the same central/points across minutes.
- Base it strictly on the given chunk. If a chunk is short/transitional, still give a central + 2 points that reflect it.
- Labels are Title Case, punchy, human-readable.

Return ONLY a JSON array with EXACTLY one object per minute, IN ORDER (same count as the number of MINUTE chunks):
[{"central":"...","points":[{"label":"...","detail":"..."}, ...]}, ...]

THE MINUTE CHUNKS:
---
[INSERT CHUNKS HERE]
---"""


MINDMAP_XMIND_PROMPT = """You are turning a spoken video into a SEQUENCE of detailed XMind-style mind maps — ONE per ~1-minute chunk — that a viewer watches like a screen recording of study notes. Each minute's map breaks down, in real study-note depth, what the narrator actually says in that minute.

Below is the transcript split into minute chunks (MINUTE 1, MINUTE 2, ...). For EACH minute, output a nested tree that reads like a real XMind map:
- The ROOT is a short 2–5 word title naming that minute's core idea.
- 3–5 BRANCHES, each a short 2–5 word heading for a sub-theme discussed that minute.
- Under each branch, 1–4 LEAF nodes, each a COMPLETE SENTENCE (or a real quote/definition) capturing an actual point, example, or claim made in that minute — like a bullet in someone's notes. Full sentences, not fragments. This is the whole point: rich, readable text, not 2-word labels.

Rules:
- Pull everything from what is ACTUALLY said in that minute — real content, not filler.
- Keep it scannable on one screen: at most ~5 branches and ~4 leaves per branch. If a minute is dense, pick the most important points.
- Vary structure minute to minute so no two maps look identical.
- Every node uses the field name "label"; branches and the root also have a "children" array; leaves have no children.

Return ONLY a JSON array with one tree object per minute, in order, nothing else:
[{"label":"<minute title>","children":[{"label":"<branch heading>","children":[{"label":"<full sentence>"},{"label":"<full sentence>"}]}, ...]}, ...]

THE MINUTE CHUNKS:
---
[INSERT CHUNKS HERE]
---"""


MINDMAP_XMIND_BIG_PROMPT = """You turn a whole spoken video SCRIPT into ONE big XMind-style mind map that a camera will travel across for the ENTIRE video — panning and zooming to each topic exactly when the narrator reaches it, like a real screen recording of someone walking through an XMind map.

Build ONE tree covering the whole script IN ORDER:
- ROOT: a SHORT 2–5 word central label (the core idea of the whole video).
- "topics": 8–16 main BRANCHES in the ORDER they are discussed. Each branch:
  - "label": a short 2–5 word heading.
  - "cue": an EXACT short phrase (3–7 words) copied VERBATIM from the script marking where this topic begins — used to time the camera, so it MUST appear word-for-word in the script.
  - "children": 2–4 LEAF nodes, each a COMPLETE SENTENCE (or a real quote/definition) capturing an actual point made under that topic — full sentences like study notes, not fragments.

Rules:
- Everything must reflect what is ACTUALLY said. Cover the script start to end, evenly.
- Full-sentence leaves (this is the point — rich readable notes), max ~4 per topic.
- Every node uses the field "label"; the root and topic branches also have a "children" array (and topics also a "cue"); leaves have only "label".

Return ONLY a JSON array with exactly ONE tree object, nothing else:
[{"label":"<central label>","children":[{"label":"<topic heading>","cue":"<verbatim phrase>","children":[{"label":"<full sentence>"},{"label":"<full sentence>"}]}, ...]}]

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


MINDMAP_SKELETON_PROMPT = """You turn a spoken video SCRIPT into a compact MINDMAP outline that will be animated (camera pans across nodes as the narrator reaches each topic).

Read the script and produce:
- a SHORT central title (2–5 words — the whole video's core idea)
- 6–9 TOPICS in the order they are discussed, each with:
  - "label": a punchy 2–4 word node label (Title Case)
  - "detail": ONE short sentence (max ~14 words) for the node's detail box
  - "cue": an EXACT short phrase (3–7 words) copied VERBATIM from the script that marks roughly where the narrator BEGINS talking about this topic (used to time the camera). It must appear word-for-word in the script.

Cover the whole script start-to-end; topics should be evenly spread, not all from the intro.

Return ONLY a JSON array of exactly ONE object, nothing else:
[{"title": "...", "topics": [{"label":"...","detail":"...","cue":"..."}, ...]}]

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


# Switchable prompt styles (the app lets you pick which narrator).


# ──────────────────────────────────────────────────────────────────────────
# 3) AI QUOTE IMAGES  (SRT -> 10 photorealistic "quote written in the real
#    world" images, each tied to a real moment in the audio)
# ──────────────────────────────────────────────────────────────────────────
AI_QUOTE_IMAGES_PROMPT = """You are the art director for a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

Below is the SRT subtitle file for ONE finished video, with real timestamps. Pick EXACTLY 10 short, powerful lines that are ACTUALLY spoken in the video, spread evenly across the whole runtime (some early, some middle, some late — not all clustered together).

For each chosen line, produce a PHOTOREALISTIC image-generation prompt that shows that exact line as if it were physically written in the real world. Vary the surface across the 10 images:
- handwritten in ink on a sheet of white paper lying on a wooden desk under warm lamplight
- written with a marker on a whiteboard
- on a torn notebook page
- on a single sticky note
- on an old typewritten page
- chalk on a blackboard
Always super realistic: shallow depth of field, soft cinematic lighting, real texture, 16:9, NO people, NO faces, NO logos. Only the written words and the surface.

The written text must be SHORT — at most ~8 words. If the spoken line is longer, shorten it to its most powerful 3–8 words, but keep those words VERBATIM from the line. The image prompt MUST contain the exact words to render, in double quotes, and explicitly instruct that the text be spelled correctly and clearly legible.

Return ONLY a JSON array of exactly 10 objects, nothing else (no markdown, no commentary). Each object:
{"time": <number, seconds from the start of the video, taken from that subtitle's start timestamp>, "text": "<the short quote exactly as it should appear written>", "prompt": "<the full photorealistic image-generation prompt, including the exact quoted text>"}

THE SRT:
---
[INSERT SRT HERE]
---"""


# ──────────────────────────────────────────────────────────────────────────
# 4) PEXELS KEYWORDS  (title + script -> stock b-roll search terms)
# ──────────────────────────────────────────────────────────────────────────
PEXELS_KEYWORDS_PROMPT = """You pick stock-footage search terms for the background b-roll of a faceless, atmospheric YouTube video.

TITLE: [INSERT TITLE HERE]

Below is the script. Produce 14 short search queries (1–2 words each) for Pexels stock VIDEO and PHOTO b-roll that visually match this video's mood and subject. Favor cinematic, atmospheric, calm footage that loops well behind a slow spiritual narration: night sky, stars, ocean waves, candle flame, rain on window, misty forest, golden light, clouds, city at night, quiet room, hands writing, dawn, mountains, fog, embers, moonlight, etc. Mix the literal subject of the video with abstract/atmospheric terms.

Avoid anything with readable text, brands, or recognizable celebrities. Each query must be something Pexels actually has lots of footage for (keep them common and visual).

Return ONLY a JSON array of 14 strings. No markdown, no commentary.

SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


# ──────────────────────────────────────────────────────────────────────────
# 5) AI IMAGES FROM A PASTED SCRIPT  (no SRT/timestamps — standalone tool)
# ──────────────────────────────────────────────────────────────────────────
AI_IMAGES_FROM_SCRIPT_PROMPT = """You are the art director for a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

Below is the full spoken SCRIPT for one video. Pick EXACTLY 10 short, powerful lines from it, spread evenly across the whole script (some from the beginning, some from the middle, some from the end).

For each chosen line, produce a PHOTOREALISTIC image-generation prompt that shows that exact line as if it were physically written in the real world. Vary the surface across the 10 images:
- handwritten in ink on a sheet of white paper on a wooden desk under warm lamplight
- written with a marker on a whiteboard
- on a torn notebook page
- on a single sticky note
- on an old typewritten page
- chalk on a blackboard
Always super realistic: shallow depth of field, soft cinematic lighting, real texture, 16:9, NO people, NO faces, NO logos. Only the written words and the surface.

The written text must be SHORT — at most ~8 words, VERBATIM from the line. The image prompt MUST contain the exact words to render, in double quotes, and instruct that the text be spelled correctly and clearly legible.

Return ONLY a JSON array of exactly 10 objects, nothing else (no markdown, no commentary). Each object:
{"text": "<the short quote exactly as it should appear written>", "prompt": "<the full photorealistic image-generation prompt, including the exact quoted text>"}

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


# ──────────────────────────────────────────────────────────────────────────
# SCENE VISUALS  (script -> scene prompts, not quote-on-paper)
#    Two sub-styles bound by one palette+texture. Code appends the shared STYLE
#    SUFFIX to every scene, and the MOTION suffix for image-to-video slots.
# ──────────────────────────────────────────────────────────────────────────
# Paste at the END of every scene prompt for a consistent look.
# Appended (on top of the still prompt) when a scene is animated into a video clip.


# ──────────────────────────────────────────────────────────────────────────
# THUMBNAIL  (vision reads the channel's reference set + the title ->
#     text tiers + a gpt-image-2 image-to-image prompt). Images are attached to
#     the message so Claude literally SEES the reference thumbnails.
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# 7) THUMBNAIL  (title -> punchy 2–4 word headline; line2 goes in a red box)
# ──────────────────────────────────────────────────────────────────────────
THUMBNAIL_HEADLINE_PROMPT = """You write the headline text for a VIRAL YouTube thumbnail.

CHANNEL: [INSERT CHANNEL HERE]

TITLE: [INSERT TITLE HERE]

Produce a punchy thumbnail headline of 2–4 words TOTAL, split into two lines:
- "line1": plain white text (the setup) — usually 1–2 words
- "line2": the punch — 1–2 words that will sit inside a RED highlight box (the most provocative / pattern-interrupt part)

Rules:
- ALL CAPS, extremely punchy, emotional, curiosity or pattern-interrupt. It must make someone stop scrolling.
- Max ~4 words across both lines combined. Short and brutal beats clever.
- Prefer SHORT, high-impact words (ideally ≤8 letters each) so they fit a thumbnail cleanly — avoid long words like TRANSFORMED, MANIFESTATION, EVERYTHING. Punchy one-syllable words land best.
- No clickbait lies — it should relate to the title's promise.
- line2 is the word with the most emotional charge.

Return ONLY a JSON object, nothing else: {"line1": "...", "line2": "..."}
Examples: {"line1":"STOP","line2":"SLEEPING"} · {"line1":"YOU ARE","line2":"NOT READY"} · {"line1":"NOT","line2":"MEANT"} · {"line1":"IT'S","line2":"ALREADY DONE"}"""


# The exact thumbnail image prompt (image-to-image). [LINE1]/[LINE2] get filled in.
THUMBNAIL_IMAGE_PROMPT_TEMPLATE = """Using the attached image as the exact base, regenerate this YouTube thumbnail with ONLY the headline text changed. Keep everything else 100% identical:

Keep the same man (avatar) — do not alter his face, pose, lighting, hair, suit, or the sepia/golden tone on him
Keep the identical dark background with the soft yellow/gold glow behind the figure
Keep any channel wordmark in the corner exactly as it is (same font, position and style)
Keep the exact same text styling: bold white sans-serif font with a slight 3D/extruded shadow and rough textured fill

Change only the main headline to:

Line 1: [LINE1] — plain white text, same bold style as the original "NOT" (white, slight 3D/extruded shadow, rough textured fill)
Line 2: [LINE2] — inside a red highlight box, the same rounded red rectangle with soft shadow as the original "MEANT"

SIZING & FIT — this is important, make it look professional, not cramped:
- Scale the headline to fit cleanly inside the right portion of the frame with GENEROUS breathing room. Leave clear margins on all sides — the text and the red box must NEVER touch or run off the frame edges, and must not crowd the man on the left.
- Size the type DYNAMICALLY to the word length: short words become larger, long words become smaller, so both lines always stay balanced and never look squished or stretched. A long word like the one on line 2 should be sized DOWN so it fits comfortably with padding, not edge-to-edge.
- The red box should hug its word with comfortable, even padding inside it — not stretched across the whole width.
- Both lines form one tidy left-aligned block, vertically centered in the right half, with comfortable space above, below and to the right.
- Prioritize a clean, uncramped, high-end look over matching any exact original size.

Keep the same font family, weight, drop shadows and the overall placement zone (right side, upper-middle). Photorealistic, high contrast, 16:9."""


# ──────────────────────────────────────────────────────────────────────────
# 6) YOUTUBE METADATA  (script -> title / description / tags)
# ──────────────────────────────────────────────────────────────────────────
YOUTUBE_META_PROMPT = """You write YouTube metadata for a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

Below is the full spoken script for one video. Produce metadata that maximizes click-through and watch time while matching the video's content.

Return ONLY a JSON object (no markdown, no commentary) with exactly these keys:
{
  "title": "<a compelling YouTube title, MAX 100 characters, no clickbait lies, evoke curiosity/transformation>",
  "description": "<a 3–5 sentence description that hooks the viewer, summarizes the promise of the video, and ends by inviting them to comment the trigger word and grab the free guide via the first link in the description>",
  "tags": ["<12-18 relevant lowercase tags for THIS channel and THIS topic>"]
}

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---"""


MOTION_MINUTES_PROMPT = """You are art-directing the MOTION GRAPHICS for a spoken video. The transcript is split into consecutive parts (PART 1, PART 2, ...), each covering a fixed stretch of the video. For EACH part you design ONE animated scene that VISUALISES what the narrator actually says in that part — the way a good explainer video cuts to a chart, a diagram, or a set of cards exactly when the voiceover needs it.

This is NOT a mindmap and NOT a summary. It is ONE strong visual idea per part. Pick the template whose SHAPE matches the idea being spoken:

- "title"     — one punchy statement worth putting on screen alone. Fields: title, subtitle.
- "stat"      — a single number that lands (a count, a year, a percentage, a duration). Fields: title, value (SHORT, e.g. "7", "72%", "3x"), subtitle.
- "bars"      — comparing amounts across 3–5 named things. Fields: title, items[{label (1–2 words), value (a number)}], subtitle.
- "crowd"     — scale/proportion of people ("most people", "only a few"). Fields: title, count (20–80), highlight (how many stand out), subtitle.
- "flow"      — a sequence or cause→effect, 2–4 stages with arrows. Fields: title, items[{label (1–2 words), icon (any simple English noun — "door", "key", "storm"; a large icon library is matched automatically)}], subtitle.
- "cards"     — 2–4 parallel things to know about. Fields: title, items[{label (1–3 words), text (max ~8 words), icon}], subtitle.
- "checklist" — 3–5 concrete actions or signs to look for. Fields: title, items[{label (max ~8 words)}], subtitle.
- "compare"   — exactly 2 opposed sides (before/after, myth/truth). Fields: title, items[2 x {label (1–2 words), text (max ~8 words), icon}], subtitle.
- "steps"     — 3–4 ordered steps over time. Fields: title, items[{label (1–2 words), text (max ~6 words)}], subtitle.
- "warning"   — a caution, a mistake to avoid, a "do not do this". Fields: title, subtitle.
- "icongrid"  — 3–6 named things shown as glowing icons on a dark pane, each with its own motion. Fast and modern; use it for lists of forces, symptoms, places, or parts of a system. Fields: title, items[{label (1–3 words), icon (any simple English noun)}], subtitle.
- "quotemark" — ONE sentence held inside a giant quotation mark. Use for a line worth sitting with: a direct quotation, a thesis, the sentence the whole part turns on. Fields: title (the sentence), subtitle.
- "deckfan"   — 3–5 cards fanned out in 3D and dealt one at a time, each drifting in depth. The richest-looking layout here: save it for the part's strongest set of named things. Fields: title, items[{label (1–3 words), text (max ~6 words), icon}], subtitle.
- "timeline"  — a horizontal spine that draws itself with numbered nodes lighting in order. Use for anything SEQUENTIAL over time: stages, nights, a progression. Fields: title, items[3-5 x {label (max ~4 words)}], subtitle.
- "splitreveal" — two halves slam in from opposite edges with a bright seam down the middle. The loud version of a contrast: use when the two sides genuinely oppose each other. Fields: title, items[2 x {label (1–2 words), text (max ~6 words), icon}], subtitle.
- "strikelist" — 3–5 things that DON'T work, get crossed out, or must be left behind: each row lands, then a red line strikes it through. Use for "stop doing these", "none of these worked", "what he abandoned". Fields: title, items[{label (max ~6 words), icon (a simple noun in English, e.g. "clock", "money", "broken chain")}], subtitle.
- "reveal"    — 2–4 tarot-like cards that fly in and flip face-up. Best for archetypes, doorways, choices, "three signs", anything that should feel drawn rather than listed. Fields: title, items[{label (1–3 words), text (max ~7 words), icon}], subtitle.
- "image"     — ONE generated illustration framed on screen with a line of copy beside it. Use when the part is a story, a scene, or an image the viewer should sit with rather than a list. Fields: title, subtitle.

THE COLLAGE FAMILY — these paste real black-and-white cut-out stickers onto an aged paper page and annotate them by hand. PREFER THESE: they are the look of this channel. Each takes a "cutouts" array naming stickers from the library below.
- "collage"    — 2–3 stickers land on the page, each with a handwritten label underneath. The everyday workhorse: use it for "three things", "what happens when", any small set of named ideas. Fields: title, cutouts[2-3], items[{label (1–2 words), text (max ~5 words)}], subtitle.
- "scatter"    — one sentence circled in the middle with 6–8 stickers flying in around it. Use for a single strong statement you want to land. Fields: title (the sentence), cutouts[6-8], subtitle.
- "pillars"    — three identical stone columns standing side by side, each carrying one load-bearing point. Use ONLY for "everything rests on these three things". Fields: title, items[3 x {label (1–2 words), text (max ~5 words)}], subtitle. (cutouts are chosen automatically.)
- "photonote"  — a real photograph of the teacher taped to the page, annotated with a hand-drawn arrow. Use when the part is about the man himself, his books, or a direct quotation. Fields: title, subtitle. (the photo is chosen automatically.)

STICKER LIBRARY — for "cutouts", use ONLY these names, picking ones that MEAN what the part is about:
[INSERT CUTOUTS HERE]

ALLOWED ICON NAMES (use ONLY these, pick the one that fits the meaning):
arrow, bank, bell, bolt, book, box, brain, bulb, calendar, card, chart, chat, check, clock, coins, cross, doc, eye, fire, gear, globe, heart, house, key, lock, money, moon, people, person, phone, road, search, seed, shield, star, sun, target, trend, trophy, warning

LANGUAGE — NON-NEGOTIABLE:
Every word you put on screen (title, subtitle, item labels, item text) MUST be in
[INSERT LANGUAGE HERE]. The transcript below is in that language; the graphics have
to match it. Never write on-screen text in English when the narration is not English —
a Czech video with English cards looks broken. Keep the narrator's own wording where
you can, and respect that language's diacritics and grammar exactly.

RULES:
- Ground EVERY scene in what is really said in THAT part. Use the narrator's own nouns and numbers. No generic filler.
- PART 1 is the intro — make it a strong opening scene ("title", "stat" or "warning" work best there).
- VARY the templates. Never use the same template twice in a row, and do not lean on "title" for more than about a quarter of the scenes.
- LEAN ON THE COLLAGE FAMILY. Across the whole run, roughly half the scenes should be "collage", "scatter", "pillars" or "photonote" — that is the channel's signature. Use "photonote" 2–3 times per video, "pillars" at most once.
- Pick stickers for MEANING, not decoration: a locked door for resistance, a key for authority, an alarm clock for the hour you set aside, a brain for the mind, a heart for the affections.
- Keep text SHORT — this is on-screen graphics, not paragraphs. Titles max ~9 words, subtitles max ~12 words, item labels 1–3 words.
- "subtitle" is optional flavour; leave it "" when it would just repeat the title.
- Only invent a number for "stat"/"bars"/"crowd" if the part genuinely talks about quantity, comparison or proportion. Otherwise choose a different template.

Return ONLY a JSON array with EXACTLY one scene object per part, IN ORDER (same count as the number of PART chunks), nothing else:
[{"template":"...","title":"...","subtitle":"...","value":"...","count":40,"highlight":12,"cutouts":["brain","key"],"items":[...]}, ...]
Include only the fields the chosen template actually uses.

THE PARTS:
---
[INSERT CHUNKS HERE]
---"""


# ══════════════════════════════════════════════════════════════════════════
# Scene visuals
# ══════════════════════════════════════════════════════════════════════════


# Channel prompts live in styles/<name>.json, not here.


# Appended (on top of the still prompt) when a scene is animated to video.


# ══════════════════════════════════════════════════════════════════════════
# CARL JUNG  —  faceless depth-psychology channel, narrated in CZECH
# ══════════════════════════════════════════════════════════════════════════


# ── Thumbnail ────────────────────────────────────────────────────────────────
# The model is asked for copy and a scene only. Layout and palette come from the
# channel's style file, so a thumbnail cannot drift away from the channel look.


# ── Thumbnail (composition follows the channel's reference set) ─────────────
# The layout is NOT up to the model: three text tiers and a before/bridge/after
# band, exactly as in the channel's own reference set. Only the words and what
# is depicted change, so every thumbnail reads as the same channel.


# ── CHANNELS ───────────────────────────────────────────────────────────────
# Deliberately empty. A channel is a file in styles/, loaded by styles.py at
# startup — see styles/_TEMPLATE.json. Nothing about any particular channel is
# baked into the engine, so the same engine renders a psychology channel and a
# cooking channel without knowing the difference.
PROMPT_SETS: dict = {}

# Substituted into the prompts below from the running style's `label` and
# `description`, so the art direction, the stock searches and the metadata all
# know what channel they are serving.
CHANNEL_TOKEN = "[INSERT CHANNEL HERE]"


THUMBNAIL_PROMPT = """You are the thumbnail director for a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

THIS CHANNEL'S THUMBNAIL STYLE:
[INSERT THUMBNAIL STYLE HERE]

VIDEO TITLE: [INSERT TITLE HERE]

Design ONE thumbnail. Return STRICT JSON, no prose:

{
  "headline": "<2-4 words, ALL CAPS, the emotional hook — not the title>",
  "prompt": "<a complete image-generation prompt for the artwork BEHIND the text>"
}

HEADLINE RULES
- 2 to 4 words. Short words (<=8 letters) so they stay huge at phone size.
- It is the promise or the threat, never a summary of the title.
- No punctuation except a question mark.

ARTWORK RULES
- One clear subject. A thumbnail is read in under a second at 320px wide.
- STAGE THIS TITLE. The artwork shows what THIS video promises — its object, its
  situation, its threat, the moment it turns. Artwork that would sit just as
  well under any other video on the channel has failed, however good it looks.
- Leave the left OR the right third visually quiet — the headline goes there.
  Ignore that when the channel's thumbnail style gives the headline a band of
  its own: then fill the frame edge to edge and leave no dead third.
- Describe lighting, palette and mood explicitly; they carry the channel.
- No text, letters, numbers or watermarks anywhere in the artwork.
- Match the channel described above. If the channel has a stated palette, use it.
- Where the channel's thumbnail style says something specific — a composition, a
  colour, a recurring subject, a camera distance — follow it exactly. It was
  derived from real thumbnails that work in this niche, and it beats your own
  instincts about what looks good.
"""


# ── SCENE VISUALS  (script -> a pool of still prompts) ─────────────────────
# One prompt for every channel. The look is not described here — it comes from
# the channel's style file and is appended as a suffix, so two channels running
# the same engine never produce the same-looking stills.
SCENE_PROMPT = """You are the art director for a faceless YouTube video.

CHANNEL: [INSERT CHANNEL HERE]

Below is the full spoken SCRIPT. Design [INSERT COUNT HERE] distinct background
SCENES that carry this video visually.

RULES
- Each scene illustrates a MOMENT the narrator actually reaches. Read the script
  and follow its order; scene 1 belongs at the start, the last at the end.
- Atmospheric imagery, never text on screen. No words, letters or numbers.
- No faces looking at camera unless the channel is built on a person.
- One subject per scene, described concretely: what is in frame, the light, the
  distance. "A hand resting on a closed book, low window light" — not "wisdom".
- Vary the distance across the set: wide, middle, close. A pool of fifteen
  medium shots edits like a slideshow.

Return ONLY a JSON array of [INSERT COUNT HERE] objects:

  {"text": "<the line of narration this scene belongs to>",
   "prompt": "<a complete image-generation prompt>"}

SCRIPT:
[INSERT SCRIPT HERE]
"""

# Appended to every still prompt so a channel's images look like one channel.
# The style file's `look.still_suffix` replaces this when it sets one.
SCENE_STYLE_SUFFIX = (
    " Cinematic still, natural light, shallow depth of field, filmic grain, "
    "muted colour, no text, no watermark, no logo.")

# Appended when a still is animated into a short clip.
SCENE_VIDEO_MOTION = (
    " Very slow, subtle camera movement only — a gentle push in or a slight "
    "drift. Nothing in the frame should move quickly.")


# ════════════════════════════════════════════════════════════════════════════
# YOUTUBE PUBLISHING — description, chapters, tags, sources
# ════════════════════════════════════════════════════════════════════════════
# Written after the video exists, from the script that was actually spoken. The
# chapter prompt returns the FIRST SPOKEN LINE of each chapter rather than a
# timestamp: the line is then found in the subtitle file, which is the only
# place the real timing lives.

YOUTUBE_DESC_PROMPT = """You write the YouTube description for a faceless channel.

CHANNEL: [INSERT CHANNEL HERE]
VIDEO TITLE: [INSERT TITLE HERE]

Below is the full spoken script of the video. Write the description from what is
actually said — never promise anything the video does not deliver.

Return ONLY a JSON object (no markdown, no commentary):
{
  "description": "<4-6 sentences. Open with the tension the video resolves, say what the viewer will walk away with, and keep the channel's voice. Plain sentences, no emoji, no hashtags, no ALL CAPS, no 'in this video we will'.>",
  "tags": ["<14-18 lowercase search terms a viewer would actually type, most specific first, no hashes, no duplicates>"]
}

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---
"""

YOUTUBE_CHAPTERS_PROMPT = """You split a spoken script into YouTube chapters.

Return EXACTLY [INSERT COUNT HERE] chapters covering the whole script in order.

Each chapter needs:
- "title": 2-5 words, plain and concrete, describing what that stretch is about.
  No numbering, no colons, no clickbait. The first chapter is always "Intro".
- "first_line": the FIRST SENTENCE of that chapter, COPIED WORD FOR WORD from the
  script below. Copy it exactly, including punctuation — it is used to find where
  the chapter starts in the subtitles. Never write a line that is not in the script.

Spread the chapters evenly through the script: each one should cover a roughly
similar stretch of the text, and the last one must start in the final quarter.

Return ONLY a JSON array (no markdown, no commentary):
[{"title": "Intro", "first_line": "..."}, ...]

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---
"""

SOURCES_PROMPT = """You list the sources behind a video, for its description.

Below is the spoken script. Name [INSERT COUNT HERE] REAL, checkable sources that
a careful viewer could look up to verify or go deeper on what the video claims:
books (author, title, year), named studies or papers, or pages from institutions
that publish on this subject.

Rules that matter more than completeness:
- Only sources you are sure exist. A made-up citation is worse than a short list.
- Give a "url" ONLY when you are confident of the exact address (a publisher page,
  a DOI, an institution's own page, a Wikipedia article). Otherwise leave "url" as
  an empty string and let the name carry it. Never invent a URL that looks right.
- No blogs, no listicles, no AI-generated pages, no affiliate links.
- Every label in English, formatted for a description line, for example:
  "Carl Jung — Man and His Symbols (1964)".

Return ONLY a JSON array (no markdown, no commentary):
[{"label": "...", "url": "..."}, ...]

THE SCRIPT:
---
[INSERT SCRIPT HERE]
---
"""


# ── story channels: an uploaded character, put into words ───────────────────
# The storyboard repeats the cast text in every image prompt, so a new face needs
# a description that matches it. Claude looks at the picture and writes this.
CHARACTER_DESC_PROMPT = """This picture is the main character of a story channel. Every frame of
the channel's videos is drawn from it, and the words you write are repeated in every image prompt, so
they must pin this exact person down.

Write it in exactly this shape — plain text, no markdown, no preamble, nothing after it:

THE MAIN CHARACTER is the <man / woman / boy / girl / person> from the reference image, and <he / she / they> <is / are> in EVERY frame:
<two or three lines: apparent age; hair colour, length and style; the face as it is drawn; skin; build;
the exact clothes and shoes, with colours; one small detail that makes them recognisable, such as a
ring, glasses or a watch>. Always this exact <man / woman / person>, drawn exactly this way.
In a close-up <he / she / they> <is / are> still there — <his / her / their> hand and sleeve holding the phone, <his / her / their> shoulder in the foreground.
Anyone else who returns in the story keeps the same face, hair and clothes every time.

Describe only what is visible in the picture. Never name or guess a real person."""
