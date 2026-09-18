#!/usr/bin/env python3
"""docgfx.py — the Documentary look's graphics: quiet, premium scenes that carry real information.

A channel whose style says `"look": {"graphics": "documentary"}` gets these instead of the
motion.py templates. They follow one rule above all: a graphic shows a real fact the voice does
not — a place and a date, a figure and its source, a comparison, a sequence, a documented quote —
or one short line at a real turn of the story. No stickers, icons, clip-art or retyped narration.

Scene types:
    dateline   ATLANTA, GEORGIA — a thin gold rule — April 23, 1985 (over soft-focus footage)
    number     one figure counting up, its unit, what it means, where it comes from
    chart      2-6 bars on warm paper, the one that matters highlighted, the source underneath
    timeline   3-5 dated events on a line the camera travels along
    quote      a documented quote, word by word, with who said it and when
    pivot      one short serif line where the story turns
    question   the question the next part answers, in a gold box

Look: EB Garamond for lines, numbers and quotes; Roboto Condensed for labels (both OFL, bundled
in assets/fonts). Cream ink, one gold accent, blurred real pictures from this video's own footage
behind the words, paper for data, slow eased motion, fine film grain. Colours and fonts come from
the style file's `look` (accent, title_font) and can be changed there.

Try every scene type without making a video (writes preview/docgfx/*.mp4):

    python docgfx.py preview
"""

import base64
import io
import json
import math
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS = HERE / "assets" / "fonts"
W, H, FPS = 1920, 1080, 30
TYPES = ("dateline", "number", "chart", "timeline", "quote", "pivot", "question", "card", "step", "social")
LENGTH = {"dateline": 5.0, "number": 6.0, "chart": 8.0, "timeline": 8.5, "quote": 7.5, "pivot": 4.6, "question": 4.2,
          "card": 5.6, "step": 3.2, "social": 5.2}

SCENES_PROMPT = """You are the graphics designer of a premium documentary YouTube channel — the calm, expensive
look of a streaming documentary. The video is titled "[INSERT TITLE HERE]".

The narration is cut into PARTS below. For each part, design at most ONE graphic that adds real information
at one moment of that part, using one of these types:

- "dateline" — a new place or time begins:
  {"type": "dateline", "place": "ATLANTA, GEORGIA", "date": "April 23, 1985", "note": "Coca-Cola headquarters"}
- "number" — one figure that matters:
  {"type": "number", "value": "200000", "prefix": "", "suffix": "", "unit": "blind taste tests", "context": "before a single can was sold", "source": "Coca-Cola, 1985"}
- "chart" — a comparison with real numbers, 2 to 6 bars:
  {"type": "chart", "title": "U.S. cola market share, 1984", "unit": "%", "bars": [{"label": "Coca-Cola", "value": 21.8, "color": "#E4002B"}, {"label": "Pepsi", "value": 18.6, "color": "#004B93"}], "highlight": 0, "source": "Beverage Digest"}
- "timeline" — 3 to 5 dated events, in order:
  {"type": "timeline", "title": "Seventy-nine days", "events": [{"date": "April 23", "label": "New Coke launches"}, {"date": "July 11", "label": "Coca-Cola Classic returns"}]}
- "quote" — a documented quote, word for word:
  {"type": "quote", "quote": "...", "who": "Roberto Goizueta", "role": "Chairman, Coca-Cola", "year": "1985"}
- "pivot" — the story turns, at most 7 words: {"type": "pivot", "line": "But the numbers were wrong."}
- "question" — the question the next part answers, at most 6 words: {"type": "question", "line": "So what went wrong?"}
- "step" — the title card of one step of a routine, a list or a how-to:
  {"type": "step", "n": "2", "title": "Leave-in conditioner", "note": "on soaking-wet hair"}
- "card" — a practical tip or the exact words to say (to a barber, a doctor, a bank), 1 to 3 phrases highlighted:
  {"type": "card", "kicker": "Tell your barber", "text": "Keep the length and remove bulk with layers, not thinning shears.", "highlight": ["layers", "not thinning shears"]}
- "social" — two people follow or unfollow each other on social media (a split, a new couple, a feud going public):
  {"type": "social", "action": "unfollow", "from": "Lamine Yamal", "to": "Alex Padilla", "mutual": true, "note": "late 2024"}
- {"type": "none"} — nothing real to add here. Fewer, better graphics beat a weak one.

Every graphic also has:
- "part": the part number
- "at": 3 to 8 words copied EXACTLY from that part's narration, where the graphic should appear

RULES
- Real facts only: numbers, dates, places, colours and quotes you are certain of — best, ones the narration
  itself states. When not certain, use a dateline, pivot, question or none instead. Never invent a quote.
- On-screen text adds what the voice does not say — a date, a place, a figure, a source, a name — or it is
  one short line at a real turn. Never retype the narration as a sentence.
- "pivot" and "question" together at most once in every four parts. Never the same type in neighbouring parts.
- A chart or number always has a real source (the organisation or publication, and the year when known).
- A part of a whole ("190 of 200 laps") is a number (value 190, unit "laps led", context "of 200 laps"), never a chart
  of the part against the whole; a chart compares three or more real things.
- A record or a "first" is true for its time: "a race record in 1965", never "fastest ever" unless it still stands.
- "color" only for a real brand, team, party or flag colour you know; otherwise leave it out.
- "value" and bar values are plain digits (a decimal point is allowed, no commas); all words are in [INSERT LANGUAGE HERE].
- Keep text short: place 32 characters, labels 28, note and context 60, title 48, quote 160.

Return ONLY a JSON array, one object per part, in order.
[INSERT EXTRA HERE]
PARTS:
[INSERT CHUNKS HERE]"""


