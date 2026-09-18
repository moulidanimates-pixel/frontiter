# Maps — built into Frontier

Map animations that appear on their own. Whenever the voice names a place that matters
to the story, the video cuts to a map of it: the country, a push in, the place lights up
and lifts off the map while its name writes itself on beside it.

## What it adds

- **Automatic map scenes.** Claude reads the finished narration, picks the moments where a
  map makes the story clearer — where something happened, where someone went, several
  places named together — and each map lands on the exact word that names the place.
- **Four kinds of map**
  - **Region** — a country, state, province, sea, desert or mountain range lights up and
    lifts out of the map, leaving its hole behind.
  - **Pin** — a city or an exact spot: the pin drops, rings ripple out, a line runs to its name.
  - **Route** — a journey: the line draws from place to place with a ship, a plane or an army
    on it, and arrives on the word that names the destination. Sea voyages go round the coast.
  - **Several places** — each one lights up as it is named. Places too far apart for one view
    (Brazil and Japan) become a flight across the globe.
- **Three looks**: `midnight` (dark, glowing teal and green), `paper` (an old atlas: stained
  paper, inked borders, red push pins and string) and `clean` (white land, one strong blue).
  A channel gets the look closest to its graphics unless it names one.
- **Real borders.** Every country, every state and province in the world, 7,300 cities,
  seas, oceans, deserts and mountain ranges, from Natural Earth. Nothing is drawn by an image
  model, so borders are right and the camera can fly from the whole globe down to a single
  state without the picture going soft.
- **Any language.** Place names on screen follow the video's language (Czech videos say
  *Středozemní moře*, not *Mediterranean Sea*).

## Using it

Nothing to set up. Restart the app: **Options → Map animations** is on. Untick it for a
video that should have none.

A map scene takes the place of a regular motion graphic that would have played at the same
moment, so a video does not end up with more graphics than before — just the right ones.

To see the maps without making a video:

    python maps.py demo                              # six sample scenes into preview/
    python maps.py show "Texas"                      # one place
    python maps.py show "London > New York" --icon plane
    python maps.py show "Japan" --skin paper
    python maps.py check                             # every map type in every look, errors reported

## Per channel

In a channel's style file, under `look`:

    "maps": {
      "skin": "paper",        // midnight | paper | clean
      "every_s": 35,          // at most one map per this many seconds of video, on average
      "gap_s": 15,            // never two maps closer than this
      "colors": {"hi": "#E8A33D", "hiLight": "#F2BE62", "hiDark": "#B97F22"}
    }

`"maps": false` turns maps off for that channel altogether. Every key is optional.

## Cost

The maps themselves cost nothing: no image model, no API. Each video makes one extra Claude
call to find the places (through Claude Code, like the script), and each map takes about
25 seconds to render.

## Limits worth knowing

- Borders are today's, in Natural Earth's default view. A historical empire is drawn as the
  modern countries it covered, merged into one shape.
- A village too small for the atlas becomes a pin at the coordinates Claude gives.
- A map never lands inside the video's opening caption.

## For Claude Code

- `maps.py` — everything: place lookup (`resolve`), the moment finder (`MOMENTS_PROMPT`,
  `plan_moments`), timing to the word (`build_scenes`), the scene builders (`_region`, `_pin`,
  `_route`, `_multi`), the skins (`SKINS`), the Chromium renderer (`render`) and the hook
  the engine calls (`add_to_timeline`). Constants at the top of the TIMELINE section set
  durations and spacing.
- `assets/maps/map.js` — the page every map is drawn on. Same rule as every Frontier
  animation: `window.renderFrame(t)` draws frame t from t alone. Shots live in `SHOTS`.
- `assets/maps/*.json` — the atlas (TopoJSON at three levels of detail, plus `gazetteer.json`
  with every name). Built from Natural Earth; there is no need to rebuild it.
- The engine side is small and dormant: `_with_scene_dlcs()` in make_video.py runs `maps.py`
  (right after the opening caption is placed), and the `maps` option in app.py / ui.html.
- Per video: `output/<video>/maps/moments.json` is what Claude picked (delete it to ask
  again), `map_NN.mp4` the rendered scenes.
- To change a look: edit its entry in `SKINS`, then `python maps.py check` and open
  `preview/_maps_check.png` with your Read tool before saying it is done.
