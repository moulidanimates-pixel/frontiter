#!/usr/bin/env python3
"""headlines.py — the HEADLINES DLC: real articles on screen, the headline highlighted.

When the narration states something a news story or a reference page backs up, the video
cuts to that page: a browser window rises in, the camera pushes in on the headline, a
yellow marker sweeps across it and the source is credited in the corner.

    python headlines.py show https://apnews.com/article/...          # one page, the headline marked
    python headlines.py show https://en.wikipedia.org/wiki/Apollo_11 --claim "landed on the Moon in July 1969"
    python headlines.py find "European Central Bank interest rates"  # what the news search finds
    python headlines.py check                                        # capture + page, errors reported

How it works
------------
* Search is free and needs no key: Bing News for news, Wikipedia for everything else.
  Claude picks the page that actually says what the narration says.
* The page is opened in the same Chromium that renders the graphics. Cookie banners,
  pop-ups, sticky bars and ad slots are hidden (never accepted), the headline is found
  and measured line by line, and only the part of the page around it is photographed.
* A page that shows a captcha, a paywall or a different headline is skipped — the next
  candidate is tried. The headline is never edited: the marker is laid over the real text.
"""

import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets" / "headlines"
W, H, FPS = 1920, 1080, 30

# ════════════════════════════════════════════════════════════════════════════
# CAPTURE — open the page, clear it, find the headline, photograph around it
# ════════════════════════════════════════════════════════════════════════════
VIEW_W, VIEW_H, SCALE = 1440, 1000, 2
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")

# Requests to these never leave the machine: the page loads faster and the photograph has no ads.
AD_HOSTS = ("doubleclick.net", "googlesyndication.com", "googletagservices.com", "adservice.google",
            "amazon-adsystem.com", "taboola.com", "outbrain.com", "criteo.", "pubmatic.com", "rubiconproject.com",
            "openx.net", "adnxs.com", "moatads.com", "scorecardresearch.com", "quantserve.com", "teads.tv",
            "sharethrough.com", "revcontent.com", "mgid.com", "zergnet.com", "yieldmo.com", "smartadserver.com",
            "adform.net", "casalemedia.com", "indexww.com", "3lift.com", "sonobi.com", "gumgum.com", "media.net",
            "adsafeprotected.com", "doubleverify.com", "connatix.com", "primis.tech", "powerinbox.com",
            "googleadservices.com", "securepubads", "prebid", "id5-sync.com", "sascdn.com", "optimizely.com",
            "piano.io", "tinypass.com", "cxense.com", "permutive.com", "chartbeat.com", "hotjar.com")

# Consent platforms, pop-ups and ad slots: hidden, never clicked. Declining by hiding is
# the most private choice there is — nothing is stored and nothing is agreed to.
HIDE_CSS = """
#onetrust-banner-sdk,#onetrust-consent-sdk,.onetrust-pc-dark-filter,.qc-cmp2-container,#qc-cmp2-ui,#didomi-host,
div[id^="sp_message_container"],#truste-consent-track,.truste_overlay,#CybotCookiebotDialog,#usercentrics-root,
.fc-consent-root,.fc-dialog-overlay,#cmpbox,#cmpbox2,.cmp-root,#cookie-law-info-bar,.cc-window,.cookie-notice,
[id*="cookie-banner" i],[class*="cookie-banner" i],[id*="consent-banner" i],[class*="consent-banner" i],
iframe[id^="google_ads"],div[id^="google_ads"],ins.adsbygoogle,[id^="div-gpt-ad"],[class*="ad-slot" i],
[class*="adslot" i],[data-ad-slot],[class*="taboola" i],[id^="taboola"],.OUTBRAIN,[class*="outbrain" i],
[class*="newsletter-popup" i],[class*="piano" i][class*="modal" i],[class*="tp-modal"],.tp-backdrop
{display:none!important;visibility:hidden!important}
html,body{overflow:visible!important}
*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
"""

JS_CLEAN = r"""
() => {
  const vw = innerWidth, vh = innerHeight;
  const words = /(cookie|consent|privacy|gdpr|partners|advertis|subscribe|sign up|newsletter|accept all|reject all)/i;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    /* a consent dialog left its blur on the page */
    if (cs.filter && cs.filter.includes('blur')) el.style.setProperty('filter', 'none', 'important');
    if (cs.position === 'fixed' || cs.position === 'sticky') {
      const r = el.getBoundingClientRect();
      /* a sticky site header scrolls away with the page like it would on paper */
      if (cs.position === 'sticky' && r.top < 160 && r.height < 180) { el.style.setProperty('position', 'static', 'important'); continue; }
      el.style.setProperty('display', 'none', 'important');
      continue;
    }
    const role = el.getAttribute('role'), modal = el.getAttribute('aria-modal');
    if ((role === 'dialog' || role === 'alertdialog' || modal === 'true') && words.test(el.innerText || '')) {
      el.style.setProperty('display', 'none', 'important');
    }
  }
  /* empty grey boxes an ad would have filled */
  for (const el of document.querySelectorAll('div, section, aside, figure')) {
    const t = (el.innerText || '').trim().toLowerCase();
    if ((t === 'advertisement' || t === 'ad' || t === 'sponsored' || t === 'anzeige' || t === 'reklama') &&
        el.getBoundingClientRect().height > 40) el.style.setProperty('display', 'none', 'important');
  }
  /* a consent dialog inside a web component (Reddit's) sits in a shadow root that body * never reaches */
  const inShadow = (root, depth) => { if (depth > 6) return; for (const el of root.querySelectorAll('*')) {
    if (!el.shadowRoot) continue;
    const text = el.shadowRoot.textContent || '';
    if (words.test(text) && [...el.shadowRoot.querySelectorAll('*')].some(x => ['fixed', 'sticky'].includes(getComputedStyle(x).position))) {
      el.style.setProperty('display', 'none', 'important'); continue; }
    inShadow(el.shadowRoot, depth + 1); } };
  inShadow(document, 0);
  /* ...or found by its own buttons: from "Accept all" up to the box floating over the page, across shadow roots */
  const buttons = [];
  const gather = (root, depth) => { if (depth > 6) return; for (const el of root.querySelectorAll('*')) {
    if (el.matches('button, [role=button], a') && /^(accept all|reject all|reject optional cookies|allow all)$/i.test((el.textContent || '').trim())) buttons.push(el);
    if (el.shadowRoot) gather(el.shadowRoot, depth + 1); } };
  gather(document, 0);
  for (const b of buttons) {
    let el = b, hops = 0;
    while (el && hops++ < 40) {
      const cs = el.nodeType === 1 ? getComputedStyle(el) : null;
      if (cs && (cs.position === 'fixed' || cs.position === 'absolute') && el.getBoundingClientRect().width > 200) {
        el.style.setProperty('display', 'none', 'important'); break; }
      el = el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
    }
  }
  document.documentElement.style.setProperty('overflow', 'visible', 'important');
  document.body.style.setProperty('overflow', 'visible', 'important');
}
"""