# ── helpers ──────────────────────────────────────────────────────────────────
def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _clean(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


_EVER = re.compile(r"\s*\b(ever|of all time|all[- ]time|in history)\b", re.I)


def of_its_time(sc: dict, title: str) -> dict:
    """A story set decades ago: "the fastest Indy 500 ever run" was true in 1965, not today. The model keeps
    writing it, so a superlative with "ever" on a graphic of an old story is said as true for its time
    (a "first" stays first forever and is left alone)."""
    years = [int(y) for y in re.findall(r"\b(1[5-9]\d\d|20[0-2]\d)\b", str(title or ""))]
    if not years or min(years) > time.localtime().tm_year - 10:
        return sc
    for key in ("context", "unit", "title", "note", "line"):
        v = sc.get(key)
        if isinstance(v, str) and _EVER.search(v) and re.search(r"\b\w{2,}est\b|\b(best|worst|most|least|record)\b", v, re.I) and not re.search(r"\bfirst\b", v, re.I):
            sc[key] = _EVER.sub("", v).strip() + " at the time"
    return sc


def normalise(spec: dict) -> dict:
    """A safe scene from loose model JSON — or None when there is nothing to draw."""
    if not isinstance(spec, dict):
        return None
    t = str(spec.get("type") or "").strip().lower()
    if t not in TYPES:
        return None
    out = {"type": t}
    if t == "dateline":
        out.update(place=_clean(spec.get("place"), 40).upper(), date=_clean(spec.get("date"), 40),
                   note=_clean(spec.get("note"), 70))
        if not (out["place"] or out["date"]):
            return None
    elif t == "number":
        v = _num(spec.get("value"))
        if v is None:
            return None
        out.update(value=v, decimals=len(str(spec.get("value")).split(".")[1]) if "." in str(spec.get("value")) else 0,
                   prefix=_clean(spec.get("prefix"), 4), suffix=_clean(spec.get("suffix"), 6),
                   unit=_clean(spec.get("unit"), 40), context=_clean(spec.get("context"), 80),
                   source=_clean(spec.get("source"), 60))
    elif t == "chart":
        bars = []
        for b in (spec.get("bars") or [])[:6]:
            if not isinstance(b, dict) or _num(b.get("value")) is None:
                continue
            col = str(b.get("color") or "").strip()
            bars.append({"label": _clean(b.get("label"), 30), "value": _num(b.get("value")),
                         "color": col if re.fullmatch(r"#[0-9a-fA-F]{6}", col) else ""})
        if len(bars) < 2 or max(b["value"] for b in bars) <= 0:
            return None
        hl = spec.get("highlight")
        out.update(title=_clean(spec.get("title"), 56), unit=_clean(spec.get("unit"), 12), bars=bars,
                   highlight=int(hl) if isinstance(hl, (int, float)) and 0 <= int(hl) < len(bars) else -1,
                   source=_clean(spec.get("source"), 60))
    elif t == "card":
        text = _clean(spec.get("text"), 170)
        if not text:
            return None
        hl = [h for h in (_clean(x, 48) for x in (spec.get("highlight") or []) if isinstance(x, str))
              if h and h.lower() in text.lower()][:3]
        out.update(kicker=_clean(spec.get("kicker"), 30).upper(), text=text, highlight=hl)
    elif t == "step":
        title = _clean(spec.get("title"), 34).upper()
        if not title:
            return None
        out.update(n=_clean(spec.get("n"), 4), title=title, note=_clean(spec.get("note"), 60))
    elif t == "timeline":
        ev = [{"date": _clean(e.get("date"), 22), "label": _clean(e.get("label"), 60)}
              for e in (spec.get("events") or [])[:5] if isinstance(e, dict) and (e.get("date") or e.get("label"))]
        if len(ev) < 2:
            return None
        out.update(title=_clean(spec.get("title"), 48), events=ev)
    elif t == "social":
        a, b = _clean(spec.get("from"), 28), _clean(spec.get("to"), 28)
        action = str(spec.get("action") or "unfollow").strip().lower()
        if not (a and b) or action not in ("unfollow", "follow"):
            return None
        out.update(action=action, frm=a, to=b, mutual=bool(spec.get("mutual", action == "unfollow")),
                   note=_clean(spec.get("note"), 40), platform=_clean(spec.get("platform"), 16))
    elif t == "quote":
        q = _clean(spec.get("quote"), 170).strip('"“”')
        if len(q.split()) < 3:
            return None
        out.update(quote=q, who=_clean(spec.get("who"), 40), role=_clean(spec.get("role"), 50),
                   year=_clean(spec.get("year"), 12))
    else:
        line = _clean(spec.get("line"), 70)
        if not line:
            return None
        out["line"] = line
    return out


# ── the moment each graphic lands on ──────────────────────────────────────────────
def _moment(entries: list, words: str, lo: float, hi: float) -> float:
    """The start of the subtitle cue where `words` are spoken inside [lo, hi), or -1."""
    want = [w for w in _fold(words).split() if len(w) > 1]
    if not want:
        return -1.0
    best, best_t = 0.0, -1.0
    for i, (st, en, txt) in enumerate(entries):
        if st < lo - 1 or st >= hi:
            continue
        hay = set(_fold(" ".join(e[2] for e in entries[i:i + 2])).split())
        score = sum(1 for w in want if w in hay) / len(want)
        if score > best + 1e-6:
            best, best_t = score, st
    return best_t if best >= 0.5 else -1.0


# ── grounds: this video's own real pictures, blurred ─────────────────────────────
def _pictures(job: Path, total: float) -> list:
    """[(second, path)] — YouTube clips and stock clips placed near the words they were found for,
    then stills (second -1: anywhere)."""
    out = []
    words = 0
    try:
        words = len((job / "script.txt").read_text(encoding="utf-8").split())
    except OSError:
        pass
    clock = (words / 140.0 * 60.0) / total if words and total > 0 else 1.0
    clock = clock if 0.6 <= clock <= 1.6 else 1.0
    try:
        for r in json.loads((job / "youtube" / "clips.json").read_text(encoding="utf-8")):
            if Path(r["path"]).exists():
                out.append((float(r["t"]) / clock, Path(r["path"])))
    except (OSError, ValueError, KeyError):
        pass
    try:
        raw = json.loads((job / "broll_tags.json").read_text(encoding="utf-8"))
        win = float(raw.pop("_win_s", 0) or 0)
        if not win:
            rows = json.loads((job / "broll_queries.json").read_text(encoding="utf-8"))
            win = float((rows[0] if rows else {}).get("win_s") or 15)
        for name, w in raw.items():
            p = job / "pexels" / name
            if p.exists():
                out.append(((int(w) + 0.5) * win / clock, p))
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        pass
    for p in sorted((job / "images").glob("*.jpg")) if (job / "images").is_dir() else []:
        out.append((-1.0, p))
    return out


def _frame(src: Path, tmp: Path) -> Path:
    if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return src
    import subprocess
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True)
    try:
        d = float(p.stdout.strip() or 0)
    except ValueError:
        d = 0.0
    out = tmp / f"{src.stem}_{abs(hash(str(src))) % 99999}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, d * 0.45):.2f}", "-i", str(src),
                    "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", str(out)], capture_output=True)
    return out if out.exists() else None


def ground_uri(src: Path, blur: float, bright: float, tmp: Path, sat: float = 0.85) -> str:
    """A real picture made into a quiet ground: cover-cropped, desaturated a little, darkened, blurred."""
    from PIL import Image, ImageEnhance, ImageFilter
    f = _frame(src, tmp)
    if f is None:
        return ""
    try:
        im = Image.open(f).convert("RGB")
    except OSError:
        return ""
    tw, th = 1280, 720
    s = max(tw / im.width, th / im.height)
    im = im.resize((max(tw, int(im.width * s + 0.5)), max(th, int(im.height * s + 0.5))), Image.LANCZOS)
    x, y = (im.width - tw) // 2, (im.height - th) // 2
    im = im.crop((x, y, x + tw, y + th))
    im = ImageEnhance.Color(im).enhance(sat)
    im = ImageEnhance.Brightness(im).enhance(bright)
    if blur > 0:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ── the page ────────────────────────────────────────────────────────────────────
_FONT_CSS = {"css": None}


