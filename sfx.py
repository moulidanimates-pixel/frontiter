#!/usr/bin/env python3
"""sfx.py — sound design under the edit, and a music bed ducked under the voice.

Options → Sound design. For every scene the edit cuts to, the sound follows the picture:

    a whoosh that peaks on the cut into a graphic, a map, a real photo or object, and a softer one out
    a low, warm hit where a figure lands, a date appears or the story turns
    a riser that arrives exactly on a chapter question
    soft taps as the events of a timeline appear, paper for headlines and collage
    a deep hit under the first frame of the cold open

Every effect is synthesised right here from filtered noise and tones — there are no sound files to
license and nothing to download. They sit well under the narration (look.sound.sfx_db).

Music is never bundled. Put tracks you have the rights to in assets/music/<channel>/ (or straight
in assets/music/) and they play under the whole video, fading in and out and dipping every time
the narrator speaks (look.sound.music_db, look.sound.duck_db).

Listen to every effect without making a video (writes preview/sfx/*.wav):

    python sfx.py preview
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 48000
MUSIC = HERE / "assets" / "music"
# music_rel_db: the bed between sentences, in dB against the narration's own loudness — every track sits at the
# same place whatever level it was mastered at (music_db is the fixed gain used when either cannot be measured)
DEFAULTS = {"sfx_db": -19.0, "music_db": -18.0, "music_rel_db": -6.0, "duck_db": -6.0, "music": True}


def _active_db(path: Path, seconds: float = 90.0, channels: int = 1):
    """The loudness of the audible part of a file (dBFS RMS over its sounding blocks), or None — decoded with
    the channel count it is mixed with, so a stereo song and the mono voice are measured alike."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-t", f"{seconds:.0f}", "-i", str(path), "-ac", str(channels), "-ar", "8000",
                        "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout[: len(p.stdout) // 4 * 4], np.float32)
    if len(x) < 8000:
        return None
    k = len(x) // 400
    r = np.sqrt(np.mean(x[: k * 400].reshape(k, 400) ** 2, axis=1) + 1e-12)
    loud = r[r > 10 ** (-45 / 20)]
    return float(20 * np.log10(np.sqrt(np.mean(loud ** 2)))) if len(loud) > 20 else None


def _db(x: float) -> float:
    return float(10 ** (x / 20.0))


# ── synthesis ─────────────────────────────────────────────────────────────────
def _band_noise(n: int, centers, widths, seed: int, frame: int = 1024, hop: int = 256) -> np.ndarray:
    """Noise whose pass band moves over time: `centers`/`widths` in Hz, spread evenly across the
    sound. Built frame by frame in the frequency domain and overlap-added."""
    rng = np.random.default_rng(seed)
    frames = int(math.ceil(n / hop)) + 4
    freqs = np.fft.rfftfreq(frame, 1.0 / SR)
    tt = np.linspace(0, 1, frames)
    c = np.interp(tt, np.linspace(0, 1, len(centers)), centers)
    w = np.interp(tt, np.linspace(0, 1, len(widths)), widths)
    mag = np.exp(-0.5 * ((freqs[None, :] - c[:, None]) / np.maximum(w[:, None], 20.0)) ** 2)
    spec = mag * np.exp(1j * rng.uniform(0, 2 * np.pi, mag.shape))
    blocks = np.fft.irfft(spec, n=frame, axis=1) * np.hanning(frame)[None, :]
    out = np.zeros(frames * hop + frame)
    for i in range(frames):
        out[i * hop:i * hop + frame] += blocks[i]
    out = out[frame // 2: frame // 2 + n]
    return out / (np.abs(out).max() + 1e-9)


def _space(x: np.ndarray, seconds: float, mix: float, seed: int) -> np.ndarray:
    """A short, dark room around a mono sound (FFT convolution with a decaying noise tail)."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = _band_noise(n, [2200, 900, 400], [1800, 900, 400], seed) * np.exp(-t * 6.9 / seconds)
    size = 1 << int(math.ceil(math.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x) + n]
    wet /= (np.abs(wet).max() + 1e-9)
    dry = np.concatenate([x, np.zeros(n)])
    return dry * (1 - mix) + wet * mix * np.abs(x).max()


def _stereo(x: np.ndarray, pan=None, width: float = 0.0, seed: int = 0) -> np.ndarray:
    if pan is None:
        pan = np.full(len(x), 0.5)
    left, right = x * np.cos(pan * np.pi / 2), x * np.sin(pan * np.pi / 2)
    if width > 0:                                   # a few ms apart: wide, not doubled
        d = int(0.006 * SR)
        right = np.concatenate([np.zeros(d), right])[: len(x)] * (1 - width) + right * width
    out = np.stack([left, right], 1) * math.sqrt(2)
    return (out / (np.abs(out).max() + 1e-9)).astype(np.float32)


def whoosh(dur: float = 1.15, seed: int = 1, up: bool = True) -> tuple:
    """(sound, peak second) — air moving past, rising into the cut."""
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    peak = 0.64
    x = _band_noise(n, [260, 700, 2400, 3000, 1100, 420], [140, 380, 1300, 1600, 700, 260], seed)
    x += 0.35 * _band_noise(n, [90, 160, 260, 180, 90], [50, 90, 140, 90, 50], seed + 7)
    env = np.where(t < peak, (t / peak) ** 2.4, np.exp(-(t - peak) * 7.5))
    pan = np.clip(0.15 + t * 0.7, 0, 1) if up else np.clip(0.85 - t * 0.7, 0, 1)
    return _stereo(x * env, pan, 0.3, seed), dur * peak


def hit(dur: float = 2.8, seed: int = 2, deep: float = 1.0) -> tuple:
    """(sound, 0.0) — a low, warm impact with a short room."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = (36 + 46 * np.exp(-t * 16)) * deep
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.4)
    knock = _band_noise(n, [2600, 900, 300], [1900, 700, 220], seed) * np.exp(-t * 48)
    air = _band_noise(n, [220, 150, 110], [140, 100, 70], seed + 1) * np.exp(-t * 1.5)
    x = body * 0.95 + knock * 0.28 + air * 0.3
    x[: int(0.004 * SR)] *= np.linspace(0, 1, int(0.004 * SR))
    x = _space(x, 1.6, 0.22, seed + 3)[:n]
    return _stereo(x, None, 0.5, seed), 0.0


