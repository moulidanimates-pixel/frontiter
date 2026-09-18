# Real Headlines — built into Frontier

The article behind the claim, on screen. When the narration states something a real
news story or an encyclopedia backs up, the video cuts to that page: a browser window rises
in, the camera pushes in on the headline, a yellow marker sweeps across it line by line, the
rest of the page sinks back and the source is credited in the corner.

## What it adds

- **Automatic research.** Claude reads the finished narration and picks the checkable
  claims — an announcement, a decision, a study, a record, a dated historical fact. A free
  news search (Bing News) and Wikipedia bring back candidate pages, and Claude picks the one
  that states that exact fact, not just the same topic.
- **Real pages, checked.** Each page is opened in Chromium. Cookie banners, pop-ups, sticky
  bars and ad slots are hidden — never accepted. A page behind a captcha or a paywall, or
  whose headline turns out to be something else (a live blog that moved on), is skipped and
  the next candidate is tried.
- **The right line highlighted.** For news, the headline. For a reference page, whose title is
  only a name, the sentence in the article that states the claim ("The fleet arrived … on
  20 May 1498.").
- **Timed to the word.** The marker starts as the voice says it. A regular motion graphic
  in the way gives way; a map scene from the maps module does not — the page waits a few seconds
  or is left out.
- **The look.** A tilted browser window with the page's real address, depth of field around
  the highlighted lines, a red–cyan flicker as it lands, a sheen, film grain and a vignette.
  White headlines on dark pages (AP News) get a lit bar and an underline instead.

## Using it

Nothing to set up and no key. Restart the app: **Options → Real headlines** is on.

To try it without a video:

    python headlines.py show https://apnews.com/article/...        # one page, headline marked
    python headlines.py show https://en.wikipedia.org/wiki/Apollo_11 --claim "Apollo 11 landed on the Moon in July 1969"
    python headlines.py find "European Central Bank interest rates" # what the news search finds
    python headlines.py check                                       # capture + page, errors reported

## Per channel

In a channel's style file, under `look`:

    "headlines": {
      "every_s": 60,                       // at most one page per this many seconds, on average
      "gap_s": 25,                         // never two pages closer than this
      "marker": "rgba(242,208,39,0.86)"    // the highlighter colour
    }

`"headlines": false` turns it off for that channel. A story channel with no real-world claims
simply gets none — Claude returns an empty list.

## Cost

No image model and no search API: the searches are free. Two extra Claude calls per video
(find the claims, pick the pages), a few seconds per page to open and photograph it, and
about 30 seconds per scene to render.

## Worth knowing

- The page is the real page: the text is not edited, the marker is laid over it. The
  address bar and the credit name the source.
- A story that opens with a video would photograph as a black box: a player never draws in
  the capture browser. The video's own poster is put in its place, or, if the player has
  none, the picture the article shares itself with. A player that already shows a picture
  is left alone.
- Showing a headline with its source is how news and commentary channels quote the press,
  but you are responsible for what your channel publishes. A headline scene crops to the
  text; the article's photo is the part most likely to belong to a photo agency, and it sits
  at the edge of the frame, out of focus.
- Big paywalled outlets (NYT, WSJ, FT, Bloomberg …) are skipped outright; a search that only
  finds those gives no page for that moment.

## For Claude Code

- `headlines.py` — search (`bing_news`, `wikipedia`), the two prompts (`MOMENTS_PROMPT`,
  `PICK_PROMPT`), `capture()` (clearing the page: `HIDE_CSS`, `JS_CLEAN`; finding the headline:
  `JS_FIND`; finding a sentence: `JS_SENTENCE`; a poster in an empty video player: `JS_MEDIA`),
  `compose_window()`, `render()` and the hook the engine calls, `add_to_timeline()`.
- `assets/headlines/page.js` — the animation. Same rule as every Frontier scene: frame t from t
  alone. The window is one picture, so it can move and scale without the type crawling.
- A site whose banner still shows: add its selector to `HIDE_CSS`. A site that should never be
  used: add it to `SKIP_DOMAINS`.
- Per video: `output/<video>/headlines/moments.json` (the claims), `picks.json` (the pages, best
  first), `cap_NN.jpg/.json` (the photograph and where its lines are), `headline_NN.mp4`.
  Delete a file to redo that step.