JS_FIND = r"""
(expect) => {
  const norm = s => (s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const meta = p => (document.querySelector(`meta[property="${p}"], meta[name="${p}"]`) || {}).content || '';
  const og = meta('og:title') || document.title || '';
  const target = norm(expect || og);
  const words = new Set(target.split(' ').filter(w => w.length > 2));
  let best = null, bestScore = -1;
  const cands = [...document.querySelectorAll('h1, h2, [class*="headline" i], [class*="title" i], [itemprop="headline"]')]
    .filter(el => { const r = el.getBoundingClientRect(); const t = norm(el.innerText);
                    return r.width > 160 && r.height > 18 && t.length > 3 && t.length < 300 && getComputedStyle(el).visibility !== 'hidden'; });
  for (const el of cands) {
    const t = norm(el.innerText), tw = t.split(' ').filter(w => w.length > 2);
    const overlap = tw.length ? tw.filter(w => words.has(w)).length / Math.max(tw.length, Math.min(words.size, 12)) : 0;
    const fs = parseFloat(getComputedStyle(el).fontSize) || 16;
    const top = el.getBoundingClientRect().top + scrollY;
    const score = overlap * 4 + (el.tagName === 'H1' ? 1.2 : 0) + Math.min(fs, 64) / 32 - Math.max(0, top - 1400) / 700;
    if (score > bestScore) { bestScore = score; best = el; }
  }
  if (!best) return null;
  best.scrollIntoView({ block: 'center' });
  const range = document.createRange();
  range.selectNodeContents(best);
  const lines = [];
  for (const q of range.getClientRects()) {
    if (q.width < 4 || q.height < 6) continue;
    const y = q.top + scrollY, h = q.height, x = q.left + scrollX;
    const same = lines.find(l => Math.abs((l.y + l.h / 2) - (y + h / 2)) < h * 0.5);
    if (same) { const x1 = Math.min(same.x, x), x2 = Math.max(same.x + same.w, x + q.width); same.x = x1; same.w = x2 - x1;
                const y1 = Math.min(same.y, y), y2 = Math.max(same.y + same.h, y + h); same.y = y1; same.h = y2 - y1; }
    else lines.push({ x, y, w: q.width, h });
  }
  lines.sort((a, b) => a.y - b.y);
  const r = best.getBoundingClientRect();
  const text = best.innerText.replace(/\s+/g, ' ').trim();
  const tw = norm(text).split(' ').filter(w => w.length > 2);
  const match = tw.length && words.size ? tw.filter(w => words.has(w)).length / Math.max(tw.length, words.size) : 0;
  const body = (document.body.innerText || '').slice(0, 4000).toLowerCase();
  return {
    text, tag: best.tagName, match, og, site: meta('og:site_name'),
    date: meta('article:published_time') || meta('datePublished') || ((document.querySelector('time[datetime]') || {}).dateTime || ''),
    box: { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height },
    fontSize: parseFloat(getComputedStyle(best).fontSize), lines,
    pageH: document.documentElement.scrollHeight,
    walled: /(verify you are human|are you a robot|unusual traffic|press and hold|access denied|subscribe to continue|to continue reading)/.test(body),
  };
}
"""


# The line in the ARTICLE that states the claim — for a reference page, whose headline is only
# the subject's name ("Vasco da Gama"), this is the part worth highlighting.
JS_SENTENCE = r"""
(claim) => {
  const norm = s => (s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const stop = new Set('the and for that with was were are from this have has had his her its their into than then which who what when where after before about over also but not all one two its she him they them there these those been being would could should will just only very more most some such what while were thus upon unto jako jsou bylo byla byly ktery ktera ktere tak pro nebo jeho jeji jejich kdyz jenz take pouze velmi'.split(' '));
  const want = new Set(norm(claim).split(' ').filter(w => w.length > 2 && !stop.has(w)));
  const nums = (claim.match(/\d{3,4}/g) || []);
  const root = document.querySelector('#mw-content-text, article, main, [role="main"]') || document.body;
  const paras = [...root.querySelectorAll('p')].filter(p => (p.innerText || '').trim().length > 60).slice(0, 45);
  let best = null;
  for (const p of paras) {
    const text = p.textContent, re = /[^.!?]+(?:[.!?]+(?=\s|$)|$)/g;
    let m;
    while ((m = re.exec(text))) {
      const sentence = m[0];
      if (sentence.trim().length < 30) continue;
      const toks = norm(sentence).split(' ');
      const stems = new Set(toks.map(w => w.slice(0, 6)));
      let hit = 0;
      for (const w of want) if (stems.has(w.slice(0, 6))) hit++;
      let score = hit / Math.max(4, want.size);
      for (const n of nums) if (sentence.includes(n)) score += 0.25;
      if (!best || score > best.score) best = { p, start: m.index, end: m.index + sentence.length, score, text: sentence.trim() };
      if (m[0].length === 0) break;
    }
  }
  if (!best || best.score < 0.34) return null;
  const lead = best.p.textContent.slice(best.start).match(/^\s*/)[0].length;
  const S = best.start + lead, E = best.end;
  const walker = document.createTreeWalker(best.p, NodeFilter.SHOW_TEXT);
  let pos = 0, n, sn = null, so = 0, en = null, eo = 0;
  while ((n = walker.nextNode())) {
    const len = n.textContent.length;
    if (!sn && pos + len > S) { sn = n; so = S - pos; }
    if (pos + len >= E) { en = n; eo = E - pos; break; }
    pos += len;
  }
  if (!sn || !en) return null;
  best.p.scrollIntoView({ block: 'center' });
  const range = document.createRange();
  range.setStart(sn, so); range.setEnd(en, eo);
  const lines = [];
  for (const q of range.getClientRects()) {
    if (q.width < 3 || q.height < 6) continue;
    const y = q.top + scrollY, h = q.height, x = q.left + scrollX;
    const same = lines.find(l => Math.abs((l.y + l.h / 2) - (y + h / 2)) < h * 0.5);
    if (same) { const x1 = Math.min(same.x, x), x2 = Math.max(same.x + same.w, x + q.width); same.x = x1; same.w = x2 - x1;
                const y1 = Math.min(same.y, y), y2 = Math.max(same.y + same.h, y + h); same.y = y1; same.h = y2 - y1; }
    else lines.push({ x, y, w: q.width, h });
  }
  lines.sort((a, b) => a.y - b.y);
  if (!lines.length) return null;
  const x0 = Math.min(...lines.map(l => l.x)), y0 = lines[0].y, x1 = Math.max(...lines.map(l => l.x + l.w)), y1 = Math.max(...lines.map(l => l.y + l.h));
  return { text: best.text, score: best.score, lines, box: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 },
           pageH: document.documentElement.scrollHeight };
}
"""

