# 2D Stories — built into Frontier

A story channel for Frontier: a POV money story ("one decision, ten years") told in
flat 2D pictures, with the same main character in every one of them.

## What it adds

- **The channel** — `styles/stories.json`: the POV script format (second person,
  "Level one…", a setback, the closing scene back where it started), the voice,
  big upper-case captions, the in-and-out zoom and the storyboard prompt.
- **The main character** — `assets/characters/stories.png`, in every picture.
- **The look it aims for** — `assets/styles/stories.jpg`, shown on the channel card.

## How it works

- **Pictures only.** A new AI picture about every 5.5 seconds, each drawn for the
  words it sits on and never shown twice. No stock footage, no motion graphics — the
  mix sliders are hidden for this channel.
- **Specific, like a comic.** When the voice says a sum, the phone shows that figure;
  a car is that model, seen from behind, with the number that was said; a place comes
  back the way it was set up. The pictures are timed off the subtitles, so each one
  lands on its own words.
- **The character.** The picture goes to the image model as a reference for every
  still. The image model only takes a reference as a *link*, so the first time it is
  used Frontier puts the picture on catbox.moe, a free public image host (uguu.se when
  catbox is down), and remembers the link.
- **Your own character.** Options → *Your character* → *Upload your own* (PNG, JPG or
  WebP, ideally a full-body drawing on a plain background). Claude writes the words that
  describe it; edit them if they are off. Only upload a picture you are fine having on a
  public link.

## Cost

About 11 pictures a minute: a 10-minute story is roughly 110 pictures, about
40 Algrow credits of images (gpt-image-2 at 0.35 a picture), plus the voice. Long
videos work the same way — the storyboard is drawn in parts of about 36 pictures.

## For Claude Code

Everything the channel does is in `styles/stories.json`; the engine features it uses
(`placement: "story"`, `character_image`, `photo_every_s`, `graphics: false`,
`encode`) are described in CLAUDE.md under "Writing a style file" and "The main
character's picture". To make a second story channel, copy the file under a new name
and change the prompts, the character and the colours.