def _font_css() -> str:
    if _FONT_CSS["css"] is None:
        faces = [("Doc Serif", "normal", "400 800", "EB-Garamond.ttf"),
                 ("Doc Serif", "italic", "400 800", "EB-Garamond-Italic.ttf"),
                 ("Doc Sans", "normal", "100 900", "Roboto-Condensed.ttf"),
                 ("Doc Sans", "italic", "100 900", "Roboto-Condensed-Italic.ttf")]
        css = []
        for fam, style, wt, name in faces:
            f = FONTS / name
            if f.exists():
                css.append(f"@font-face{{font-family:'{fam}';font-style:{style};font-weight:{wt};"
                           f"src:url(data:font/ttf;base64,{base64.b64encode(f.read_bytes()).decode()});font-display:block}}")
        _FONT_CSS["css"] = "".join(css)
    return _FONT_CSS["css"]


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
__FONTS__
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#0D0C0A}
*{box-sizing:border-box}
#cam{position:absolute;inset:0;overflow:hidden;transform-origin:50% 50%}
#ground{position:absolute;left:-60px;top:-34px;width:2040px;height:1148px;background-size:cover;background-position:center;transform-origin:50% 50%}
#shade,#grain,#vig{position:absolute;inset:0;pointer-events:none}
#grain{opacity:var(--grain);mix-blend-mode:overlay;background-size:512px 512px}
.serif{font-family:__SERIF__}
.sans{font-family:'Doc Sans','Roboto Condensed','Arial Narrow',sans-serif}
.lining{font-variant-numeric:lining-nums tabular-nums;font-feature-settings:"lnum" 1,"tnum" 1}
.ink{color:var(--ink)} .soft{color:var(--soft)} .gold{color:var(--accent)}
.mask{overflow:hidden;display:block;padding-bottom:.08em;margin-bottom:-.08em}
.line{display:block;will-change:transform}
.abs{position:absolute}
</style></head><body><div id="cam"><div id="ground"></div><div id="shade"></div><div id="scene"></div><div id="vig"></div></div><div id="grain"></div>
<script>
const S = __SCENE__;
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const seg=(t,a,b)=>clamp((t-a)/(b-a));
const eo5=x=>1-Math.pow(1-x,5), eo3=x=>1-Math.pow(1-x,3), eio3=x=>x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2;
const $=(h)=>{const d=document.createElement('div');d.innerHTML=h.trim();return d.firstChild};
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const root=document.documentElement.style;
root.setProperty('--ink',S.ink); root.setProperty('--soft',S.soft); root.setProperty('--accent',S.accent);
root.setProperty('--grain',S.paper?'.09':'.08');
const scene=document.getElementById('scene'), ground=document.getElementById('ground'), shade=document.getElementById('shade');
const D=S.duration, OUT=Math.max(0.2,D-0.5);
let parts={};
function fmt(v,dec){const p=Number(v).toFixed(dec).split('.');p[0]=p[0].replace(/\B(?=(\d{3})+(?!\d))/g,',');return p.join('.')}

// film grain: one noise tile, moved every other frame
(function(){const c=document.createElement('canvas');c.width=c.height=512;const x=c.getContext('2d');const im=x.createImageData(512,512);
 let s=1234567;for(let i=0;i<im.data.length;i+=4){s=(s*16807)%2147483647;const v=(s&255);im.data[i]=im.data[i+1]=im.data[i+2]=v;im.data[i+3]=255}
 x.putImageData(im,0,0);document.getElementById('grain').style.backgroundImage=`url(${c.toDataURL()})`})();

function lines(el){   // wrap each rendered line of words in a mask, for the line-by-line rise
  const words=el.textContent.split(/\s+/).filter(Boolean); el.innerHTML=words.map(w=>`<span class="w">${esc(w)} </span>`).join('');
  const rows=[];let top=null;for(const sp of el.querySelectorAll('.w')){if(top===null||Math.abs(sp.offsetTop-top)>4){rows.push([]);top=sp.offsetTop}rows[rows.length-1].push(sp.textContent)}
  el.innerHTML=rows.map(r=>`<span class="mask"><span class="line">${esc(r.join('').trim())}</span></span>`).join('');
  return [...el.querySelectorAll('.line')];
}