# A video player never draws in a headless browser — no H.264, no autoplay, its media blocked —
# so a story that leads with a video photographs as a black box with a spinner. A reader sees
# the video's poster there: the player's own poster goes in, else, for the lead player, the
# picture the article shares itself with (og:image). A player already showing a picture is left alone.
JS_MEDIA = r"""
async ({ top, bottom }) => {
  const PLAYER = /(^|[\s_-])(video|player|jwplayer|jw-|brightcove|vjs|jasper|kaltura|vimeo|youtube|dailymotion|connatix|anvato)/i;
  const meta = n => ((document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || '');
  const share = meta('og:image') || meta('twitter:image');
  const cands = [];
  for (const el of document.querySelectorAll('video, iframe, div, figure, section')) {
    const name = (typeof el.className === 'string' ? el.className : '') + ' ' + el.id;
    if (!(el.tagName === 'VIDEO' || el.tagName === 'IFRAME' || PLAYER.test(name))) continue;
    const r = el.getBoundingClientRect(), y = r.top + scrollY, cs = getComputedStyle(el);
    if (r.width < 320 || r.height < 180 || y > bottom || y + r.height < top) continue;
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.1) continue;
    cands.push(el);
  }
  const size = el => el.getBoundingClientRect();
  /* the outermost box of each player: the one that is the player's size */
  const slots = cands.filter(el => !cands.some(o => o !== el && o.contains(el) &&
      Math.abs(size(o).width - size(el).width) < 4 && Math.abs(size(o).height - size(el).height) < 40))
    .sort((a, b) => size(a).top - size(b).top);
  const painted = el => {
    const r = size(el), area = r.width * r.height;
    for (const im of el.querySelectorAll('img')) {
      const q = im.getBoundingClientRect(), cs = getComputedStyle(im);
      if (im.complete && im.naturalWidth > 64 && q.width * q.height > area * 0.5 && cs.visibility !== 'hidden' && +cs.opacity > 0.5) return true;
    }
    return [...el.querySelectorAll('video')].some(v => v.readyState >= 2);
  };
  const posterOf = el => {
    const v = el.tagName === 'VIDEO' ? el : el.querySelector('video[poster]');
    if (v && v.poster) return v.poster;
    const p = el.matches('[data-poster]') ? el : el.querySelector('[data-poster]');
    if (p) return p.getAttribute('data-poster');
    const jw = el.matches('[data-jw-media_id]') ? el : el.querySelector('[data-jw-media_id]');
    if (jw) return `https://cdn.jwplayer.com/v2/media/${jw.getAttribute('data-jw-media_id')}/poster.jpg?width=1280`;
    for (const d of el.querySelectorAll('.jw-preview, .vjs-poster, [class*="poster" i]')) {
      const m = getComputedStyle(d).backgroundImage.match(/url\(["']?(.*?)["']?\)/);
      if (m) return m[1];
    }
    return '';
  };
  const load = src => new Promise(res => {
    if (!src) return res('');
    const im = new Image();
    const t = setTimeout(() => res(''), 6000);
    im.onload = () => { clearTimeout(t); res(im.naturalWidth > 64 ? src : ''); };
    im.onerror = () => { clearTimeout(t); res(''); };
    im.src = src;
  });
  const filled = [];
  let lead = true;
  for (const el of slots) {
    if (painted(el)) { lead = false; continue; }
    const src = (await load(posterOf(el))) || (lead ? await load(share) : '');
    lead = false;
    if (!src) continue;
    if (getComputedStyle(el).position === 'static') el.style.setProperty('position', 'relative', 'important');
    const im = document.createElement('img');
    im.style.cssText = 'position:absolute!important;left:0!important;top:0!important;width:100%!important;' +
      'height:100%!important;max-width:none!important;margin:0!important;object-fit:cover!important;' +
      'display:block!important;opacity:1!important;z-index:2147483000!important';
    await new Promise(r => { im.onload = im.onerror = r; im.src = src; });
    el.appendChild(im);
    filled.push({ src: src.slice(0, 100), w: Math.round(size(el).width), h: Math.round(size(el).height) });
  }
  return filled;
}
"""


