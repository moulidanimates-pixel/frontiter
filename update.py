#!/usr/bin/env python3
"""update.py — install a Frontier update pack without losing your own work.

    python update.py ~/Downloads/Frontier-3-UPDATE          # install it
    python update.py ~/Downloads/Frontier-3-UPDATE --check  # say what would happen, change nothing

A pack is a folder (or the zip you downloaded, already unzipped) holding:

    update.json      what this update is and which files it carries
    files/…          the new versions, laid out the way they sit in Frontier
    NOTES.md         what changed, for you

The part that matters: YOUR EDITS SURVIVE. For every file the pack carries, the
manifest knows what that file looked like in the version you are coming from.

    your file is identical to that      -> you never touched it, it is replaced
    your file is already the new one    -> nothing to do
    your file differs                   -> you edited it. It is NOT overwritten.
                                           The new version lands beside it as
                                           <name>.new and is listed as a conflict.

Nothing is deleted, and everything touched is copied into .frontier/backup/<time>/
first, so any update can be undone by hand.

A DLC (an extra channel, say) is a pack too and installs the same way. It adds
to Frontier without changing its version, and refuses a Frontier too old to play it.

When there are conflicts, open this folder in Claude Code and say:

    merge the .new files from this update into my versions

which is the one job a model is genuinely good at: it has your file, the new
file, and the notes describing what changed.
"""

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

def find_install(explicit: str = "") -> Path:
    """The Frontier folder this update is for.

    `--into` wins; otherwise the folder holding this script, which is right when
    the copy already inside Frontier is the one being run. A pack carries its own
    copy of update.py so someone on an older version can install at all — run from
    there, the script sits in the pack, not in Frontier, so the target is checked
    rather than assumed. Installing into the wrong folder silently does nothing,
    which is worse than an error."""
    p = Path(explicit).expanduser().resolve() if explicit else Path(__file__).resolve().parent
    if not (p / "make_video.py").exists() or not (p / "styles").is_dir():
        raise SystemExit(
            f"\n  {p} does not look like a Frontier folder (no make_video.py next to a styles/).\n"
            f"  Point me at it:  python update.py <pack folder> --into ~/Desktop/Frontier\n")
    return p


HERE = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pack(src: Path) -> dict:
    """The pack's manifest, whether you point at the folder or at what it contains."""
    for cand in (src / "update.json", src):
        if cand.is_file() and cand.name == "update.json":
            pack = json.loads(cand.read_text(encoding="utf-8"))
            pack["_root"] = cand.parent
            return pack
    # a folder with one folder inside it (what a zip usually unpacks into)
    subs = [d for d in src.iterdir() if d.is_dir() and (d / "update.json").exists()] if src.is_dir() else []
    if len(subs) == 1:
        return load_pack(subs[0])
    sys.exit(f"No update.json in {src} — point me at the folder you unzipped.")


# The first builds of Frontier 3 called themselves 1.2 (and Frontier 2 was 1.1).
LEGACY = {"1.1": "2", "1.2": "3"}


def _ver(v: str) -> tuple:
    """Versions compare as numbers, so 10 comes after 9."""
    v = LEGACY.get(str(v).strip(), str(v).strip())
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def lacking(needs) -> list:
    """The `file:text` entries of a DLC's `needs` that this Frontier does not have —
    how a DLC tells a Frontier that can play it from one that is too old, even when
    the folder carries no version file."""
    out = []
    for n in needs or []:
        f, _, text = str(n).partition(":")
        p = HERE / f
        if not p.is_file() or text not in p.read_text(encoding="utf-8", errors="replace"):
            out.append(n)
    return out