function build(){
  if(S.ground){ground.style.backgroundImage=`url(${S.ground})`}
  else if(S.paper){ground.style.background='radial-gradient(ellipse at 45% 38%,#F1EBDD 0%,#E7DFCE 55%,#D8CDB7 100%)'}
  else {ground.style.background='radial-gradient(ellipse at 38% 30%,#221E19 0%,#15130F 45%,#0A0907 100%)'}
  document.getElementById('vig').style.background=S.paper?'radial-gradient(ellipse at 50% 45%,rgba(0,0,0,0) 55%,rgba(60,40,20,.20) 100%)':'radial-gradient(ellipse at 50% 45%,rgba(0,0,0,0) 50%,rgba(0,0,0,.42) 100%)';
  const t=S.type;
  if(t==='pivot'){
    shade.style.background='linear-gradient(90deg,rgba(8,7,6,.66) 0%,rgba(8,7,6,.30) 55%,rgba(8,7,6,.08) 100%)';
    scene.innerHTML=`<div class="abs" style="left:132px;top:360px;width:1180px">
      <div id="rule" style="height:3px;background:var(--accent);width:0;margin-bottom:44px"></div>
      <div id="txt" class="serif ink" style="font-size:104px;line-height:1.06;font-weight:470;letter-spacing:-.014em">${esc(S.line)}</div></div>`;
    parts.lines=lines(document.getElementById('txt')); parts.rule=document.getElementById('rule');
  }
  if(t==='question'){
    shade.style.background='linear-gradient(90deg,rgba(8,7,6,.5) 0%,rgba(8,7,6,.22) 70%,rgba(8,7,6,.12) 100%)';
    scene.innerHTML=`<div class="abs" style="left:132px;top:0;height:1080px;display:flex;align-items:center">
      <div id="box" class="sans" style="background:var(--accent);color:#16120D;font-weight:700;font-size:76px;line-height:1.02;padding:20px 34px 26px;letter-spacing:-.005em;max-width:1300px">${esc(S.line)}</div></div>`;
    parts.box=document.getElementById('box');
  }
  if(t==='card'){
    shade.style.background='rgba(8,7,6,.22)';
    let html=esc(S.text), i=0;
    (S.highlight||[]).forEach(h=>{const k=html.toLowerCase().indexOf(esc(h).toLowerCase());
      if(k>=0){html=html.slice(0,k)+`<span class="hl" data-i="${i++}">`+html.slice(k,k+esc(h).length)+'</span>'+html.slice(k+esc(h).length)}});
    scene.innerHTML=`<style>.hl{background-image:linear-gradient(transparent 14%,#FFE14D 14%,#FFE14D 90%,transparent 90%);background-repeat:no-repeat;background-size:0% 100%;padding:0 .08em;margin:0 -.08em}</style>
      <div class="abs" style="left:0;top:0;width:1920px;height:1080px;display:flex;align-items:center;justify-content:center">
      <div id="card" style="background:#FFFFFF;border-radius:28px;padding:46px 60px 52px;max-width:1240px;box-shadow:0 30px 80px rgba(0,0,0,.35)">
        ${S.kicker?`<div class="sans" style="font-size:30px;font-weight:700;letter-spacing:.16em;color:#7A7A7A;margin-bottom:18px">${esc(S.kicker)}</div>`:''}
        <div class="sans" style="font-size:60px;line-height:1.2;font-weight:600;color:#141414">&ldquo;${html}&rdquo;</div></div></div>`;
    parts.card=document.getElementById('card'); parts.hl=[...document.querySelectorAll('.hl')];
  }
  if(t==='step'){
    ground.style.background='#5E6064'; ground.style.backgroundImage='none';
    document.getElementById('vig').style.background='radial-gradient(ellipse at 50% 45%,rgba(0,0,0,0) 60%,rgba(0,0,0,.18) 100%)';
    scene.innerHTML=`<div class="abs" style="left:0;top:0;width:1920px;height:1080px;display:flex;flex-direction:column;align-items:center;justify-content:center">
      <div id="kick" class="sans" style="font-size:34px;font-weight:700;letter-spacing:.2em;color:var(--accent);margin-bottom:14px">${S.n?'STEP '+esc(S.n):''}</div>
      <div id="ttl" class="sans" style="font-size:66px;font-weight:700;letter-spacing:.02em;color:#FFFFFF;margin-bottom:34px;text-align:center;max-width:1500px">${esc(S.title)}</div>
      <div id="pic" style="width:380px;height:380px;border-radius:22px;background:#3E4044 center/cover no-repeat;box-shadow:0 22px 50px rgba(0,0,0,.35)${S.ground?`;background-image:url(${S.ground})`:''}"></div>
      ${S.note?`<div id="note" class="sans" style="font-size:36px;color:rgba(255,255,255,.82);margin-top:30px">${esc(S.note)}</div>`:'<div id="note"></div>'}</div>`;
    parts.kick=document.getElementById('kick'); parts.ttl=document.getElementById('ttl'); parts.pic=document.getElementById('pic'); parts.note=document.getElementById('note');
  }
  if(t==='social'){
    shade.style.background='rgba(8,7,6,.52)';
    const ini=n=>esc(String(n||'').split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase());
    const on=S.action==='unfollow';           // an unfollow starts as "Following" and ends as "Follow"
    const card=(name,x,i)=>`<div class="sc abs" data-i="${i}" style="left:${x}px;top:372px;width:700px;height:270px;background:#FFFFFF;border-radius:34px;box-shadow:0 34px 90px rgba(0,0,0,.42);display:flex;align-items:center;padding:0 44px;gap:32px;opacity:0">
        <div style="flex:none;width:150px;height:150px;border-radius:50%;padding:6px;background:conic-gradient(from 210deg,#FEDA75,#FA7E1E,#D62976,#962FBF,#4F5BD5,#FEDA75)">
          <div class="sans" style="width:100%;height:100%;border-radius:50%;border:5px solid #fff;background:linear-gradient(135deg,#2B2B2B,#5A5A5A);color:#fff;display:flex;align-items:center;justify-content:center;font-size:56px;font-weight:700">${ini(name)}</div></div>
        <div style="flex:1;min-width:0">
          <div class="sans" style="font-size:50px;font-weight:700;color:#121212;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(name)}</div>
          <div class="btn sans" data-i="${i}" style="margin-top:20px;display:inline-flex;align-items:center;justify-content:center;width:270px;height:74px;border-radius:18px;font-size:36px;font-weight:700;background:${on?'#EFEFEF':'#0095F6'};color:${on?'#121212':'#FFFFFF'}">${on?'Following':'Follow'}</div></div></div>`;
    scene.innerHTML=`${card(S.frm,200,0)}${card(S.to,1020,1)}
      <svg class="abs" style="left:0;top:0" width="1920" height="1080">
        <line id="tieL" x1="900" y1="507" x2="900" y2="507" stroke="#FFFFFF" stroke-width="4" stroke-dasharray="12 10" stroke-linecap="round"/>
        <line id="tieR" x1="1020" y1="507" x2="1020" y2="507" stroke="#FFFFFF" stroke-width="4" stroke-dasharray="12 10" stroke-linecap="round"/>
        <g id="heart" transform="translate(960 507)">
          <g id="hl"><path d="M0,26 C-8,18 -40,-2 -40,-22 C-40,-36 -30,-46 -18,-46 C-9,-46 -3,-41 0,-35 L0,26 Z" fill="#FF3040"/></g>
          <g id="hr"><path d="M0,26 C8,18 40,-2 40,-22 C40,-36 30,-46 18,-46 C9,-46 3,-41 0,-35 L0,26 Z" fill="#FF3040"/></g></g>
        <g id="cursor" opacity="0"><path d="M0,0 L0,46 L12,35 L21,56 L30,52 L21,32 L37,32 Z" fill="#FFFFFF" stroke="#121212" stroke-width="3" stroke-linejoin="round"/></g>
        <circle id="ripple" cx="0" cy="0" r="0" fill="none" stroke="#FFFFFF" stroke-width="4" opacity="0"/>
      </svg>
      <div id="snote" class="abs serif ink" style="left:0;width:1920px;top:704px;text-align:center;font-size:58px;font-style:italic;opacity:0">${esc([S.note,S.platform].filter(Boolean).join(' · '))}</div>`;
    parts.cards=[...document.querySelectorAll('.sc')]; parts.btns=[...document.querySelectorAll('.btn')];
    parts.tieL=document.getElementById('tieL'); parts.tieR=document.getElementById('tieR');
    parts.heart=document.getElementById('heart'); parts.hl=document.getElementById('hl'); parts.hr=document.getElementById('hr');
    parts.cursor=document.getElementById('cursor'); parts.ripple=document.getElementById('ripple'); parts.note=document.getElementById('snote');
    // where the clicks land: the button of each card, measured once the fonts are in
    parts.btnXY=parts.btns.map(b=>{const r=b.getBoundingClientRect();return [r.left+r.width*0.5,r.top+r.height*0.55]});
  }
  if(t==='dateline'){
    shade.style.background='linear-gradient(0deg,rgba(8,7,6,.8) 0%,rgba(8,7,6,.34) 42%,rgba(8,7,6,0) 100%)';
    const place=[...S.place].map(c=>`<span class="ch" style="opacity:0">${esc(c)}</span>`).join('');
    scene.innerHTML=`<div class="abs" style="left:132px;bottom:168px;width:1500px">
      <div id="place" class="sans ink" style="font-size:42px;font-weight:500;letter-spacing:.24em;white-space:nowrap">${place}</div>
      <div id="rule" style="height:2px;background:var(--accent);width:0;margin:26px 0 30px"></div>
      <div id="date" class="serif ink" style="font-size:72px;font-style:italic;font-weight:420;line-height:1">${esc(S.date)}</div>
      <div id="note" class="sans soft" style="font-size:28px;letter-spacing:.06em;margin-top:22px">${esc(S.note)}</div></div>`;
    parts.chars=[...document.querySelectorAll('#place .ch')]; parts.rule=document.getElementById('rule');
    parts.date=document.getElementById('date'); parts.note=document.getElementById('note');
    parts.ruleW=Math.min(640,Math.max(220,document.getElementById('place').scrollWidth*0.62));
  }
  if(t==='number'){
    shade.style.background=S.ground?'linear-gradient(90deg,rgba(8,7,6,.74) 0%,rgba(8,7,6,.42) 60%,rgba(8,7,6,.22) 100%)':'none';
    scene.innerHTML=`<div class="abs" style="left:132px;top:0;height:1080px;display:flex;flex-direction:column;justify-content:center;width:1500px">
      <div id="unit" class="sans gold" style="font-size:40px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;margin-bottom:6px">${esc(S.unit)}</div>
      <div id="val" class="serif ink lining" style="font-size:250px;line-height:.92;font-weight:460;letter-spacing:-.02em;white-space:nowrap">${esc(S.prefix)}<span id="n">0</span>${esc(S.suffix)}</div>
      <div id="ctx" class="serif soft" style="font-size:52px;font-style:italic;line-height:1.15;margin-top:26px;max-width:1200px">${esc(S.context)}</div></div>
      <div id="src" class="abs sans" style="left:132px;bottom:92px;display:flex;align-items:center;gap:18px;color:var(--soft);font-size:23px;letter-spacing:.16em;text-transform:uppercase">
      <span style="display:inline-block;width:46px;height:1.5px;background:var(--soft)"></span>${S.source?'Source: '+esc(S.source):''}</div>`;
    parts.n=document.getElementById('n'); parts.unit=document.getElementById('unit'); parts.ctx=document.getElementById('ctx');
    parts.src=document.getElementById('src'); parts.val=document.getElementById('val');
    if(!S.source) parts.src.style.display='none';
  }
  if(t==='chart'){
    const n=S.bars.length, max=Math.max(...S.bars.map(b=>b.value)), areaH=560, rowH=Math.min(n<=3?170:128,areaH/n);
    const top=Math.round(560-rowH*n/2), U=(/^[A-Za-z]/.test(S.unit)?' ':'')+S.unit, big=n<=3;
    parts.U=U;
    const nice=(()=>{const raw=max/4, p=Math.pow(10,Math.floor(Math.log10(raw))), m=raw/p;const st=(m<=1?1:m<=2?2:m<=2.5?2.5:m<=5?5:10)*p;return st})();
    const top_v=Math.ceil(max/nice)*nice, x0=620, len=(big?960:1080);
    let grid='';for(let v=0;v<=top_v+1e-9;v+=nice){const x=x0+len*v/top_v;
      grid+=`<div class="gl abs" style="left:${x}px;top:${top-24}px;height:${rowH*n+30}px;border-left:1.5px ${v?'dashed':'solid'} rgba(38,33,28,${v?'.16':'.45'})"></div>
      <div class="gl abs sans" style="left:${x-90}px;width:180px;text-align:center;top:${top+rowH*n+18}px;font-size:26px;color:#857B6F;letter-spacing:.04em">${fmt(v,v%1?1:0)}${v+nice>top_v+1e-9?esc(U):''}</div>`}
    let rows='';S.bars.forEach((b,i)=>{const y=top+i*rowH, bh=Math.round(rowH*(big?.56:.5)), hi=(S.highlight===i);
      const col=b.color||(hi?'#B8472F':'#8E857A');
      rows+=`<div class="abs serif" style="left:110px;width:480px;top:${y}px;height:${rowH}px;display:flex;align-items:center;justify-content:flex-end;text-align:right;line-height:1.05;font-size:${big?50:40}px;color:#231E19;font-weight:${hi?600:450}">${esc(b.label)}</div>
      <div class="bar abs" data-i="${i}" style="left:${x0}px;top:${y+(rowH-bh)/2}px;height:${bh}px;width:0;background:linear-gradient(180deg,${col} 0%,${col} 100%);box-shadow:inset 0 1px 0 rgba(255,255,255,.18)"></div>
      <div class="val abs sans lining" data-i="${i}" style="left:${x0}px;top:${y}px;height:${rowH}px;display:flex;align-items:center;font-size:${big?50:38}px;font-weight:700;color:${hi?'#B8472F':'#3A332C'};white-space:nowrap">0</div>`});
    scene.innerHTML=`<div class="abs" style="left:150px;top:118px">
       <div id="title" class="sans" style="font-size:54px;font-weight:700;font-style:italic;letter-spacing:.03em;text-transform:uppercase;color:#231E19">${esc(S.title)}</div>
       <div id="trule" style="height:2px;background:#B8472F;width:0;margin-top:18px"></div></div>
       ${grid}${rows}
       <div id="src" class="abs sans" style="left:150px;bottom:74px;font-size:23px;letter-spacing:.16em;text-transform:uppercase;color:#857B6F">${S.source?'Source: '+esc(S.source):''}</div>`;
    parts.bars=[...document.querySelectorAll('.bar')]; parts.vals=[...document.querySelectorAll('.val')];
    parts.grid=[...document.querySelectorAll('.gl')]; parts.title=document.getElementById('title');
    parts.trule=document.getElementById('trule'); parts.src=document.getElementById('src'); parts.len=len; parts.top_v=top_v;
    parts.dec=Math.max(...S.bars.map(b=>(String(b.value).split('.')[1]||'').length));
  }
  if(t==='quote'){
    shade.style.background='linear-gradient(90deg,rgba(8,7,6,.76) 0%,rgba(8,7,6,.48) 60%,rgba(8,7,6,.3) 100%)';
    const words=S.quote.split(/\s+/).map(w=>`<span class="qw" style="opacity:0">${esc(w)} </span>`).join('');
    scene.innerHTML=`<div id="mark" class="abs serif gold" style="left:112px;top:118px;font-size:300px;line-height:1;opacity:0">“</div>
      <div class="abs" style="left:172px;top:300px;width:1380px">
      <div class="serif ink" style="font-size:${S.quote.length<=24?128:S.quote.length<=60?92:64}px;font-style:italic;line-height:1.14;font-weight:440">${words}</div>
      <div id="who" style="margin-top:48px;display:flex;align-items:center;gap:22px;opacity:0">
        <span style="display:inline-block;width:64px;height:2px;background:var(--accent)"></span>
        <span class="sans ink" style="font-size:30px;font-weight:600;letter-spacing:.18em;text-transform:uppercase">${esc(S.who)}</span>
        <span class="serif soft" style="font-size:32px;font-style:italic">${esc([S.role,S.year].filter(Boolean).join(', '))}</span></div></div>`;
    parts.words=[...document.querySelectorAll('.qw')]; parts.mark=document.getElementById('mark'); parts.who=document.getElementById('who');
  }
  if(t==='timeline'){
    const n=S.events.length, gap=Math.max(470,Math.min(640,1500/(n-1||1))), x0=260, y=610;
    parts.world=x0+gap*(n-1)+Math.min(gap-70,520)+150; parts.gap=gap; parts.x0=x0;
    let ev='';S.events.forEach((e,i)=>{const x=x0+i*gap;
      ev+=`<div class="ev abs" data-i="${i}" style="left:${x}px;top:0;width:0">
        <div class="dot abs" style="left:-9px;top:${y-9}px;width:18px;height:18px;border-radius:50%;background:var(--accent);transform:scale(0)"></div>
        <div class="dt abs sans ink lining" style="left:-2px;top:${y-118}px;font-size:50px;font-weight:600;letter-spacing:.06em;white-space:nowrap;opacity:0">${esc(e.date)}</div>
        <div class="lb abs serif soft" style="left:-2px;top:${y+44}px;width:${Math.min(gap-70,520)}px;font-size:42px;line-height:1.18;opacity:0">${esc(e.label)}</div></div>`});
    scene.innerHTML=`<div id="title" class="abs sans gold" style="left:132px;top:120px;font-size:34px;font-weight:600;letter-spacing:.24em;text-transform:uppercase;z-index:3">${esc(S.title)}</div>
      <div id="world" class="abs" style="left:0;top:0;height:1080px;width:${parts.world}px">
      <div id="track" class="abs" style="left:${x0-120}px;top:${y-1}px;height:2px;width:0;background:rgba(243,238,228,.34)"></div>${ev}</div>`;
    parts.evs=[...document.querySelectorAll('.ev')]; parts.track=document.getElementById('track');
    parts.worldEl=document.getElementById('world'); parts.title=document.getElementById('title'); parts.y=y;
  }
}

