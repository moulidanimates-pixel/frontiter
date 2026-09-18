#!/usr/bin/env python3
"""wavespeed.py — WaveSpeed AI for Frontier: upload a picture, run a model, keep the result.

WaveSpeed hosts a thousand models behind one key (WAVESPEED_API_KEY in .env), paid
per run from a prepaid balance. Everything here is three calls:

    upload(path)                 -> a URL the models can read (reference pictures)
    run(model, payload, dest)    -> submits, waits, downloads the first output to dest
    balance()                    -> dollars left

A run that fails for a passing reason (a timeout, a 5xx) is tried again; a run
that fails because the balance is empty stops at once with a plain message — no
retry can pay for it, and four more attempts would only print the same error.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import requests

BASE = os.environ.get("WAVESPEED_BASE", "https://api.wavespeed.ai/api/v3")


class WaveSpeedError(RuntimeError):
    """A run that did not produce a picture."""


class WaveSpeedBalanceError(WaveSpeedError):
    """The WaveSpeed balance cannot pay for the run. Retrying cannot fix it."""


def _key() -> str:
    k = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
    if not k:
        raise WaveSpeedError("WAVESPEED_API_KEY is missing from .env — create a key at "
                             "wavespeed.ai (Dashboard -> Access Keys) and paste it there.")
    return k


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}"}


def _broke(status: int, text: str) -> bool:
    t = (text or "").lower()
    return status == 402 or "insufficient" in t or "balance" in t and ("not enough" in t or "low" in t)


def balance() -> float:
    r = requests.get(f"{BASE}/balance", headers=_headers(), timeout=30)
    r.raise_for_status()
    return float((r.json().get("data") or {}).get("balance") or 0.0)


_BAL = {"t": 0.0, "usd": None}


def has_budget(reserve_usd: float = None) -> bool:
    """Whether there is more on the WaveSpeed balance than the reserve kept back (WAVESPEED_RESERVE_USD,
    default $0.50) — checked at most once a minute. With no key or no answer: False."""
    import time as _time
    reserve = float(os.environ.get("WAVESPEED_RESERVE_USD") or 0.5) if reserve_usd is None else reserve_usd
    if not (os.environ.get("WAVESPEED_API_KEY") or "").strip():
        return False
    if _BAL["usd"] is None or _time.time() - _BAL["t"] > 60:
        try:
            _BAL["usd"], _BAL["t"] = balance(), _time.time()
        except Exception:                                   # noqa: BLE001
            return False
    return float(_BAL["usd"]) > reserve


def upload(path: Path, cache_file: Path = None) -> str:
    """A WaveSpeed-hosted URL for a local picture, remembered by its hash so the same
    reference is never uploaded twice for one job."""
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = {}
    if cache_file and Path(cache_file).exists():
        try:
            cache = json.loads(Path(cache_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if digest in cache:
        return cache[digest]
    last = None
    for attempt in range(3):
        try:
            with path.open("rb") as fh:
                r = requests.post(f"{BASE}/media/upload/binary", headers=_headers(),
                                  files={"file": (path.name, fh)}, timeout=180)
            if r.status_code == 200:
                url = (r.json().get("data") or {}).get("download_url") or ""
                if url.startswith("http"):
                    cache[digest] = url
                    if cache_file:
                        Path(cache_file).write_text(json.dumps(cache, indent=2), encoding="utf-8")
                    return url
            last = f"HTTP {r.status_code} {r.text[:160]}"
        except requests.exceptions.RequestException as e:
            last = str(e)[:160]
        time.sleep(3 * (attempt + 1))
    raise WaveSpeedError(f"upload of {path.name} failed: {last}")


def result(model: str, payload: dict, label: str = "", retries: int = 3, timeout_s: int = 600, log=print) -> dict:
    """Run `model` on `payload` and return WaveSpeed's finished result (status, outputs…)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(f"{BASE}/{model}", headers={**_headers(), "Content-Type": "application/json"},
                              json=payload, timeout=120)
            if r.status_code != 200:
                if _broke(r.status_code, r.text):
                    raise WaveSpeedBalanceError(
                        f"WaveSpeed balance is too low for {model} — top up at wavespeed.ai, then run the "
                        f"same title again: everything already made is kept.")
                raise WaveSpeedError(f"{model}: HTTP {r.status_code} {r.text[:200]}")
            pid = (r.json().get("data") or {}).get("id")
            if not pid:
                raise WaveSpeedError(f"{model}: no prediction id in {r.text[:200]}")
            t0 = time.time()
            while True:
                if time.time() - t0 > timeout_s:
                    raise WaveSpeedError(f"{model}: still not done after {timeout_s}s")
                time.sleep(2.5)
                try:
                    g = requests.get(f"{BASE}/predictions/{pid}/result", headers=_headers(), timeout=60)
                except requests.exceptions.RequestException:
                    continue
                d = (g.json().get("data") or {}) if g.status_code == 200 else {}
                st = str(d.get("status") or "").lower()
                if st == "completed":
                    if not d.get("outputs"):
                        raise WaveSpeedError(f"{model}: completed without an output")
                    return d
                if st == "failed":
                    err = str(d.get("error") or "")
                    if _broke(0, err):
                        raise WaveSpeedBalanceError(f"WaveSpeed balance is too low — {err[:120]}")
                    raise WaveSpeedError(f"{model}: failed — {err[:200]}")
        except WaveSpeedBalanceError:
            raise
        except (WaveSpeedError, requests.exceptions.RequestException, ValueError) as e:
            last = e
            if attempt < retries:
                wait = 6 * attempt
                log(f"    wavespeed {label or model}: attempt {attempt}/{retries} failed, retrying in {wait}s — {str(e)[:90]}")
                time.sleep(wait)
    raise WaveSpeedError(f"{label or model} failed after {retries} attempts: {last}")