def _domain(url: str) -> str:
    return re.sub(r"^www\.", "", urlparse(url).netloc.lower())


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def capture(url: str, out_png: Path, expect: str = "", browser=None, claim: str = "") -> dict:
    """Open `url`, clear it and photograph the part around its headline — or, with a
    `claim`, around the sentence in the article that states it, when there is one.

    Returns {ok, url, domain, site, date, text, target, lines (image px), img_w, img_h} or
    {ok: False, why}. `expect` is the headline the search said this page has: a page
    whose headline turns out to be something else (a live blog that moved on) is refused."""
    from playwright.sync_api import sync_playwright
    own = browser is None
    pw = None
    if own:
        import motion
        with motion._PW_START:
            pw = sync_playwright().start()
        browser = pw.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb"])
    ctx = browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, device_scale_factor=SCALE,
                              user_agent=UA, locale="en-US", color_scheme="light")
    page = ctx.new_page()
    page.route("**/*", lambda route: route.abort()
               if any(h in route.request.url for h in AD_HOSTS) or route.request.resource_type == "media"
               else route.continue_())
    out = {"ok": False, "url": url, "domain": _domain(url)}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp is not None and resp.status >= 400:
            out["why"] = f"HTTP {resp.status}"
            return out
        try:
            page.wait_for_load_state("load", timeout=9000)
        except Exception:                                   # noqa: BLE001 - slow trackers never finish
            pass
        page.add_style_tag(content=HIDE_CSS)
        page.wait_for_timeout(1300)
        # a consent dialog the page draws inside a closed web component cannot be hidden from outside: the
        # privacy-preserving answer (reject what is optional) closes it — never "accept"
        for label in ("Reject Optional Cookies", "Reject all", "Reject All", "Decline optional cookies"):
            try:
                btn = page.get_by_role("button", name=label, exact=True)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=1500)
                    page.wait_for_timeout(400)
                    break
            except Exception:                               # noqa: BLE001 - no dialog is the usual case
                pass
        page.evaluate(JS_CLEAN)
        page.wait_for_timeout(250)
        found = page.evaluate(JS_FIND, expect or "")
        if not found:
            out["why"] = "no headline on the page"
            return out
        if found["walled"] and found["match"] < 0.5:
            out["why"] = "a wall (captcha, paywall or 'access denied')"
            return out
        if expect and found["match"] < 0.45:
            out["why"] = f"the headline is something else: {found['text'][:90]!r}"
            return out
        target, kind = found, "headline"
        if claim:
            sent = page.evaluate(JS_SENTENCE, claim)
            if sent:
                target, kind = sent, "sentence"
        # Pictures below the first screen load only when scrolled to: ask for them now and
        # give them a moment, or the photograph has empty frames where the images go.
        page.evaluate("""() => { for (const im of document.images) { im.loading = 'eager';
                          if (im.dataset && im.dataset.src && !im.src) im.src = im.dataset.src; } }""")
        try:
            page.wait_for_function("""(y) => [...document.images].filter(im => { const r = im.getBoundingClientRect();
                                        const top = r.top + scrollY; return top < y + 1500 && top + r.height > y - 400 && r.width > 40; })
                                        .every(im => im.complete)""", arg=target["box"]["y"], timeout=6000)
        except Exception:                                   # noqa: BLE001 - a slow image is not worth the page
            pass
        try:
            y0 = max(0, target["box"]["y"] - 400)
            out["posters"] = page.evaluate(JS_MEDIA, {"top": y0, "bottom": y0 + 1600})
        except Exception:                                   # noqa: BLE001 - a black player is not worth the page
            pass
        page.evaluate(JS_CLEAN)                             # the scroll can bring new pop-ups
        if kind == "sentence":
            target = page.evaluate(JS_SENTENCE, claim) or target   # images that arrived may have moved the text
        box = target["box"]
        above = 300 if kind == "headline" else 380
        top = max(0, int(box["y"] - above))
        height = int(min(1500, box["h"] + above + 640, target["pageH"] - top))
        page.screenshot(path=str(out_png), full_page=True, clip={"x": 0, "y": top, "width": VIEW_W, "height": height})
        lines = [{"x": l["x"] * SCALE, "y": (l["y"] - top) * SCALE, "w": l["w"] * SCALE, "h": l["h"] * SCALE}
                 for l in target["lines"] if l["y"] + l["h"] > top]
        out.update({"ok": True, "text": found["text"], "target": kind, "quote": target["text"] if kind == "sentence" else "",
                    "site": found["site"], "date": found["date"], "match": round(found["match"], 3),
                    "lines": lines, "img_w": VIEW_W * SCALE, "img_h": height * SCALE})
        return out
    except Exception as e:                                  # noqa: BLE001 - one page is never worth the video
        out["why"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out
    finally:
        ctx.close()
        if own:
            browser.close()
            pw.stop()


# ════════════════════════════════════════════════════════════════════════════
# THE WINDOW — the photograph under a browser bar, as one picture
# ════════════════════════════════════════════════════════════════════════════
BAR_H = 64          # CSS px of the browser bar, doubled in the picture


def _font(name: str, size: int):
    from PIL import ImageFont
    f = HERE / "assets" / "fonts" / name
    try:
        return ImageFont.truetype(str(f), size)
    except OSError:
        return ImageFont.load_default()


def compose_window(png: Path, info: dict, out_jpg: Path) -> dict:
    """The capture with a plain browser bar on top (dots, a padlock, the address), saved as
    one JPEG. Returns the picture's size and the headline lines moved under the bar."""
    from PIL import Image, ImageDraw
    shot = Image.open(png).convert("RGB")
    s = SCALE
    bar = BAR_H * s
    win = Image.new("RGB", (shot.width, shot.height + bar), (255, 255, 255))
    win.paste(shot, (0, bar))
    d = ImageDraw.Draw(win)
    d.rectangle([0, 0, win.width, bar], fill=(236, 238, 241))
    d.line([0, bar - 1, win.width, bar - 1], fill=(212, 215, 220), width=2)
    for i, c in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        cx, cy, r = (30 + i * 24) * s, bar // 2, 7 * s
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    x0, x1 = 200 * s, min(win.width - 200 * s, 1240 * s)
    d.rounded_rectangle([x0, 14 * s, x1, bar - 14 * s], radius=18 * s, fill=(255, 255, 255))
    # a small padlock, then the address in the channel-neutral UI face
    lx, ly = x0 + 22 * s, bar // 2
    d.rounded_rectangle([lx - 6 * s, ly - 2 * s, lx + 6 * s, ly + 8 * s], radius=2 * s, fill=(95, 99, 104))
    d.arc([lx - 4 * s, ly - 10 * s, lx + 4 * s, ly + 2 * s], 180, 360, fill=(95, 99, 104), width=2 * s)
    host = info.get("domain") or _domain(info.get("url", ""))
    path = urlparse(info.get("url", "")).path.rstrip("/")
    d.text((lx + 20 * s, ly), host, font=_font("Inter-SemiBold.ttf", 17 * s), fill=(32, 33, 36), anchor="lm")
    tw = d.textlength(host, font=_font("Inter-SemiBold.ttf", 17 * s))
    if path:
        room = x1 - (lx + 20 * s + tw) - 24 * s
        f2 = _font("Inter-SemiBold.ttf", 17 * s)
        shown = path
        while shown and d.textlength(shown, font=f2) > room:
            shown = shown[:-2]
        if shown != path and len(shown) > 3:
            shown = shown[:-1] + "…"
        if shown:
            d.text((lx + 20 * s + tw, ly), shown, font=f2, fill=(128, 134, 139), anchor="lm")
    win.save(out_jpg, "JPEG", quality=90, optimize=True)
    lines = [dict(l, y=l["y"] + bar) for l in info["lines"]]
    return {"img_w": win.width, "img_h": win.height, "lines": lines, "dark": _is_dark(win, lines)}


def _is_dark(img, lines: list) -> bool:
    """White type on a dark header (AP does this). A multiplied marker would only turn the
    letters yellow, so a dark page gets a lit bar behind the words and a line under them."""
    if not lines:
        return False
    from PIL import Image, ImageStat
    if not hasattr(img, "crop"):
        img = Image.open(img)
    x0, y0 = int(min(l["x"] for l in lines)), int(min(l["y"] for l in lines))
    x1, y1 = int(max(l["x"] + l["w"] for l in lines)), int(max(l["y"] + l["h"] for l in lines))
    if x1 <= x0 or y1 <= y0:
        return False
    return ImageStat.Stat(img.crop((x0, y0, x1, y1)).convert("L")).mean[0] < 110


# ════════════════════════════════════════════════════════════════════════════
# PAGE + RENDER
# ════════════════════════════════════════════════════════════════════════════
SKINS = {
    "default": {"marker": "rgba(242,208,39,0.86)", "dim": "6,8,12", "bg": "#07080a",
                "vignette": "radial-gradient(ellipse 70% 68% at 50% 46%, transparent 38%, rgba(0,0,0,.78) 100%)",
                "creditInk": "#ffffff", "creditSoft": "rgba(255,255,255,.62)"},
}

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>__FONTS__
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px;overflow:hidden;background:__BG__}
#stage{position:relative;width:__W__px;height:__H__px;overflow:hidden;background:__BG__}
#bg{position:absolute;left:-6%;top:-6%;width:112%;height:112%;object-fit:cover;
  filter:blur(34px) brightness(.30) saturate(.75);transform-origin:50% 45%}
#win{position:absolute;left:0;top:0;border-radius:18px;overflow:hidden;background:#fff;
  box-shadow:0 60px 140px rgba(0,0,0,.65),0 0 0 1px rgba(255,255,255,.08);will-change:transform}