window.renderFrame=function(t){
  const out=1-eio3(seg(t,OUT,D));           // words leave in the last half second; the picture stays
  const push=S.paper?1+0.022*seg(t,0,D):1.0+0.055*eio3(seg(t,0,D));
  ground.style.transform=`scale(${push})`;
  const g=document.getElementById('grain'); const k=Math.floor(t*15); g.style.backgroundPosition=`${(k*197)%512}px ${(k*331)%512}px`;
  const ty=S.type;
  if(ty==='pivot'){
    parts.rule.style.width=(96*eo5(seg(t,.15,.85)))+'px'; parts.rule.style.opacity=out;
    parts.lines.forEach((l,i)=>{const p=eo5(seg(t,.3+i*.17,1.25+i*.17));l.style.transform=`translateY(${(1-p)*105}%)`;l.style.opacity=out});
  }
  if(ty==='question'){
    const p=eio3(seg(t,.2,.85)); parts.box.style.clipPath=`inset(0 ${(1-p)*100}% 0 0)`; parts.box.style.opacity=out;
  }
  if(ty==='card'){
    const p=eo5(seg(t,.12,.75)); parts.card.style.opacity=p*out; parts.card.style.transform=`translateY(${(1-p)*28}px) scale(${.965+.035*p})`;
    parts.hl.forEach((h,i)=>{h.style.backgroundSize=`${100*eo3(seg(t,.95+i*.45,1.55+i*.45))}% 100%`});
  }
  if(ty==='step'){
    const pk=eo3(seg(t,.05,.5)); parts.kick.style.opacity=pk*out;
    const pt=eo5(seg(t,.15,.7)); parts.ttl.style.opacity=pt*out; parts.ttl.style.transform=`translateY(${(1-pt)*18}px)`;
    const pp=eo5(seg(t,.3,.95)); parts.pic.style.opacity=pp*out; parts.pic.style.transform=`scale(${.9+.1*pp+.03*eio3(seg(t,.95,D))})`;
    parts.note.style.opacity=eo3(seg(t,.8,1.4))*out;
  }
  if(ty==='social'){
    const unf=S.action==='unfollow', clicks=S.mutual?[1.05,1.75]:[1.05];
    parts.cards.forEach((c,i)=>{const p=eo5(seg(t,.05+i*.12,.6+i*.12));c.style.opacity=p*out;c.style.transform=`translateY(${(1-p)*40}px)`});
    // the tie between the two: drawn in, then (unfollow) snapped apart after the last click
    const tie=eo5(seg(t,.35,.85)), last=clicks[clicks.length-1]+.25, cut=unf?eio3(seg(t,last,last+.6)):0, join=unf?1:eo5(seg(t,last,last+.5));
    const reach=60*(unf?tie*(1-cut):join);
    parts.tieL.setAttribute('x2',900+reach); parts.tieR.setAttribute('x2',1020-reach);
    parts.tieL.style.opacity=parts.tieR.style.opacity=out*(unf?1:join);
    const hs=unf?eo5(seg(t,.55,.95)):eo5(seg(t,last+.1,last+.6)); const pop=1+.18*Math.sin(Math.PI*seg(t,unf?.55:last+.1,unf?.95:last+.6));
    parts.heart.setAttribute('transform',`translate(960 507) scale(${1.15*hs*pop})`);
    const split=unf?eo3(seg(t,last,last+.7)):0;
    parts.hl.setAttribute('transform',`translate(${-34*split} ${22*split}) rotate(${-24*split})`);
    parts.hr.setAttribute('transform',`translate(${34*split} ${22*split}) rotate(${24*split})`);
    parts.heart.style.opacity=out*(1-.85*seg(t,last+.5,last+1.2));
    // the cursor travels to each button and clicks it
    const pts=[[1500,980]].concat(parts.btnXY.slice(0,clicks.length)); let cx=pts[0][0], cy=pts[0][1];
    clicks.forEach((c,i)=>{const p=eio3(seg(t,c-.55,c-.05)); cx=cx+(pts[i+1][0]-cx)*p; cy=cy+(pts[i+1][1]-cy)*p});
    const press=clicks.some(c=>t>=c&&t<c+.12)?.9:1;
    parts.cursor.setAttribute('transform',`translate(${cx} ${cy}) scale(${press})`);
    parts.cursor.style.opacity=eo3(seg(t,.45,.7))*(1-seg(t,last+.3,last+.7))*out;
    const cur=clicks.filter(c=>t>=c).pop();
    if(cur!==undefined){const k=seg(t,cur,cur+.45);parts.ripple.setAttribute('cx',cx);parts.ripple.setAttribute('cy',cy);
      parts.ripple.setAttribute('r',8+46*eo3(k));parts.ripple.style.opacity=(1-k)*out}
    parts.btns.forEach((b,i)=>{const c=clicks[i]; if(c===undefined) return; const done=t>=c;
      const now=unf?!done:done;             // "Following" while they still follow each other
      b.textContent=now?'Following':'Follow'; b.style.background=now?'#EFEFEF':'#0095F6'; b.style.color=now?'#121212':'#FFFFFF';
      const k=seg(t,c,c+.3); b.style.transform=`scale(${1+.1*Math.sin(Math.PI*k)})`});
    const pn=eo5(seg(t,last+.35,last+1.0)); parts.note.style.opacity=pn*out; parts.note.style.transform=`translateY(${(1-pn)*16}px)`;
  }
  if(ty==='dateline'){
    const nC=parts.chars.length; parts.chars.forEach((c,i)=>{c.style.opacity=seg(t,.25+i*.035,.33+i*.035)*out});
    parts.rule.style.width=(parts.ruleW*eo5(seg(t,.55,1.45)))+'px'; parts.rule.style.opacity=out;
    const p=eo5(seg(t,.95,1.85)); parts.date.style.opacity=p*out; parts.date.style.transform=`translateY(${(1-p)*16}px)`;
    parts.note.style.opacity=eo3(seg(t,1.5,2.3))*out;
  }
  if(ty==='number'){
    const c=eo3(seg(t,.35,2.2)); parts.n.textContent=fmt(S.value*c,S.decimals);
    const pv=eo5(seg(t,.1,.9)); parts.val.style.opacity=pv*out; parts.val.style.transform=`translateY(${(1-pv)*18}px)`;
    parts.unit.style.opacity=eo3(seg(t,.5,1.2))*out;
    const pc=eo5(seg(t,1.5,2.4)); parts.ctx.style.opacity=pc*out; parts.ctx.style.transform=`translateY(${(1-pc)*14}px)`;
    parts.src.style.opacity=eo3(seg(t,2.1,2.9))*out;
  }
  if(ty==='chart'){
    document.getElementById('cam').style.transform=`scale(${1+0.018*eio3(seg(t,0,D))})`;
    parts.title.style.opacity=eo3(seg(t,.1,.7))*out; parts.trule.style.width=(160*eo5(seg(t,.3,1.1)))+'px'; parts.trule.style.opacity=out;
    parts.grid.forEach(g=>g.style.opacity=eo3(seg(t,.4,1.1))*out);
    S.bars.forEach((b,i)=>{const p=eo5(seg(t,.9+i*.14,2.5+i*.14)); const w=parts.len*b.value/parts.top_v*p;
      parts.bars[i].style.width=w+'px'; parts.bars[i].style.opacity=out;
      const v=parts.vals[i]; v.style.left=(620+w+20)+'px'; v.textContent=fmt(b.value*p,parts.dec)+parts.U;
      const land=seg(t,2.5+i*.14,2.9+i*.14); v.style.opacity=clamp(p*3)*out;
      v.style.transform=(S.highlight===i)?`scale(${1+0.08*Math.sin(Math.PI*land)})`:'none'; v.style.transformOrigin='0 50%'});
    parts.src.style.opacity=eo3(seg(t,2.6,3.4))*out;
  }
  if(ty==='quote'){
    parts.mark.style.opacity=eo3(seg(t,.1,.8))*out;
    const n=parts.words.length, span=Math.min(3.4,Math.max(1.4,n*.16));
    parts.words.forEach((w,i)=>{w.style.opacity=eo3(seg(t,.45+span*i/n,.45+span*i/n+.45))*out});
    const pw=eo5(seg(t,.6+span,1.4+span)); parts.who.style.opacity=pw*out; parts.who.style.transform=`translateY(${(1-pw)*12}px)`;
  }
  if(ty==='timeline'){
    // events arrive one after another; the camera travels so the newest one stays in view
    const n=S.events.length, over=Math.max(0,parts.world-1920), step=(D-2.6)/Math.max(1,n-1);
    const cx=over*eio3(seg(t,.5,.5+step*(n-1)+.6));
    parts.worldEl.style.transform=`translateX(${-cx}px)`; parts.title.style.opacity=eo3(seg(t,.1,.8))*out;
    const reachX=parts.x0+parts.gap*(n-1)*eio3(seg(t,.3,.45+step*(n-1)))+120;
    parts.track.style.width=Math.max(0,reachX-(parts.x0-120))+'px'; parts.track.style.opacity=out;
    parts.evs.forEach((e,i)=>{const p=eo5(seg(t,.45+i*step,1.25+i*step));
      const edge=clamp((parts.x0+i*parts.gap-cx-60)/220);        // an event leaving the frame fades, never half cut
      e.querySelector('.dot').style.transform=`scale(${p})`; e.querySelector('.dot').style.opacity=out*edge;
      const dt=e.querySelector('.dt'), lb=e.querySelector('.lb');
      dt.style.opacity=p*out*edge; dt.style.transform=`translateY(${(1-p)*14}px)`; lb.style.opacity=eo3(seg(t,.7+i*step,1.5+i*step))*out*edge});
  }
};
document.fonts.ready.then(()=>{build();window.renderFrame(0);window.__ready=true});
</script></body></html>"""


def build_html(scene: dict, look: dict = None) -> str:
    look = look or {}
    acc = look.get("accent") or ["#E3B25B"]
    serif = str(look.get("title_font") or "")
    serif_css = (f"{serif},'Doc Serif',Georgia,serif" if serif and "garamond" not in serif.lower()
                 and "newsreader" not in serif.lower() else "'Doc Serif','EB Garamond',Georgia,serif")
    sc = dict(scene)
    sc.setdefault("ink", "#F3EEE4")
    sc.setdefault("soft", "rgba(243,238,228,.72)")
    sc.setdefault("accent", acc[0] if isinstance(acc, list) and acc else "#E3B25B")
    sc["paper"] = scene.get("type") == "chart"
    return (PAGE.replace("__FONTS__", _font_css()).replace("__SERIF__", serif_css)
            .replace("__SCENE__", json.dumps(sc, ensure_ascii=False)))


def render(jobs: list, workers: int = 2, look: dict = None) -> list:
    """jobs = [(scene, out_mp4)] — Chromium, one screenshot per frame, the same loop every Frontier scene uses."""
    import motion
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="docgfx_"))
    prepared = [(build_html(sc, look), float(sc.get("duration", 6.0)), Path(out), tmp / f"f{i:03d}")
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
                    for k in range(max(1, int(round(dur * FPS)))):
                        page.evaluate("(t)=>window.renderFrame(t)", k / FPS)
                        page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"), type="jpeg", quality=94,
                                        clip={"x": 0, "y": 0, "width": W, "height": H})
                    motion._frames_to_mp4(fdir, out, FPS)
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
        raise RuntimeError(f"docgfx: rendering failed: {last}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, [prepared[i::workers] for i in range(workers)]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [Path(o) for _, o in jobs]


def sound_marks(sc: dict) -> list:
    """[(second, sound, gain dB)] — where sfx.py puts its sounds under this graphic, from its own timing."""
    t, d = sc["type"], float(sc.get("duration", 6.0))
    if t == "number":
        return [(0.0, "whoosh", -3.0), (2.2, "hit", -5.0)]
    if t == "chart":
        return [(0.0, "whoosh", -3.0), (2.5, "hit", -9.0)]
    if t == "dateline":
        return [(0.0, "whoosh", -3.0), (0.95, "hit", -7.0)]
    if t == "question":
        return [(0.2, "riser", -6.0), (0.22, "hit", -4.0)]
    if t == "pivot":
        return [(0.0, "whoosh", -4.0), (0.35, "hit", -7.0)]
    if t == "card":
        return [(0.1, "whoosh", -5.0)] + [(round(0.95 + i * 0.45, 2), "tap", -8.0) for i in range(len(sc.get("highlight") or []))]
    if t == "step":
        return [(0.0, "whoosh", -4.0), (0.3, "hit", -8.0)]
    if t == "social":
        clicks = [1.05, 1.75] if sc.get("mutual") else [1.05]
        return [(0.05, "whoosh", -5.0)] + [(c, "tap", -5.0) for c in clicks] + [(clicks[-1] + 0.3, "hit", -9.0)]
    if t == "timeline":
        n = len(sc.get("events") or [])
        step = (d - 2.6) / max(1, n - 1)
        return [(0.0, "whoosh", -3.0)] + [(round(0.45 + i * step, 2), "tap", -6.0) for i in range(n)]
    return [(0.0, "whoosh", -6.0)]


# ── the whole step ─────────────────────────────────────────────────────────────
def design(engine, srt: Path, job: Path, total: float, force: bool, style: str, step_s: float,
           title: str = "") -> list:
    """Claude's design for every part (motion/doc_scenes.json). Needs only the subtitles, so the
    engine can run it while footage is still being found."""
    log = engine.log
    mo_dir = job / "motion"
    mo_dir.mkdir(exist_ok=True)
    entries = engine._parse_srt_full(srt.read_text(encoding="utf-8"))
    n = max(1, int(math.ceil(total / step_s)))
    spec_file = mo_dir / "doc_scenes.json"
    if spec_file.exists() and not force:
        try:
            specs = json.loads(spec_file.read_text(encoding="utf-8"))
            if isinstance(specs, dict) or len(specs) >= n:      # a dict is the director's plan (director.py)
                return specs
        except ValueError:
            pass
    chunks = ["" for _ in range(n)]
    for a, b, txt in entries:
        i = int(a // step_s)
        if 0 <= i < n:
            chunks[i] += " " + txt
    if not title and (job / "title.txt").exists():
        title = (job / "title.txt").read_text(encoding="utf-8").splitlines()[0]
    body = "\n\n".join(f"PART {i + 1} ({int(i * step_s) // 60}:{int(i * step_s) % 60:02d}):\n"
                        f"{(chunks[i].strip() or '(quiet)')[:1500]}" for i in range(n))
    log(f"Claude: designing documentary graphics for {n} parts...")
    try:
        specs = engine._json_items(SCENES_PROMPT.replace("[INSERT TITLE HERE]", title or "")
                                   .replace("[INSERT LANGUAGE HERE]", engine._job_language(job, style))
                                   .replace("[INSERT EXTRA HERE]", engine._extra_block())
                                   .replace("[INSERT CHUNKS HERE]", body), max_tokens=max(4000, n * 260))
    except SystemExit as e:
        log(f"docgfx: Claude's answer did not parse — no graphics this time ({str(e)[:100]})")
        return []
    spec_file.write_text(json.dumps(specs, indent=1, ensure_ascii=False), encoding="utf-8")
    return specs


def generate(engine, srt: Path, job: Path, total: float, force: bool, style: str, step_s: float,
             seg_dur: float, title: str = "") -> list:
    """[(start, path, dur)] — the documentary graphics for one video, each on the words it belongs to."""
    log = engine.log
    st = engine.STYLE_INFO.get(style) or {}
    look = st.get("look") or {}
    max_s = float((st.get("pacing") or {}).get("graphic_max_s") or 9.0)
    if not title and (job / "title.txt").exists():
        title = (job / "title.txt").read_text(encoding="utf-8").splitlines()[0].strip()
    mo_dir = job / "motion"
    mo_dir.mkdir(exist_ok=True)
    entries = engine._parse_srt_full(srt.read_text(encoding="utf-8"))
    n = max(1, int(math.ceil(total / step_s)))
    specs = design(engine, srt, job, total, force, style, step_s, title)
    if not specs:
        return []

    placed, last_end = [], -3.0
    if isinstance(specs, dict):
        # the director placed every graphic on its words already (director.py): keep its timing
        for raw in specs.get("items") or []:
            sc = normalise(raw)
            if sc is None:
                continue
            sc = of_its_time(sc, title)
            dur = min(max_s, LENGTH[sc["type"]])
            if sc["type"] == "quote" and len(sc.get("quote") or "") <= 40:
                dur = 4.6
            if raw.get("dur"):
                dur = min(max_s, max(3.0, float(raw["dur"])))
            at = max(float(raw.get("at_s") or 0) - 0.15, last_end + 1.0, 0.0)
            if at + dur > total - 0.5:
                continue
            sc["duration"] = dur
            placed.append((at, sc))
            last_end = at + dur
    else:
        # one graphic per part, on the words it names
        stream = engine._spoken_stream(entries) if hasattr(engine, "_spoken_stream") else None
        for i, raw in enumerate(specs[:n]):
            sc = normalise(raw)
            if sc is None:
                continue
            sc = of_its_time(sc, title)
            part = int(raw.get("part") or (i + 1)) - 1 if str(raw.get("part") or "").isdigit() else i
            lo, hi = part * step_s, (part + 1) * step_s
            at = engine._find_line(stream, raw.get("at") or "", lo, lo, hi) if stream else None
            at = at if at is not None else _moment(entries, raw.get("at") or "", lo, hi)
            at = at if at is not None and at >= 0 else lo + step_s * 0.35
            dur = min(max_s, LENGTH[sc["type"]])
            at = max(at - 0.15, last_end + 3.0, 0.0)
            if at + dur > min(hi + step_s * 0.5, total - 1.0):
                continue
            sc["duration"] = dur
            placed.append((at, sc))
            last_end = at + dur

    # grounds: this video's own pictures, nearest in time, each used once where possible
    pics = _pictures(job, total)
    used, tmp = set(), Path(tempfile.mkdtemp(prefix="docgfx_g_"))
    render_jobs, segs = [], []
    try:
        for k, (at, sc) in enumerate(placed):
            if sc["type"] != "chart":
                timed = sorted((p for p in pics if p[0] >= 0 and str(p[1]) not in used), key=lambda p: abs(p[0] - at))
                still = [p for p in pics if p[0] < 0 and str(p[1]) not in used]
                pick = (timed or still or [None])[0]
                if pick is not None and not (sc["type"] == "timeline"):
                    used.add(str(pick[1]))
                    blur, bright = {"dateline": (3.5, 0.9), "number": (6, 0.62), "quote": (6, 0.58),
                                    "pivot": (5, 0.66), "question": (7, 0.66), "card": (0, 0.84),
                                    "step": (0, 1.0), "social": (9, 0.55)}.get(sc["type"], (8, 0.5))
                    sc["ground"] = ground_uri(pick[1], blur, bright, tmp)
            out = mo_dir / f"doc_{k:02d}_{sc['type']}.mp4"
            if force:
                out.unlink(missing_ok=True)
            out.with_suffix(".sfx.json").write_text(json.dumps(sound_marks(sc)), encoding="utf-8")
            segs.append((round(at, 2), str(out), sc["duration"]))
            if not out.exists():
                render_jobs.append((sc, out))
        if render_jobs:
            log(f"docgfx: rendering {len(render_jobs)} documentary graphics in Chromium...")
            render(render_jobs, workers=max(1, min(3, getattr(engine, "MOTION_WORKERS", 2))), look=look)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    kinds = [Path(p).stem.split("_", 2)[-1] for _, p, _ in segs]
    log(f"docgfx: {len(segs)} graphics — {', '.join(kinds[:10])}{' ...' if len(kinds) > 10 else ''}")
    return segs


# ── preview ────────────────────────────────────────────────────────────────────
SAMPLES = [
    {"type": "dateline", "place": "SARAJEVO, BOSNIA", "date": "June 28, 1914", "note": "Archduke Franz Ferdinand arrives",
     "duration": 5.0},
    {"type": "number", "value": 600000000, "decimals": 0, "prefix": "", "suffix": "", "unit": "people watched it live",
     "context": "about one in six people alive in 1969", "source": "NASA", "duration": 6.0},
    {"type": "chart", "title": "The world's tallest buildings", "unit": " m", "highlight": 0, "source": "CTBUH",
     "bars": [{"label": "Burj Khalifa", "value": 828, "color": ""}, {"label": "Merdeka 118", "value": 679, "color": ""},
              {"label": "Shanghai Tower", "value": 632, "color": ""},
              {"label": "Makkah Clock Tower", "value": 601, "color": ""}], "duration": 8.0},
    {"type": "timeline", "title": "Apollo 11", "duration": 8.5,
     "events": [{"date": "July 16, 1969", "label": "Launch from Cape Kennedy"},
                {"date": "July 20", "label": "The Eagle lands on the Moon"},
                {"date": "July 21", "label": "Armstrong's first step"},
                {"date": "July 24", "label": "Splashdown in the Pacific"}]},
    {"type": "quote", "quote": "That's one small step for man, one giant leap for mankind.",
     "who": "Neil Armstrong", "role": "Commander, Apollo 11", "year": "1969", "duration": 7.5},
    {"type": "pivot", "line": "But the numbers were wrong.", "duration": 4.6},
    {"type": "question", "line": "So what went wrong?", "duration": 4.2},
    {"type": "social", "action": "unfollow", "frm": "Lamine Yamal", "to": "Alex Padilla", "mutual": True,
     "note": "late 2024", "platform": "", "duration": 5.2},
]


def _preview(argv: list) -> None:
    out = HERE / "preview" / "docgfx"
    out.mkdir(parents=True, exist_ok=True)
    style = {}
    try:
        style = json.loads((HERE / "styles" / "documentary.json").read_text(encoding="utf-8")).get("look") or {}
    except (OSError, ValueError):
        pass
    ground = next((Path(a) for a in argv if Path(a).exists()), None)
    tmp = Path(tempfile.mkdtemp(prefix="docgfx_p_"))
    jobs = []
    for i, sc in enumerate(SAMPLES):
        sc = dict(sc)
        if ground is not None and sc["type"] not in ("chart", "timeline"):
            blur, bright = {"dateline": (3.5, 0.9), "number": (6, 0.62), "quote": (6, 0.58),
                            "pivot": (5, 0.66), "question": (7, 0.66), "social": (9, 0.55)}[sc["type"]]
            sc["ground"] = ground_uri(ground, blur, bright, tmp)
        dest = out / f"{i + 1}_{sc['type']}.mp4"
        dest.unlink(missing_ok=True)
        jobs.append((sc, dest))
    t0 = time.time()
    render(jobs, workers=3, look=style)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(jobs)} graphics in {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["preview"]:
        _preview(sys.argv[2:])
    else:
        print(__doc__)