def download(url: str, dest: Path) -> Path:
    got = requests.get(url, timeout=180)
    got.raise_for_status()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(got.content)
    return dest


def run(model: str, payload: dict, dest: Path, label: str = "", retries: int = 3,
        timeout_s: int = 600, log=print) -> Path:
    """Run `model` on `payload` and save its first output (a picture, a clip, audio) to `dest`."""
    d = result(model, payload, label, retries, timeout_s, log)
    out = d["outputs"][0]
    url = out if isinstance(out, str) else (out.get("audio") or out.get("url") or out.get("image") or "")
    if not str(url).startswith("http"):
        raise WaveSpeedError(f"{label or model}: the output is not a link ({str(out)[:120]})")
    return download(url, dest)


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def voiceover(engine, script: str, job: Path, voice: dict, force: bool = False) -> tuple:
    """(audio.mp3, subs.srt) from ElevenLabs v3 with timings, run on WaveSpeed.

    The model returns every character's start and end, so the subtitles are the script's
    own words on the voice's own clock — no transcription pass, no misspelled names."""
    import re
    import shutil
    import subprocess
    mp3, srt = job / "audio.mp3", job / "subs.srt"
    if mp3.exists() and srt.exists() and not force:
        engine.log("cached: audio.mp3 + subs.srt")
        return mp3, srt
    model = voice.get("model") or "elevenlabs/eleven-v3/timing"
    vdir = job / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    # a few sentences per request: ElevenLabs v3 drifts to another voice on a long block, and the
    # parts are recorded side by side
    limit = int(voice.get("chunk_chars") or 1000)
    pieces, cur = [], ""
    for para in re.split(r"(?<=[.!?])\s+", script.strip()):
        if len(cur) + len(para) + 1 > limit and cur:
            pieces.append(cur)
            cur = ""
        cur = (cur + " " + para).strip()
    if cur:
        pieces.append(cur)
    engine.log(f"WaveSpeed voice: {model} ({voice.get('voice_id') or 'Brian'}), {len(script)} characters, "
               f"{len(pieces)} part(s)")

    def record(i):
        part, meta = vdir / f"part_{i:02d}.mp3", vdir / f"part_{i:02d}.json"
        if force or not (part.exists() and meta.exists()):
            d = result(model, {"text": pieces[i], "voice_id": voice.get("voice_id") or "Brian",
                               "stability": float(voice.get("stability", 0.5)),
                               "similarity": float(voice.get("similarity", 1.0)), "use_speaker_boost": True},
                       label=f"voice {i + 1}", log=engine.log)
            out = d["outputs"][0]
            download(out["audio"], part)
            meta.write_text(json.dumps(out.get("alignment") or {}), encoding="utf-8")
        return part, meta

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, len(pieces) or 1)) as ex:
        recorded = list(ex.map(record, range(len(pieces))))
    words, parts, offset = [], [], 0.0
    for i, (part, meta) in enumerate(recorded):
        al = json.loads(meta.read_text(encoding="utf-8"))
        w, ws, we = "", None, None
        for ch, a, b in zip(al.get("characters", []), al.get("character_start_times_seconds", []),
                            al.get("character_end_times_seconds", [])):
            if ch.isspace():
                if w:
                    words.append((w, ws + offset, we + offset))
                w, ws = "", None
                continue
            w, ws, we = w + ch, (a if ws is None else ws), b
        if w:
            words.append((w, ws + offset, we + offset))
        parts.append(part)
        offset += engine._audio_dur(part)
    # the audio tags a script carries ([pause], [long pause], [softly]) direct the voice — nobody hears
    # them, so they never reach the subtitles
    kept, in_tag = [], False
    for w, a, b in words:
        if in_tag:
            if "]" not in w:
                continue
            w, in_tag = w.split("]", 1)[1], False
        w = re.sub(r"\[[^\]]*\]", "", w)
        if "[" in w:
            w, in_tag = w.split("[", 1)[0], True
        if w.strip():
            kept.append((w, a, b))
    words = kept
    if len(parts) == 1:
        shutil.copyfile(parts[0], mp3)
    else:
        lst = vdir / "parts.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    cues, cur_c = [], []
    for w in words:
        cur_c.append(w)
        if re.search(r"[.!?…]$", w[0]) or len(cur_c) >= 6 or len(" ".join(x[0] for x in cur_c)) >= 32:
            cues.append(cur_c)
            cur_c = []
    if cur_c:
        cues.append(cur_c)
    lines = []
    for k, c in enumerate(cues):
        end = c[-1][2]
        if k + 1 < len(cues) and cues[k + 1][0][1] - end < 0.3:
            end = cues[k + 1][0][1]
        lines.append(f"{k + 1}\n{_srt_time(c[0][1])} --> {_srt_time(end)}\n{' '.join(x[0] for x in c)}\n")
    srt.write_text("\n".join(lines), encoding="utf-8")
    engine.log(f"  voice: {offset:.1f}s, {len(cues)} subtitle lines")
    return mp3, srt