#shot,#dof{position:absolute;left:0;top:0;width:100%;height:100%;display:block}
#dof{filter:blur(7px);opacity:0;will-change:opacity;pointer-events:none}
.ghost{position:absolute;left:0;top:0;display:none;border-radius:18px;pointer-events:none;mix-blend-mode:screen}
#ghostR{filter:sepia(1) saturate(9) hue-rotate(-50deg)}
#ghostC{filter:sepia(1) saturate(9) hue-rotate(140deg)}
.bar{position:absolute;transform-origin:0 50%;mix-blend-mode:multiply}
#dim{position:absolute;border-radius:14px;pointer-events:none}
#sheen{position:absolute;inset:0;pointer-events:none;mix-blend-mode:screen;
  background:linear-gradient(105deg,transparent 38%,rgba(255,255,255,.42) 50%,transparent 62%)}
#vig{position:absolute;inset:0;pointer-events:none;background:__VIG__}
#grain{position:absolute;left:0;top:0;width:2432px;height:1592px;opacity:.055;mix-blend-mode:overlay;
  image-rendering:pixelated;pointer-events:none}
#credit{position:absolute;left:84px;bottom:70px;font:600 30px/1.2 'Inter Semi Bold',Inter,sans-serif;
  color:__CINK__;letter-spacing:.5px;opacity:0;padding-left:22px;border-left:5px solid __MARK__;
  text-shadow:0 3px 16px rgba(0,0,0,.7)}
</style></head><body>
<div id="stage">
  <img id="bg" alt="">
  <img id="ghostR" class="ghost" alt=""><img id="ghostC" class="ghost" alt="">
  <div id="win"><img id="shot" alt=""><img id="dof" alt=""><div id="dim"></div><div id="sheen"></div></div>
  <div id="vig"></div>
  <canvas id="grain"></canvas>
  <div id="credit"></div>
</div>
<script>const SCENE=__SCENE__;const PAL=__PAL__;const W=__W__,H=__H__;</script>
<script>__JS__</script>
</body></html>"""


def build_html(scene: dict) -> str:
    try:
        import maps                                             # shares the font embedder when the MAPS DLC is here
        fonts = maps._font_css("'Inter Semi Bold',Inter,sans-serif")
    except Exception:                                           # noqa: BLE001
        fonts = _font_css()
    pal = dict(SKINS.get(scene.get("skin", "default")) or SKINS["default"])
    if scene.get("marker"):
        pal["marker"] = scene["marker"]
    return (_PAGE.replace("__FONTS__", fonts)
            .replace("__SCENE__", json.dumps(scene, separators=(",", ":")))
            .replace("__PAL__", json.dumps(pal))
            .replace("__JS__", (ASSETS / "page.js").read_text(encoding="utf-8"))
            .replace("__VIG__", pal["vignette"]).replace("__BG__", pal["bg"])
            .replace("__CINK__", pal["creditInk"]).replace("__MARK__", pal["marker"])
            .replace("__W__", str(W)).replace("__H__", str(H)))


def _font_css() -> str:
    f = HERE / "assets" / "fonts" / "Inter-SemiBold.ttf"
    if not f.exists():
        return ""
    b64 = base64.b64encode(f.read_bytes()).decode()
    return f"@font-face{{font-family:'Inter Semi Bold';src:url(data:font/ttf;base64,{b64});font-display:block}}"


def render(jobs: list, workers: int = 2, fps: int = FPS) -> list:
    """jobs = [(scene, out_mp4)] — the frame-by-frame Chromium loop every Frontier scene uses."""
    import motion
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="headlines_"))
    prepared = [(build_html(sc), float(sc.get("duration", 6.5)), Path(out), tmp / f"f{i:03d}")
                for i, (sc, out) in enumerate(jobs)]
    workers = max(1, min(workers, len(prepared)))

    def one(chunk):
        last = None
        for attempt in range(3):
            pw = None
            try:
                with motion._PW_START:
                    pw = sync_playwright().start()
                browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu",
                                                   "--font-render-hinting=none"])
                for html, dur, out, fdir in chunk:
                    if out.exists() and out.stat().st_size > 10_000:
                        continue
                    fdir.mkdir(parents=True, exist_ok=True)
                    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                    page.set_content(html, wait_until="load")
                    page.wait_for_function("window.__ready===true", timeout=60000)
                    for k in range(max(1, int(round(dur * fps)))):
                        page.evaluate("(t)=>window.renderFrame(t)", k / fps)
                        page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"), type="jpeg", quality=93,
                                        clip={"x": 0, "y": 0, "width": W, "height": H})
                    motion._frames_to_mp4(fdir, out, fps)
                    shutil.rmtree(fdir, ignore_errors=True)
                    page.close()
                browser.close()
                return
            except Exception as e:                              # noqa: BLE001 - driver flakiness
                last = e
                time.sleep(3.0 * (attempt + 1))
            finally:
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:                           # noqa: BLE001
                        pass
        raise RuntimeError(f"headlines: rendering failed: {last}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, [prepared[i::workers] for i in range(workers)]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [Path(o) for _, o in jobs]


def _credit(info: dict) -> str:
    site = (info.get("site") or "").strip() or (info.get("outlet") or "").strip() or info.get("domain", "")
    if info.get("target") == "sentence" and info.get("text") and info.get("text", "").lower() not in site.lower():
        site = f"{site} — {info['text'][:60]}"
    date = ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", info.get("date") or "")
    if m:
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                  "October", "November", "December"]
        date = f"{int(m.group(3))} {months[int(m.group(2)) - 1]} {m.group(1)}"
    return f"{site}  ·  {date}" if date and info.get("target") != "sentence" else site


def scene_from_capture(info: dict, jpg: Path, geo: dict, duration: float = 6.5, hit: float = 1.4,
                       seed: int = 7, skin: str = "default") -> dict:
    import motion
    return {"image": motion.image_data_uri(jpg), "img_w": geo["img_w"], "img_h": geo["img_h"],
            "lines": geo["lines"], "duration": float(duration), "hit": float(hit), "seed": int(seed),
            "credit": _credit(info), "skin": skin, "target": info.get("target", "headline"),
            "dark": bool(geo.get("dark"))}


# ════════════════════════════════════════════════════════════════════════════
# SEARCH — free, no key: Bing News for news, Wikipedia for reference
# ════════════════════════════════════════════════════════════════════════════
# Pages that are never worth photographing: portals, social networks, video pages, paywalls.
SKIP_DOMAINS = ("msn.com", "bing.com", "youtube.com", "facebook.com", "x.com", "twitter.com", "instagram.com",
                "tiktok.com", "reddit.com", "google.com", "yahoo.com", "apple.news", "linkedin.com", "pinterest.",
                "nytimes.com", "wsj.com", "ft.com", "bloomberg.com", "economist.com", "washingtonpost.com",
                "theathletic.com", "barrons.com", "newyorker.com", "telegraph.co.uk", "thetimes.co.uk", "haaretz.com")
_HTTP_UA = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}


def _get(url: str, timeout: float = 15.0) -> str:
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers=_HTTP_UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def bing_news(query: str, lang: str = "en", n: int = 8) -> list:
    """[{title, url, outlet, date}] from Bing News' RSS feed, skipping portals and paywalls."""
    import html as _html
    import urllib.parse
    cc = {"en": "US", "cs": "CZ", "sk": "SK", "de": "DE", "fr": "FR", "es": "ES", "it": "IT", "pl": "PL"}.get(lang, "US")
    try:
        x = _get("https://www.bing.com/news/search?format=rss&setlang=" + lang + "&cc=" + cc +
                 "&q=" + urllib.parse.quote(query))
    except Exception:                                           # noqa: BLE001 - no results, not a crash
        return []
    out = []
    for it in re.findall(r"<item>(.*?)</item>", x, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", it, re.S)
            return _html.unescape(m.group(1)).strip() if m else ""
        link = tag("link")
        url = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0]
        dom = _domain(url)
        if not url.startswith("http") or any(dom == d or dom.endswith("." + d) or d in dom for d in SKIP_DOMAINS):
            continue
        out.append({"title": re.sub(r"<[^>]+>", "", tag("title")), "url": url, "outlet": tag("News:Source") or dom,
                    "date": tag("pubDate")})
        if len(out) >= n:
            break
    return out