def riser(dur: float = 2.3, seed: int = 3) -> tuple:
    """(sound, last second) — builds to the moment it ends on."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    k = t / dur
    noise = _band_noise(n, [380, 900, 2300, 5200], [200, 520, 1400, 3000], seed)
    f = 150 * 2 ** (k * 2.4)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * (0.22 + 0.1 * np.sin(2 * np.pi * 5.5 * t))
    x = (noise * 0.85 + tone) * k ** 2.7
    x[-int(0.02 * SR):] *= np.linspace(1, 0, int(0.02 * SR))
    return _stereo(x, np.clip(0.5 + 0.25 * np.sin(2 * np.pi * 0.6 * t), 0, 1), 0.4, seed), dur


def tap(seed: int = 4) -> tuple:
    """(sound, 0.0) — a small wooden tick for a dot on a timeline."""
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    x = (np.sin(2 * np.pi * 820 * t) * 0.6 + np.sin(2 * np.pi * 1640 * t) * 0.25) * np.exp(-t * 26)
    x += _band_noise(n, [3500, 1800], [1500, 900], seed) * np.exp(-t * 120) * 0.5
    x = _space(x, 0.5, 0.15, seed)[:n]
    return _stereo(x, None, 0.2, seed), 0.0


def paper(dur: float = 0.75, seed: int = 5) -> tuple:
    """(sound, 0.25) — a sheet sliding onto a table."""
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    x = _band_noise(n, [2400, 4800, 3600, 2000], [1500, 2600, 2000, 1200], seed)
    rng = np.random.default_rng(seed)
    grain = np.interp(np.arange(n), np.linspace(0, n, 40), rng.uniform(0.45, 1.0, 40))
    env = np.where(t < 0.3, (t / 0.3) ** 1.6, np.exp(-(t - 0.3) * 6))
    return _stereo(x * env * grain, np.clip(0.3 + t * 0.4, 0, 1), 0.25, seed), dur * 0.3


BANK = {"whoosh": whoosh, "hit": hit, "riser": riser, "tap": tap, "paper": paper}


# ── what plays where ───────────────────────────────────────────────────────────
def cues(plan: list) -> list:
    """[(second the sound's key moment lands on, sound, gain dB)] for a finished timeline."""
    out, t = [(0.0, "hit", -1.0)], 0.0
    for kind, path, _leak, dur in plan:
        dur = float(dur)
        name = Path(str(path)).name
        stem = Path(str(path)).stem
        if kind == "motion":
            marks = Path(str(path)).with_suffix(".sfx.json")
            if name.startswith("doc_"):
                try:        # the graphic knows its own timing (docgfx.sound_marks)
                    out += [(t + float(a), str(s_), float(g)) for a, s_, g in json.loads(marks.read_text(encoding="utf-8"))]
                except (OSError, ValueError, TypeError):
                    out.append((t, "whoosh", -3.0))
                out.append((t + dur, "whoosh_out", -9.0))
            elif name.startswith("map_"):
                out += [(t, "whoosh", -3.0), (t + 1.1, "hit", -10.0), (t + dur, "whoosh_out", -10.0)]
            elif name.startswith("headline_"):
                out += [(t, "paper", -3.0), (t + 1.4, "hit", -12.0)]
            elif name.startswith(("spot_", "objects_", "depth_")):
                out += [(t, "whoosh", -5.0)]
            elif stem.startswith(("vox", "scene_vox")) or "/vox/" in str(path):
                out += [(t, "paper", -4.0)]
            elif stem != "introcap":
                out += [(t, "whoosh", -4.0)]
        t += dur
    return sorted(out)


# ── the mix ───────────────────────────────────────────────────────────────────
def _tracks(style: str) -> list:
    for d in (MUSIC / style, MUSIC):
        if d.is_dir():
            got = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac"))
            if got:
                return got
    return []


def _pcm(path: Path, channels: int, loops: int = 0):
    cmd = ["ffmpeg", "-v", "error"] + (["-stream_loop", str(loops)] if loops else []) + \
          ["-i", str(path), "-f", "f32le", "-ac", str(channels), "-ar", str(SR), "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip() or 0)
    except ValueError:
        return 0.0


def mix(engine, job: Path, voice: Path, plan: list, style: str) -> Path:
    """The narration with the sound design (and any music) under it, as job/_voice_sound.wav."""
    look = ((engine.STYLE_INFO.get(style) or {}).get("look") or {}) if engine is not None else {}
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (look.get("sound") or {}).items() if k in DEFAULTS})
    out = job / "_voice_sound.wav"
    total = _duration(voice)
    if total <= 0:
        return voice
    log = engine.log if engine is not None else print
    sounds = {}
    variants = {"whoosh": 4, "hit": 3, "riser": 2, "tap": 3, "paper": 3}
    for name, fn in BANK.items():
        sounds[name] = [fn(seed=11 + 17 * k) for k in range(variants[name])]
    sounds["whoosh_out"] = [whoosh(dur=0.9, seed=301 + 17 * k, up=False) for k in range(3)]
    events = []
    for i, (at, name, gain) in enumerate(cues(plan)):
        bank = sounds.get(name)
        if not bank:
            continue
        snd, key = bank[i % len(bank)]
        start = int(round((at - key) * SR))
        if start + len(snd) <= 0 or start >= total * SR:
            continue
        events.append((start, snd, _db(cfg["sfx_db"] + gain)))
    tracks = _tracks(style) if cfg.get("music") else []
    # one track asked for by name: FRONTIER_MUSIC in the environment, or look.sound.track in the style —
    # looked for in the channel's folder first, then anywhere under assets/music/
    want = (os.environ.get("FRONTIER_MUSIC") or str((look.get("sound") or {}).get("track") or "")).strip().lower()
    named = []
    if want and cfg.get("music"):
        match = lambda t: t.name.lower() == want or t.stem.lower() == want
        named = [t for t in tracks if match(t)] or sorted(
            t for t in MUSIC.rglob("*") if t.is_file() and t.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")
            and match(t)) if MUSIC.is_dir() else []
        tracks = tracks or named
    music, mdb = None, None
    if tracks:
        pick = named[0] if named else tracks[abs(hash(job.name)) % len(tracks)]
        loops = int(total // max(1.0, _duration(pick)))
        music = _pcm(pick, 2, loops)
        vdb, mdb = _active_db(voice, total + 1), _active_db(pick, min(total, _duration(pick)) + 1, channels=2)
        if vdb is not None and mdb is not None:
            cfg["music_db"] = max(-40.0, min(0.0, vdb + float(cfg["music_rel_db"]) - mdb))
        log(f"sound: {len(events)} effects + music bed '{pick.name}' under the voice"
            + (f" ({cfg['music_db']:+.1f} dB: {cfg['music_rel_db']:+.0f} dB against the voice)" if vdb is not None and mdb is not None else ""))
    else:
        log(f"sound: {len(events)} effects under the voice (no music in assets/music/)")
    vproc = _pcm(voice, 1)
    chunk = SR * 5
    duck_gain, env_state, level_gain = 1.0, 0.0, 1.0
    fade_in, fade_out = 2.5 * SR, 4.0 * SR
    n_total = int(total * SR)
    pos = 0
    wav = wave.open(str(out), "wb")
    wav.setnchannels(2)
    wav.setsampwidth(2)
    wav.setframerate(SR)
    try:
        while True:
            raw = vproc.stdout.read(chunk * 4)
            if not raw:
                break
            v = np.frombuffer(raw[: len(raw) // 4 * 4], np.float32)
            n = len(v)
            buf = np.repeat(v[:, None], 2, 1).astype(np.float32)
            for start, snd, g in events:
                a, b = max(start, pos), min(start + len(snd), pos + n)
                if a < b:
                    buf[a - pos:b - pos] += snd[a - start:b - start] * g
            if music is not None:
                mraw = music.stdout.read(n * 8)
                m = np.frombuffer(mraw[: len(mraw) // 8 * 8], np.float32).reshape(-1, 2)
                if len(m) < n:
                    m = np.vstack([m, np.zeros((n - len(m), 2), np.float32)])
                # the voice's loudness, smoothed: fast down, slow back up
                blk = 480
                nb = int(math.ceil(n / blk))
                rms = np.sqrt(np.mean(np.pad(v, (0, nb * blk - n)).reshape(nb, blk) ** 2, axis=1))
                target = np.where(rms > 0.02, _db(cfg["duck_db"]), 1.0)
                gains = np.empty(nb)
                # a quiet passage of the track is lifted (at most +6 dB) and a loud one tamed, slowly, so the
                # bed stays at one level under the voice however the song was mastered
                mr = float(np.sqrt(np.mean(m[:n] ** 2)) + 1e-9)
                lift = min(2.0, max(0.5, (10 ** (mdb / 20) if mdb is not None else mr) / mr)) if mr > 1e-4 else 1.0
                for k in range(nb):
                    coef = 0.35 if target[k] < duck_gain else 0.08
                    duck_gain += (target[k] - duck_gain) * coef
                    gains[k] = duck_gain
                gain = np.repeat(gains, blk)[:n]
                idx = np.arange(pos, pos + n)
                fade = np.minimum(1.0, np.minimum(idx / fade_in, np.maximum(0.0, (n_total - idx) / fade_out)))
                ramp = np.linspace(level_gain, lift, n)
                level_gain = lift
                buf += m[:n] * (gain * fade * ramp * _db(cfg["music_db"]))[:, None]
            # a gentle ceiling instead of clipping
            over = np.abs(buf) > 0.92
            if over.any():
                buf[over] = np.sign(buf[over]) * (0.92 + 0.08 * np.tanh((np.abs(buf[over]) - 0.92) / 0.08))
            wav.writeframes((np.clip(buf, -1, 1) * 32767).astype("<i2").tobytes())
            pos += n
    finally:
        wav.close()
        for p in (vproc, music):
            if p is not None:
                p.stdout.close()
                p.kill()
    if pos <= 0:
        return voice
    return master(out, log)


def master(path: Path, log=print, target: float = -14.0) -> Path:
    """The finished soundtrack at YouTube's loudness (-14 LUFS, peaks under -1.5 dBTP): measured once, then
    turned up or down in one straight line, so nothing pumps. A quieter upload is simply played quieter."""
    import json as _json
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                            f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr or "")
    if not m:
        return path
    try:
        got = _json.loads(m.group(0))
        flt = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={got['input_i']}:measured_TP={got['input_tp']}:"
               f"measured_LRA={got['input_lra']}:measured_thresh={got['input_thresh']}:offset={got['target_offset']}:linear=true")
    except (ValueError, KeyError):
        return path
    tmp = path.with_name(path.stem + "_master.wav")
    run = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", flt, "-ar", str(SR), "-ac", "2",
                          "-c:a", "pcm_s16le", str(tmp)], capture_output=True, text=True)
    if run.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return path
    tmp.replace(path)
    log(f"sound: mastered from {float(got['input_i']):.1f} to {target:.0f} LUFS")
    return path


# ── preview ────────────────────────────────────────────────────────────────────
def _write(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767 * 0.8).astype("<i2").tobytes())


if __name__ == "__main__":
    if sys.argv[1:2] == ["preview"]:
        d = HERE / "preview" / "sfx"
        d.mkdir(parents=True, exist_ok=True)
        for name, fn in BANK.items():
            _write(d / f"{name}.wav", fn()[0])
        _write(d / "whoosh_out.wav", whoosh(dur=0.9, seed=301, up=False)[0])
        print(f"wrote {len(BANK) + 1} sounds to {d}")
    else:
        print(__doc__)