def current_version() -> str:
    f = STATE / "version"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description="Install a Frontier update pack.")
    ap.add_argument("pack", help="the unzipped update folder")
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--into", default="", help="the Frontier folder to update (when running this from the pack)")
    a = ap.parse_args()

    global HERE, STATE
    HERE = find_install(a.into)
    STATE = HERE / ".frontier"

    pack = load_pack(Path(a.pack).expanduser().resolve())
    print(f"  into: {HERE}")
    root, files = pack["_root"], pack.get("files") or {}
    need = str(pack.get("requires") or "").strip()
    have = current_version()
    dlc = str(pack.get("kind") or "") == "dlc"
    title, ver = str(pack.get("name") or "Frontier update"), str(pack.get("version", "?"))
    shown = LEGACY.get(have, have)
    print(f"\n  {title if title.endswith(ver) else title + (' v' if dlc else ' ') + ver}"
          f"   (your Frontier: {'unknown version' if have == 'unknown' else shown})")
    if pack.get("notes"):
        print(f"  {pack['notes']}")
    if need and ((have != "unknown" and _ver(have) < _ver(need)) or (dlc and lacking(pack.get("needs")))):
        sys.exit(f"\n  This {'DLC' if dlc else 'update'} needs Frontier {need} or newer, and this folder is older.\n"
                 f"  Install the Frontier {need} update first, then run this again.\n")

    added, updated, done, conflicts, missing = [], [], [], [], []
    for rel, info in sorted(files.items()):
        src, dst = root / "files" / rel, HERE / rel
        if not src.exists():
            missing.append(rel)
            continue
        if not dst.exists():
            added.append(rel)
        else:
            mine = sha(dst)
            if mine == info.get("sha256"):
                done.append(rel)
            elif mine == info.get("base") or mine in (info.get("known") or ()):
                # untouched: it matches what some Frontier release shipped
                updated.append(rel)
            else:
                conflicts.append(rel)

    def show(title, items, note=""):
        if items:
            print(f"\n  {title} ({len(items)}){note}")
            for r in items:
                print(f"    {r}")

    show("NEW", added)
    show("UPDATED", updated, " — you had not touched these")
    show("ALREADY UP TO DATE", done)
    show("YOURS, KEPT", conflicts, " — you edited these; the new version lands beside them")
    show("MISSING FROM THE PACK", missing, " — the pack is incomplete")

    if a.check:
        print("\n  --check: nothing was changed.\n")
        return
    if missing:
        sys.exit("\n  Refusing to install an incomplete pack.\n")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = STATE / "backup" / stamp
    for rel in added + updated + conflicts:
        src, dst = root / "files" / rel, HERE / rel
        if dst.exists():                       # keep a copy of what you had
            b = backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b)
        if rel in conflicts:                   # never overwrite your own work
            shutil.copy2(src, dst.with_suffix(dst.suffix + ".new"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    STATE.mkdir(exist_ok=True)
    if dlc:
        # a DLC is added to Frontier; it is not a Frontier version of its own
        df = STATE / "dlc.json"
        installed = json.loads(df.read_text(encoding="utf-8")) if df.exists() else {}
        installed[str(pack.get("id") or pack.get("name"))] = str(pack.get("version", ""))
        df.write_text(json.dumps(installed, indent=2), encoding="utf-8")
    else:
        (STATE / "version").write_text(str(pack.get("version", "")).strip() + "\n", encoding="utf-8")
    hist = STATE / "history.json"
    log = json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else []
    log.append({"at": stamp, "kind": "dlc" if dlc else "update", "name": pack.get("name"),
                "version": pack.get("version"), "from": have,
                "added": added, "updated": updated, "kept": conflicts})
    hist.write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"\n  Done. {len(added)} new, {len(updated)} updated, {len(conflicts)} left alone.")
    print(f"  A copy of everything replaced is in .frontier/backup/{stamp}/")
    if conflicts:
        print("\n  You edited these files, so they were NOT overwritten:")
        for r in conflicts:
            print(f"    {r}   (new version: {r}.new)")
        print("\n  Open this folder in Claude Code and say:")
        print("    merge the .new files from this update into my versions")
    if pack.get("after"):
        print(f"\n  {pack['after']}")
    print()


if __name__ == "__main__":
    main()