def wikipedia(query: str, lang: str = "en", n: int = 4) -> list:
    import urllib.parse
    try:
        data = json.loads(_get(f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&format=json"
                               f"&srlimit={n}&srsearch=" + urllib.parse.quote(query)))
    except Exception:                                           # noqa: BLE001
        return []
    return [{"title": r["title"], "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
             "outlet": "Wikipedia", "date": ""} for r in (data.get("query") or {}).get("search", [])]


# ════════════════════════════════════════════════════════════════════════════
# TIMELINE — the moments, the pages, the scenes
# ════════════════════════════════════════════════════════════════════════════
HL_DUR = 6.5
LEAD_S = 1.3                # the window rises and the camera pushes in; the marker starts on the words
HL_EVERY_S = float(os.environ.get("HEADLINES_EVERY_S", "60"))
HL_GAP_S = float(os.environ.get("HEADLINES_GAP_S", "25"))
PROTECTED = ("introcap", "map_", "headline_")     # scenes a DLC placed on a word: nobody else removes them

MOMENTS_PROMPT = """You are the researcher for a documentary-style YouTube video. At some moments the edit
shows a REAL published page on screen — a news story or an encyclopedia article — with the line that
backs up the narration highlighted in yellow. It makes the video feel researched.

Below is the full voiceover as timed subtitles, one cue per line: #number [minute:second] text.

Pick the moments where a real page would back up what is said:
- a specific event, announcement, decision, lawsuit, deal, record, study or official report;
- a fact with a date, a number or a named source ("according to Reuters", "a 2019 Harvard study");
- a documented statement by a public figure;
- a historical or scientific fact an encyclopedia states plainly.
Never for: opinions, advice, the narrator's own reasoning, a fictional or hypothetical story, common
knowledge nobody would look up, or anything the narration only hints at.

At most [INSERT MAX HERE] moments, at least [INSERT GAP HERE] seconds apart. Fewer is fine — an empty
array is the right answer for a video that makes no checkable claims.

Return a JSON array, one object per moment:
{"cue": 12, "words": "addressed the UN General Assembly", "kind": "news",
 "query": "Netanyahu UN General Assembly speech September 2026",
 "claim": "Netanyahu addressed the UN General Assembly in September 2026.",
 "lang": "en"}

- "cue": the number of the cue in which the claim is SPOKEN.
- "words": 2 to 6 words exactly as written in that cue, copied letter for letter (keep the language).
- "kind": "news" when news outlets reported it (recent or long ago); "reference" when it is background
  an encyclopedia states (history, science, geography, a biography).
- "query": what to type into a news search (for news) or into Wikipedia (for reference). Name the
  people, places and the year. For reference: just the article's subject, e.g. "Vasco da Gama".
- "claim": the fact the page has to state, one plain sentence in English.
- "lang": the language of the best source — "en" unless the story is local to a country whose own
  press is the natural source (a Czech story: "cs").

TRANSCRIPT:
[INSERT TRANSCRIPT HERE]
"""

PICK_PROMPT = """For each moment below: the fact the narration states, and the pages a search found
(title, outlet, date). Choose the pages that state THAT fact — the same event and the same claim, not
merely the same topic. Prefer established outlets and the original report; avoid live blogs, opinion
columns, press releases, video pages, sports scores and aggregators. For a "reference" moment choose the
encyclopedia article about the subject.

Return a JSON array, one object per moment: {"moment": 1, "pick": [3, 1]} — up to 3 page numbers,
best first, or "pick": [] when none of them states the fact.

[INSERT MOMENTS HERE]
"""


