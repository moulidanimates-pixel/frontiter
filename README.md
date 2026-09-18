# FRONTIER 4

A faceless-video studio that runs on your own computer. You type a title — or paste your own
script — and Frontier writes the narration, records the voice, finds real footage, draws the
graphics, animates maps and headlines, adds sound design, cuts it all together and hands you a
finished 1080p video with a thumbnail and a YouTube description.

Nothing is uploaded anywhere except the API calls it makes for you.

*Made by [Primal Robin](https://x.com/primalrobin).*

---

## Start here — the two-minute version

1. **Open this folder in Claude Code and say hello.** Claude reads `CLAUDE.md`, explains everything
   below in plain words, installs what's missing, and asks you for your API keys one at a time.
2. **Describe your channel** — the niche, the tone, a few videos you love, colours you like.
   Claude builds you your own style, starting from the Documentary style, and shows you samples
   before you spend anything.
3. **Run `python app.py`**, pick your style, type a title, press **Create video**.

You don't need to read the rest of this file. It's here so you know what's in the box.

---

## The three styles that come with it

| Style | What it looks like | Roughly costs |
|---|---|---|
| **Documentary** *(the default)* | A streaming-documentary look. Real YouTube footage of the actual events, map animations, real newspaper articles, real photos and objects, Vox-style paper-collage scenes, quiet premium graphics (serif type, one gold accent, numbers counting up, charts on paper, timelines), sound design under every cut. No stock footage, no AI pictures. | **about $0.50 per minute** of video |
| **Carl Jung — cheap mode** | The original Frontier look: dark psychology narration over stock footage and AI images, animated graphics, the depth-pop effect on pictures. Built to be cheap. | **about $1–2 for a 30-minute video** |
| **2D Stories** | A POV money story told in flat 2D pictures with one consistent main character in every frame. | about $0.15 per minute |

A style is one file in `styles/`. Your own channels live there too — Claude writes them for you.

**See before you spend.** A channel card in the app plays a sample of what that channel makes — press
**Watch sample** to see it big, with sound. Give your own channel one with `look.sample` in its style
file: a video under `assets/` (the samples that come with Frontier are in `assets/brand/`).

**A channel adapts to the title.** Documentary has variants in `styles/variants/` that it switches to by itself when a title calls for them: a video-game story gets the gaming explainer (a lively narrator, quick cuts, bold colour, game trailers), a grooming or style how-to gets the how-to look (step cards, tip cards, products). The log says which look was chosen. Add your own variant by copying one and changing `match` — the sentence that describes the titles it is for.

Every channel writes in its own voice, and Frontier adds the same retention craft on top of its prompts: a hook that confirms the video in the first seconds, opens one specific question and stacks two or three hard specifics; a chain of payoffs that always opens the next question; the title's big payoff foreshadowed and then delivered; no invented facts. A story channel (documentary, history, mystery) gets the story framework, a teaching channel (how-to, psychology, money) the teaching one — set `"script_craft": "story"`, `"teaching"` or `"off"` in the style file. A script you paste yourself is never rewritten.

**How a Documentary is edited.** One director reads your narration and decides, sentence by sentence,
what the viewer sees on top of the real footage. The first minute is the hook and gets the most: a
date graphic the moment the date is said, a map of the first place, a real photo of the first person,
a paper-collage scene, text labels for every name and number. After that the video breathes — real
footage carries it, with about one collage scene and one graphic a minute, real photos often, a map
when a new place matters and text labels on the footage. Collage scenes take their colours from the
story; newspaper pages move three different ways, so no two look the same.

---

## Options — everything a video can be built from

When you pick a style, **Options** shows every feature it offers, all ticked. Untick anything you
don't want in *this* video. A feature whose API key is missing is shown **in red** with the exact
key to add — so you see it before you press Create, not halfway through a render.

Every switch shows roughly what it costs per minute of video with the services in your `.env`
(hover it for the detail), and next to **Length** you see the estimate for this video — about how
long it takes to render and about what it costs in API calls. It follows the length, a pasted
script and every switch you tick. It's an estimate: your Algrow, WaveSpeed and kie dashboards have
the real numbers.

| Switch | What it does | Needs |
|---|---|---|
| **YouTube footage** | Real footage of the real events, people and places. Claude writes a YouTube search for every ~30 seconds of narration, a video model watches the results and logs every shot, Claude picks the shots that show what's being said, and only those seconds are downloaded — trimmed to the shot, cropped clear of logos and black bars. Official uploads (studios, broadcasters, news agencies) come first; for a video game it uses the studio's own trailers and never fan-made "concept" ones. Short videos are watched whole; a long one only where it talks about your story (read from its captions), so a 25-minute essay costs a few minutes of watching — give Frontier a Gemini, WaveSpeed or kie.ai key next to Algrow for that. Blurry uploads are left out. The Documentary style always prefers it. | yt-dlp + **one** of: Algrow · Gemini API · WaveSpeed · kie.ai |
| **Stock footage** | Pexels b-roll searched from the narration. | Pexels (free) |
| **AI images** | Pictures drawn for the exact words they sit on. | Algrow · WaveSpeed · kie.ai |
| &nbsp;&nbsp;↳ **Depth pop** | A person or a car in a picture slowly lifts off it while the background pulls back. | WaveSpeed |
| **Motion graphics** | Animated scenes for the numbers, dates, names and turning points — plus text labels on the footage (a name with who they are, a place, a date, a figure). In the Documentary style these are the premium documentary graphics; how-to videos also get step cards and "tell your barber"-style tip cards with the key words highlighted; people following or unfollowing each other get an animated social-media unfollow. | nothing extra |
| **Vox style scenes** | A few moments told as hand-cut paper collage — real people as halftone cutouts with their real faces, pins, red string, typewriter labels. With an Algrow key the pictures are drawn on Algrow (cheaper); WaveSpeed hosts the reference photos. | WaveSpeed |
| **Map animations** | Whenever the narrator names a place that matters: real borders, the place lit up, routes and pins, timed to the word. | nothing extra |
| **Photo spotlight** | A real photo of who or what is being named; the rest darkens as the camera pushes in. | WaveSpeed |
| **Real objects** | The products and things the narrator names, cut out of real photos and flying in. | WaveSpeed |
| **Newspaper animations** | The real article behind a claim, on screen, the sentence highlighted. | nothing extra |
| **Sound design** | Whooshes into every scene, low hits where a figure lands, risers into questions, taps along timelines — all synthesised, nothing to license — plus your own music bed, dipping whenever the narrator speaks. | nothing extra |

Also in Options:

- **Use your own script** *(optional)* — paste a finished script and Frontier narrates exactly that.
  The length buttons are ignored; your script decides how long the video is.
- **Extra instructions** *(optional)* — anything for this one video: "focus on the engineers, not
  the executives", "no graphics in the first minute", "UK spelling". They reach the script writer,
  the footage researcher, the graphics designer and the art director.
- **Output** — burnt-in subtitles, thumbnails, a YouTube description with chapters, sources.

**Your title matters most.** The script writer is trained per style: it builds a cold open, real
facts, tension and a payoff from the title alone. A specific title ("New Coke: The 79-Day Disaster
That Saved Coca-Cola") makes a far better video than a vague one ("Coca-Cola history").

---

## What you need

You do **not** need every service. Pick one of these setups:

| Setup | Voice | Pictures | YouTube footage | Real photos, objects, Vox, depth |
|---|---|---|---|---|
| **Algrow** + WaveSpeed *(recommended)* | Algrow (ElevenLabs voices) | Algrow (gpt-image-2) | Algrow | WaveSpeed |
| **WaveSpeed only** | WaveSpeed (ElevenLabs v3) | WaveSpeed (Seedream) | WaveSpeed (Gemini) | WaveSpeed |
| **kie.ai** + a voice service | Algrow, WaveSpeed or ai33 | kie.ai | kie.ai (Gemini) | WaveSpeed |
| **Gemini API** for footage | any of the above | any of the above | Google Gemini API (free tier works) | WaveSpeed |

Links for all of them are on the Whop page. Plus, always:

| | Why | Where |
|---|---|---|
| **Claude Code** | Writes every script, prompt and scene. No extra key — or set `ANTHROPIC_API_KEY`. | claude.com/claude-code |
| **Pexels** *(free)* | Stock footage, if you use it. | pexels.com/api |
| **ffmpeg** | Renders the video. | Mac: `brew install ffmpeg` · Windows: `winget install Gyan.FFmpeg` |
| **Python 3.10+** | Runs everything (3.9 works too, except that yt-dlp then has to be installed on its own). | python.org |
| **yt-dlp** + **Node.js or Deno** | Downloads the YouTube shots. YouTube needs a JavaScript runtime to hand over the good formats. | yt-dlp comes with the requirements (or `brew install yt-dlp` / `winget install yt-dlp.yt-dlp`); Node from nodejs.org |

If a service is missing, everything that doesn't need it still works — the red text in Options
tells you exactly what you'd unlock by adding it.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Open `.env` and paste in the keys you have. **On Windows** it's the same in PowerShell (`copy`
instead of `cp`; use `py` if `python` isn't found). After installing ffmpeg or Node, open a **new**
terminal so it's found.

## Run

```bash
python app.py
```

Your browser opens. Pick a style, type a title, choose a length, look through Options, press
**Create video**. While it renders, the page shows every stage as it happens — script, voice,
footage, edit, scenes, graphics, render, sound, final — and **Details** opens the full log.
A 10-minute Documentary takes roughly 40–60 minutes on a laptop.

---

## Make it yours

You own the code. Everything is changeable by asking Claude Code in plain words:

- *"Make a new style for my true-crime channel, based on Documentary, darker, red accent."*
- *"Use a different serif for the graphics and make the numbers bigger."*
- *"Add a graphic that shows two people side by side with their dates."*
- *"Captions off by default, and the music quieter."*

Claude changes the files, renders a preview and **looks at it** before telling you it's done.
Nothing is locked: colours and fonts live in your style file, the graphics in `docgfx.py` and
`motion.py`, the pipeline in `make_video.py`. Keep a copy of anything you're proud of — or ask
Claude to.

See things before you spend anything:

```bash
python docgfx.py preview          # every documentary graphic -> preview/docgfx/
python sfx.py preview             # every sound effect -> preview/sfx/
python youtube.py search "Apollo 11 launch footage"
python maps.py demo               # a map animation
python preview.py --all           # every classic motion graphic in your style's look
python check_templates.py         # did anything break?
python check_video.py <slug>      # a finished video: pacing, repetition, language, audio
```

### Music

Frontier never ships music. Drop tracks you have the rights to into `assets/music/<style>/`
(for example `assets/music/documentary/`) or straight into `assets/music/`. With **Sound design**
on, one of them plays under the whole video, fading in and out and dipping under the voice. Every
track is levelled against the narration automatically (however loud it was mastered), and the
finished soundtrack is mastered to YouTube's loudness (-14 LUFS). To pick one track by name, set
`look.sound.track` in the style file (or `FRONTIER_MUSIC=my track.mp3` in `.env`). Levels live in
`look.sound`: `music_rel_db` (the bed against the voice, default -6), `duck_db`, `sfx_db`.

### Footage you didn't film

YouTube footage belongs to whoever filmed it. Documentary channels use short excerpts for
commentary and transformation, but fair use depends on your country and how you use it, and a
rights holder can still claim a video. You decide what you publish — check anything that matters.

---

## Cost

| | Per minute of finished video |
|---|---|
| Documentary, all options | about $0.50 (voice, a few collage pictures and cutouts; footage search is nearly free) |
| Carl Jung — cheap mode | about $0.05 |
| 2D Stories | about $0.15 |

Claude Code usage comes from your own Claude plan.

**Running low on WaveSpeed?** Frontier keeps a reserve (`WAVESPEED_RESERVE_USD`, default $0.50):
below it, collage and object pictures are drawn on Algrow instead, and nothing stops halfway.

---

## Files

```
app.py            the local web UI
make_video.py     the pipeline
director.py       the editor: which scene, label or graphic shows each sentence
styles/           one file per style — yours live here too
features.py       the Options switches and the keys each needs
youtube.py        YouTube footage: search, shot logging, cutting
docgfx.py         the Documentary graphics
sfx.py            sound design and the music bed
maps.py           map animations          (README-MAPS.md)
headlines.py      newspaper animations    (README-HEADLINES.md)
photofx.py        photo spotlight, real objects, depth pop (README-PHOTO-FX.md)
vox.py            Vox style collage scenes (README-VOX-STYLE.md)
motion.py         the classic motion graphics
assets/           fonts, maps, textures, reference pictures
output/           your finished videos
```

Every finished video is also in **Your videos** at the bottom of the app — **Open in Finder** (or
**Open in File Explorer** on Windows) opens its folder with the video selected, named after its title.

## Updates

Updates come from the Whop as a small pack. Unzip it anywhere and tell Claude Code
*"install the update in ~/Downloads/Frontier-UPDATE"*, or run it yourself:

    python Frontier-UPDATE/update.py Frontier-UPDATE --into ~/Desktop/Frontier

Files you never touched are replaced; files you changed are left alone, with the new version beside
them as `.new` for Claude to merge. Everything replaced is backed up first. Your `.env`, your styles
and your `output/` are never touched.

## Licence

You bought this. Use it, change it, run as many channels on it as you like. Don't resell the tool
itself.
