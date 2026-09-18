# Photo FX — built into Frontier

Three ways a picture comes alive, the way the big documentary channels cut:

- **Photo spotlight.** When the voice names a real person, place or moment, a real photograph
  of it comes on screen. The camera pushes in on the subject while everything else in the
  photo darkens, loses its colour and blurs. The subject is cut out, so it stays sharp and
  grows a little against the fading ground.
- **Real objects.** When the voice names a real thing — a can of Dr Pepper, a Rolex, a gold
  bar — the thing is cut out of a real photo and animated over a blurred ground in its own
  colours, with a soft drop shadow: it rises in turning, overshoots and settles. A crown drops
  onto the new number one and wobbles into place. Two things slide in from the sides to be
  compared. A row of them pops in one by one.
- **Depth pop.** On AI pictures of people and vehicles, the person (or the car) is lifted off the
  picture with a thin light keyline and slowly grows while the background pulls back and softens —
  on every third such picture, so it stays a treat and not a gimmick. Claude looks at the pictures
  first: a close-up of hands, a phone, a steering wheel or a plate of food keeps its plain zoom.
  It works on 2D drawings and painted scenes alike.

## What it adds

- **Moments picked for you.** Claude reads the finished narration and picks the moments where
  a real photo or a real object makes the point land: a named public figure, a documented
  event, a branded product, an object that carries the idea. Never private people, never an
  imagined scene, never an abstract idea.
- **Real pictures, chosen by eye.** An image search (free, through Algrow) and Wikimedia
  Commons bring back candidates. Stock-photo sites are skipped — they watermark everything.
  Claude looks at the photos with their captions and picks the one that works on screen: the
  right thing, whole, sharp, no text over it. Who is in a photo comes from its caption and the
  search, not from anyone's face.
- **Clean cutouts.** The background remover cuts the subject out. For an object, Claude then
  looks at the cutouts themselves and rejects a pack of three cans or a shelf edge left on the
  object; an object cut off by its photo's edge or too small to fill the frame is never used.
  A can shot at a slight angle is stood up straight.
- **Timed to the word.** A spotlight darkens as the name is said; an object has landed when
  it is named; the crown lands on "the new king". A regular motion graphic in the way gives
  way; a map or a headline from the other modules does not.
- **Captions stay on.** These are pictures, not graphics, so the burnt-in captions keep
  running over them (on a channel with captions in the middle of the frame, an object scene
  mutes them — that is exactly where the object lands).

## Using it

Put `WAVESPEED_API_KEY` in `.env` — the background remover runs on WaveSpeed (see Cost).
Restart the app: **Options → Photo spotlight**, **Real objects** and **Depth pop** are on.
Photo spotlight and Real objects need motion graphics in the mix, like maps and headlines;
Depth pop works on every channel that makes AI pictures, the pictures-only story channels too.

To try it without a video:

    python photofx.py depth output/<video>/images/scene_03.jpg     # one AI still, 5 seconds
    python photofx.py spotlight photo.jpg --subject "the man in the red shirt"
    python photofx.py object "Dr Pepper can" --accent "silver crown"
    python photofx.py object "Pepsi can" --also "Coca-Cola can"   # two things, compared
    python photofx.py find "Rolex Submariner"                      # what the image search finds
    python photofx.py check                                        # offline test, no keys

Each writes an mp4 and a strip of frames (`.png`) into `preview/`.

## Per channel

In a channel's style file, under `look`:

    "photofx": {
      "spotlight": true,        // real photos of named people, places and moments
      "objects": true,          // real things cut out and animated
      "depth": true,            // the depth pop on AI pictures
      "depth_every": 3,         // every Nth picture of a person or a vehicle gets it (1 = all of them)
      "depth_subjects": "people", // "people" = people and vehicles only; "any" = any clear subject
      "keyline": "#F6F1E8",     // the depth pop's outline colour ("" for none)
      "every_s": 45,            // at most one photo or object scene per this many seconds
      "gap_s": 14               // never two closer than this
    }

`"photofx": false` turns all three off for that channel.

## Cost

- The background remover is $0.004 a picture on WaveSpeed. An object tries its best three
  photos at once and keeps the cleanest cutout; a cutout is kept under the picture's hash, so
  nothing is paid twice. A ten-minute video with a dozen photo scenes and the depth pop on
  every third still stays well under half a dollar.
- The image search is free. Claude: one call to find the moments, one to pick each photo, one
  more to check an object's cutout.
- Rendering: about 30 seconds per photo or object scene (Chromium), a few seconds per depth
  shot (ffmpeg).

Without a WaveSpeed key the scenes are skipped and every still keeps its plain zoom — the
video still renders.

## Worth knowing

- A still without one clear subject — a landscape, a crowd, a room — keeps its plain zoom.
  So does a subject that fills the frame top to bottom: there is no room for it to grow.
- The spotlight needs a photo at least 900 px wide; a smaller one is skipped. A photo whose
  cutout fails still works: the subject sits in a soft oval of light instead.
- Photos come from the open web. Commentary, news and documentary channels show them this
  way, but you are responsible for what your channel publishes. `photofx/mNN/pick.json` and
  `thing.json` record where every picture came from.

## For Claude Code

- `photofx.py` — sections in order: pictures (numpy + Pillow helpers, no OpenCV), `cutout()`
  (WaveSpeed, cached by hash), DEPTH (`depth_parts`, `part_motion`, `build_depth`,
  `render_depth`, and the two engine hooks `depth_slots` / `depth_segment`), SCENES
  (`spotlight_layers`, `spotlight_scene`, `thing_from_cut`, `upright`, `brand_ground`,
  `objects_scene`, `render`), SOURCING (`image_search`, `fetch_candidates`, the pick prompts,
  `source_spotlight`, `source_thing`) and TIMELINE (`MOMENTS_PROMPT`, `add_to_timeline`).
- `assets/photofx/page.js` — both scenes. Same rule as every Frontier scene: frame t from t
  alone. The spotlight's plates are prepared in Python (sharp, dim, soft), so the browser only
  moves and fades pictures; the object moves are damped springs.
- Depth is ffmpeg: a plate with the subject painted out, a softer copy fading in, one layer per
  part zoomed by `perspective` (fractions of a pixel, no zoompan shake), eased in and out and never
  quicker than over four seconds (`DEPTH_Z0`, `DEPTH_GROW`, `DEPTH_FULL_S`). Which stills get it:
  `depth_subjects` shows Claude the stills ten at a time and keeps the ones of people and vehicles
  (answers cached in `depth/subjects.json`). Each part grows about a
  point inside itself, low as it can, and never so far that a head leaves the frame
  (`part_motion`). Its segments carry the same colour tags as the engine's plain photo segments.
- The engine: `SCENE_DLCS` runs `add_to_timeline`; `_render_astro_segments` asks `_depth_dlc`
  for the slots and `_depth_segment` renders one, falling back to the plain zoom on any error;
  `_mutes_captions` keeps the captions on `spot_` / `objects_` scenes.
- Per video, in `output/<video>/photofx/`: `moments.json` (Claude's picks), `mNN/` (candidates,
  `pick.json` or `tNN/thing.json`, the layers), `cutouts/`, `spot_NN.mp4`, `objects_NN.mp4`, and
  `depth/<hash>.json` with a folder of layers per still. Delete a file to redo that step.