def _stamp_s(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _find_cue(entries: list, n: int, words: str) -> int:
    key = _fold(words)
    if not key:
        return n if 0 <= n < len(entries) else -1
    order = sorted(range(max(0, n - 6), min(len(entries), n + 7)), key=lambda i: abs(i - n))
    for i in order:
        if key in _fold(entries[i][2]):
            return i
    for i in order:
        if i + 1 < len(entries) and key in _fold(entries[i][2] + " " + entries[i + 1][2]):
            return i
    return -1


def _word_time(words: str, cue: tuple, mv) -> float:
    st, en, txt = cue
    timed = mv._cue_words(txt, st, en)
    want = [_fold(w) for w in str(words or "").split() if _fold(w)]
    if not timed or not want:
        return st
    folded = [_fold(w["w"]) for w in timed]
    for i in range(len(folded)):
        if folded[i:i + len(want)] == want:
            return float(timed[i]["t"])
    for i, f in enumerate(folded):
        if f and f == want[0]:
            return float(timed[i]["t"])
    return st


def _settings(style: str, mv) -> dict:
    look = ((getattr(mv, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
    cfg = look.get("headlines") if isinstance(look.get("headlines"), dict) else {}
    return {"on": look.get("headlines") is not False and cfg.get("enabled", True) is not False,
            "every_s": float(cfg.get("every_s") or HL_EVERY_S), "gap_s": float(cfg.get("gap_s") or HL_GAP_S),
            "marker": str(cfg.get("marker") or "")}


def plan_moments(srt: Path, job: Path, style: str, force: bool, total: float, mv) -> list:
    out_dir = job / "headlines"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "moments.json"
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    cfg = _settings(style, mv)
    entries = mv._parse_srt_full(srt.read_text(encoding="utf-8"))
    most = max(2, int(total / cfg["every_s"]))
    prompt = (MOMENTS_PROMPT.replace("[INSERT MAX HERE]", str(most))
              .replace("[INSERT GAP HERE]", str(int(cfg["gap_s"])))
              .replace("[INSERT TRANSCRIPT HERE]",
                       "\n".join(f"#{i + 1} [{_stamp_s(st)}] {txt}" for i, (st, en, txt) in enumerate(entries))))
    mv.log(f"headlines: Claude is looking for claims a real article backs up (up to {most})...")
    moments = [m for m in mv._json_items(prompt, max_tokens=max(3000, most * 350)) if isinstance(m, dict)]
    cache.write_text(json.dumps(moments, indent=2, ensure_ascii=False), encoding="utf-8")
    return moments


def find_pages(moments: list, job: Path, force: bool, mv) -> list:
    """For every moment, the candidate pages in the order to try them. Cached in picks.json."""
    cache = job / "headlines" / "picks.json"
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    cands = []
    for m in moments:
        q, lang = str(m.get("query") or m.get("claim") or ""), str(m.get("lang") or "en")[:2].lower()
        if str(m.get("url") or "").startswith("http"):
            # the page itself is named (a subreddit, a forum thread, a company's post): photograph that one
            cands.append([{"title": "", "url": str(m["url"]), "outlet": _domain(str(m["url"])), "date": ""}])
            continue
        if str(m.get("kind")) == "reference":
            found = wikipedia(q, lang) + (wikipedia(q, "en") if lang != "en" else [])
        else:
            found = bing_news(q, lang) + (bing_news(q, "en") if lang != "en" else [])
            if len(found) < 3 and m.get("claim"):
                found += bing_news(str(m["claim"])[:120], "en")
        seen, uniq = set(), []
        for c in found:
            if c["url"] not in seen:
                seen.add(c["url"])
                uniq.append(c)
        cands.append(uniq[:10])
    blocks = []
    for i, (m, cs) in enumerate(zip(moments, cands), 1):
        rows = "\n".join(f"  {j}. {c['title']} — {c['outlet']}{(' — ' + c['date'][:16]) if c['date'] else ''}"
                         for j, c in enumerate(cs, 1)) or "  (nothing found)"
        blocks.append(f"MOMENT {i} ({m.get('kind', 'news')}): {m.get('claim', '')}\n{rows}")
    picks = []
    named = {i for i, m in enumerate(moments) if str(m.get("url") or "").startswith("http")}
    if any(cands) and len(named) < len(moments):
        mv.log(f"headlines: Claude is choosing among {sum(len(c) for c in cands)} pages...")
        answer = mv._json_items(PICK_PROMPT.replace("[INSERT MOMENTS HERE]", "\n\n".join(blocks)), max_tokens=2000)
        by = {int(a.get("moment", 0)): a.get("pick") or [] for a in answer if isinstance(a, dict)}
        for i, cs in enumerate(cands, 1):
            order = cs[:1] if (i - 1) in named else [cs[k - 1] for k in by.get(i, []) if isinstance(k, int) and 1 <= k <= len(cs)]
            picks.append(order[:3])
    else:
        picks = [cs[:1] if i in named else [] for i, cs in enumerate(cands)]
    cache.write_text(json.dumps(picks, indent=2, ensure_ascii=False), encoding="utf-8")
    return picks


def _overlaps(a0, a1, windows, margin=3.0) -> bool:
    return any(a0 < w1 + margin and w0 - margin < a1 for w0, w1 in windows)


def add_to_timeline(segs: list, srt: Path, job: Path, style: str, force: bool, total: float = 0.0,
                    workers: int = 2, engine=None) -> list:
    """The HEADLINES DLC's entry point from the engine: real pages at the words they back up.

    A regular graphic in the way gives way; the opening caption and any scene another DLC
    placed on a word (a map) do not — a headline that would land on one is dropped."""
    if engine is None:
        import make_video as engine
    mv = engine
    if not (ASSETS / "page.js").exists() or not srt.exists():
        return segs
    cfg = _settings(style, mv)
    if not cfg["on"]:
        return segs
    total = float(total or mv._audio_dur(job / "audio.mp3"))
    out_dir = job / "headlines"
    out_dir.mkdir(parents=True, exist_ok=True)
    blocked = [(float(s), float(s) + float(d)) for s, p, d in segs if Path(p).name.startswith(PROTECTED)]
    # a directed video (director.py) spaced its scenes itself: only a real collision counts
    pad = 0.0 if (job / "director.json").exists() else 3.0
    moments = plan_moments(srt, job, style, force, total, mv)
    if not moments:
        mv.log("headlines: no claim in this narration needs a source on screen")
        return segs
    picks = find_pages(moments, job, force, mv)
    entries = mv._parse_srt_full(srt.read_text(encoding="utf-8"))

    timed = []
    for k, m in enumerate(moments):
        try:
            n = int(m.get("cue") or 0) - 1
        except (TypeError, ValueError):
            n = -1
        i = _find_cue(entries, n, m.get("words") or "")
        # the director already timed this moment word by word (director.py): trust it over a text match
        if m.get("at_s") is not None:
            try:
                _t = float(m["at_s"])
                i = next((c for c, e in enumerate(entries) if e[0] - 0.05 <= _t < e[1] + 0.05), len(entries) - 1)
            except (TypeError, ValueError):
                _t = None
        else:
            _t = None
        if i < 0:
            mv.log(f"  headlines: skipped {m.get('words')!r} — not found in the subtitles near cue {n + 1}")
            continue
        if k >= len(picks) or not picks[k]:
            mv.log(f"  headlines: no page states {str(m.get('claim'))[:80]!r}")
            continue
        timed.append((_t if _t is not None else _word_time(m.get("words") or "", entries[i], mv), k, m))
    timed.sort()

    from playwright.sync_api import sync_playwright
    import motion
    scenes, last_end = [], -1e9
    with motion._PW_START:
        pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb"])
    try:
        for at, k, m in timed:
            start = max(0.3, at - LEAD_S)
            hit = at - start
            # a page squeezed between two scenes (the director's plan says how long it has) runs shorter
            hl_dur = min(HL_DUR, max(3.4, float(m.get("dur") or HL_DUR)))
            if _overlaps(start, start + hl_dur, blocked, pad):
                # a map already sits on these words: the page may follow it, a few seconds late at most
                after = max(w1 for w0, w1 in blocked if start < w1 + 3.0 and w0 - 3.0 < start + hl_dur) + 3.0
                if after - start <= 5.0 and not _overlaps(after, after + hl_dur, blocked, pad):
                    start, hit = after, LEAD_S
                else:
                    mv.log(f"  headlines: skipped {_stamp_s(at)} — another scene is on screen there")
                    continue
            end = start + hl_dur
            if end > total - 0.3:
                continue
            # the director spaced its scenes itself (two pages may follow each other): only a real overlap counts
            if start < last_end + (0.3 if m.get("at_s") is not None else cfg["gap_s"]):
                mv.log(f"  headlines: skipped {_stamp_s(at)} — too close to the page before it")
                continue
            png, jpg, meta = out_dir / f"cap_{k:02d}.png", out_dir / f"cap_{k:02d}.jpg", out_dir / f"cap_{k:02d}.json"
            info = None
            if meta.exists() and jpg.exists() and not force:
                try:
                    info = json.loads(meta.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    info = None
            if info is None:
                claim = str(m.get("claim") or "") if str(m.get("kind")) == "reference" else ""
                for c in picks[k]:
                    got = capture(c["url"], png, c.get("title", ""), browser=browser, claim=claim)
                    if got["ok"]:
                        info = dict(got, outlet=c.get("outlet", ""))
                        info.update(compose_window(png, info, jpg))
                        meta.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
                        png.unlink(missing_ok=True)
                        break
                    mv.log(f"  headlines: skipped {c['url'][:70]} — {got.get('why')}")
            if info is None:
                continue
            if "dark" not in info:                          # captured before pages were told apart
                info["dark"] = _is_dark(jpg, info.get("lines") or [])
                meta.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
            sc = scene_from_capture(info, jpg, info, hl_dur, hit, seed=31 + k * 7)
            # never the same move twice in a row: a window, a printed page, a scan (page.js)
            sc["variant"] = ("window", "paper", "scan")[(k + abs(hash(job.name))) % 3]
            if cfg["marker"]:
                sc["marker"] = cfg["marker"]
            scenes.append((round(start, 3), k, sc))
            last_end = end
    finally:
        browser.close()
        pw.stop()
    if not scenes:
        mv.log("headlines: no page could be shown this time")
        return segs

    jobs, placed = [], []
    for start, k, sc in scenes:
        mp4, stamp_f = out_dir / f"headline_{k:02d}.mp4", out_dir / f"headline_{k:02d}.stamp"
        stamp = json.dumps({x: sc.get(x) for x in ("lines", "duration", "hit", "credit", "img_w", "img_h", "marker",
                                                   "dark", "target")}, sort_keys=True)
        if force or not mp4.exists() or not stamp_f.exists() or stamp_f.read_text(encoding="utf-8") != stamp:
            mp4.unlink(missing_ok=True)
            stamp_f.write_text(stamp, encoding="utf-8")
            jobs.append((sc, mp4))
        placed.append((start, mp4, float(sc["duration"])))
    if jobs:
        mv.log(f"headlines: rendering {len(jobs)} page scene(s) in Chromium...")
        render(jobs, workers=workers)
    placed = [p for p in placed if p[1].exists()]
    kept = [(s, p, d) for s, p, d in segs
            if Path(p).name.startswith(PROTECTED) or not _overlaps(float(s), float(s) + float(d),
                                                                  [(a, a + b) for a, _, b in placed], pad)]
    mv.log(f"headlines: {len(placed)} page(s) on screen — " +
           ", ".join(f"{_stamp_s(s)} {json.loads((out_dir / f'cap_{k:02d}.json').read_text(encoding='utf-8')).get('domain', '')}"
                     for s, k, _ in scenes[:6]))
    return sorted(kept + [(s, str(p), d) for s, p, d in placed], key=lambda x: float(x[0]))


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════
def check(out: Path = None) -> int:
    """A reference page and a news search, straight through capture, the window and a few
    rendered frames: reports any script error in seconds. Writes preview/_headlines_check.png."""
    from playwright.sync_api import sync_playwright
    out = Path(out or HERE / "preview")
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    png, jpg = out / "_headlines_check_page.png", out / "_headlines_check_page.jpg"
    info = capture("https://en.wikipedia.org/wiki/Moon_landing", png, "Moon landing",
                   claim="Apollo 11 was the first crewed mission to land on the Moon, in July 1969.")
    print(f"  {'ok  ' if info['ok'] else 'FAIL'} capture: {info.get('target') or info.get('why')}"
          f" — {(info.get('quote') or info.get('text') or '')[:80]!r}")
    if not info["ok"]:
        return 1
    geo = compose_window(png, info, jpg)
    sc = scene_from_capture(info, jpg, geo, 6.5, 1.3)
    news = bing_news("central bank interest rates", "en", 3)
    print(f"  {'ok  ' if news else 'FAIL'} news search: {len(news)} results" + (f", e.g. {news[0]['title'][:60]!r}" if news else ""))
    bad += not news
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": W, "height": H})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.set_content(build_html(sc), wait_until="load")
        try:
            page.wait_for_function("window.__ready===true", timeout=60000)
            for t in (0.4, 0.7, 2.2, 4.8):
                page.evaluate("(t)=>window.renderFrame(t)", t)
            page.screenshot(path=str(out / "_headlines_check.png"))
        except Exception as e:                                  # noqa: BLE001
            errors.append(str(e))
        browser.close()
    print(f"  {'ok  ' if not errors else 'FAIL'} page: " + (str(errors[0])[:200] if errors else "renders"))
    png.unlink(missing_ok=True)
    return bad + bool(errors)


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="HEADLINES DLC")
    ap.add_argument("cmd", choices=["show", "find", "check"])
    ap.add_argument("what", nargs="?", default="", help="show: a page address · find: a search")
    ap.add_argument("--claim", default="", help="show: highlight the sentence that states this instead of the headline")
    ap.add_argument("--expect", default="")
    ap.add_argument("--reference", action="store_true", help="find: search Wikipedia instead of the news")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--seconds", type=float, default=6.5)
    ap.add_argument("--hit", type=float, default=1.3)
    ap.add_argument("--out", default=str(HERE / "preview"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "check":
        sys.exit(1 if check(out) else 0)
    if a.cmd == "find":
        for c in (wikipedia if a.reference else bing_news)(a.what, a.lang):
            print(f"  {c['outlet'][:24]:24s} {c['date'][:16]:16s} {c['title'][:90]}\n  {'':24s} {c['url']}")
        return
    stem = "headline_" + re.sub(r"[^a-z0-9]+", "_", _domain(a.what))[:40]
    png, jpg, mp4 = out / f"{stem}.png", out / f"{stem}.jpg", out / f"{stem}.mp4"
    t0 = time.time()
    info = capture(a.what, png, a.expect, claim=a.claim)
    if not info["ok"]:
        sys.exit(f"  could not use that page: {info.get('why')}")
    print(f"  captured in {time.time() - t0:.1f}s: {info['target']} {(info.get('quote') or info['text'])[:90]!r}")
    geo = compose_window(png, info, jpg)
    sc = scene_from_capture(info, jpg, geo, a.seconds, a.hit)
    mp4.unlink(missing_ok=True)
    render([(sc, mp4)], workers=1)
    png.unlink(missing_ok=True)
    print(f"  {mp4}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    _cli()
