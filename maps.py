#!/usr/bin/env python3
"""maps.py — the MAPS DLC: map animations over real borders, timed to the narration.

When the voice says a place, the video cuts to a map of it: the country, a push in,
the place lights up and lifts off the map while its name writes itself on.

    python maps.py demo                         # a few sample scenes into preview/
    python maps.py show "California"            # one place, rendered and opened
    python maps.py show "Lisbon" --title "Lisbon" --subtitle "where it began"
    python maps.py show "London > New York" --icon plane     # a route
    python maps.py check                        # every shot in every look, errors reported

How it works
------------
* Geography is REAL, not drawn by an image model: Natural Earth borders (public
  domain), shipped in assets/maps/ at three levels of detail. An AI picture of a map
  gets borders wrong and cannot be flown through; this can zoom from the globe to one
  state without ever going soft.
* Each scene is a Chromium page (assets/maps/map.js, d3-geo) rendered frame by frame,
  the same deterministic way motion.py renders every other graphic.
* Only what a scene draws travels into its page: the countries and states around the
  place are cut out of the big topology files here (`_subset`).
"""

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

HERE = Path(__file__).resolve().parent
MAPS = HERE / "assets" / "maps"
W, H, FPS = 1920, 1080, 30
R_KM = 6371.0088

# ════════════════════════════════════════════════════════════════════════════
# DATA
# ════════════════════════════════════════════════════════════════════════════
_CACHE: dict = {}


def _load(name: str) -> dict:
    if name not in _CACHE:
        _CACHE[name] = json.loads((MAPS / f"{name}.json").read_text(encoding="utf-8"))
    return _CACHE[name]


def gazetteer() -> dict:
    g = _load("gazetteer")
    if "_by_id" not in g:
        g["_by_id"] = {r["id"]: r for key in ("countries", "admin1", "water", "regions") for r in g[key]}
    return g


def installed() -> bool:
    """True when the DLC's data is present — the engine asks before it plans any maps."""
    return (MAPS / "gazetteer.json").exists() and (MAPS / "map.js").exists()


# ── bounding boxes, antimeridian-aware ──────────────────────────────────────
def _norm_lon(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def _box_span(b) -> tuple:
    """(west, east) with east >= west, east possibly past 180 when the box wraps."""
    w, e = b[0], b[2]
    return (w, e + 360.0) if e < w else (w, e)


def _box_union(boxes: list) -> list:
    """The smallest box holding all of them, choosing whichever way round the globe is shorter."""
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    s = min(b[1] for b in boxes)
    n = max(b[3] for b in boxes)
    spans = [_box_span(b) for b in boxes]
    # try every box's west edge as the start of the union and keep the narrowest
    best = None
    for w0, _ in spans:
        e_max = w0
        for w, e in spans:
            ww, ee = w, e
            while ww < w0:
                ww, ee = ww + 360, ee + 360
            e_max = max(e_max, ee)
        if best is None or e_max - w0 < best[1] - best[0]:
            best = (w0, e_max)
    w, e = best
    if e - w >= 360:
        return [-180.0, s, 180.0, n]
    return [_norm_lon(w), s, _norm_lon(e) if e > 180 else e, n]


def _box_grow(b, f: float, min_deg: float = 0.0) -> list:
    w, e = _box_span(b)
    cx, cy = (w + e) / 2, (b[1] + b[3]) / 2
    hw = max((e - w) * f, min_deg) / 2
    hh = max((b[3] - b[1]) * f, min_deg * 0.6) / 2
    w2, e2 = cx - hw, cx + hw
    if e2 - w2 >= 360:
        return [-180.0, max(-89.0, cy - hh), 180.0, min(89.0, cy + hh)]
    return [_norm_lon(w2), max(-89.0, cy - hh), _norm_lon(e2), min(89.0, cy + hh)]


def _box_hits(a, b) -> bool:
    if a[1] > b[3] or b[1] > a[3]:
        return False
    aw, ae = _box_span(a)
    bw, be = _box_span(b)
    for shift in (-360, 0, 360):
        if aw + shift <= be and bw <= ae + shift:
            return True
    return False


def _box_km(b) -> tuple:
    w, e = _box_span(b)
    mid = math.radians((b[1] + b[3]) / 2)
    return ((e - w) * 111.32 * max(0.05, math.cos(mid)), (b[3] - b[1]) * 110.57)


# ── topology ────────────────────────────────────────────────────────────────
def _subset(topo: dict, wanted: dict) -> dict:
    """A topology holding only `wanted` = {object: ids} and the arcs those use.

    The hi-detail file is 5 MB; one scene needs a few hundred kB of it. Arcs are
    delta-encoded one by one, so they can be picked out and renumbered untouched."""
    remap, arcs = {}, []

    def m(i):
        j = ~i if i < 0 else i
        if j not in remap:
            remap[j] = len(arcs)
            arcs.append(topo["arcs"][j])
        n = remap[j]
        return ~n if i < 0 else n

    objects = {}
    for name, ids in wanted.items():
        obj = topo["objects"].get(name)
        if not obj or not ids:
            continue
        geoms = []
        for g in obj["geometries"]:
            if g.get("id") not in ids or not g.get("type"):
                continue
            t, a = g["type"], g["arcs"]
            if t == "Polygon":
                a = [[m(i) for i in ring] for ring in a]
            elif t == "MultiPolygon":
                a = [[[m(i) for i in ring] for ring in poly] for poly in a]
            elif t == "LineString":
                a = [m(i) for i in a]
            elif t == "MultiLineString":
                a = [[m(i) for i in line] for line in a]
            else:
                continue
            geoms.append({"type": t, "id": g["id"], "arcs": a})
        objects[name] = {"type": "GeometryCollection", "geometries": geoms}
    out = {"type": "Topology", "arcs": arcs, "objects": objects}
    if topo.get("transform"):
        out["transform"] = topo["transform"]
    return out


def _arc(topo: dict, i: int) -> list:
    raw = topo["arcs"][~i if i < 0 else i]
    tr = topo.get("transform")
    if tr:
        (sx, sy), (tx, ty) = tr["scale"], tr["translate"]
        x = y = 0
        pts = []
        for dx, dy in raw:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
    else:
        pts = [tuple(p) for p in raw]
    return pts[::-1] if i < 0 else pts


def _polygons(topo: dict, obj: str, fid: str) -> list:
    """[[ring, ...], ...] in lon/lat for one feature."""
    for g in topo["objects"].get(obj, {}).get("geometries", []):
        if g.get("id") != fid:
            continue
        polys = g["arcs"] if g["type"] == "MultiPolygon" else ([g["arcs"]] if g["type"] == "Polygon" else [])
        out = []
        for poly in polys:
            rings = []
            for ring in poly:
                pts = []
                for i in ring:
                    c = _arc(topo, i)
                    pts.extend(c[1:] if pts else c)
                rings.append(pts)
            out.append(rings)
        return out
    return []


def _ring_box_area(ring: list) -> tuple:
    lons = [p[0] for p in ring]
    # keep a ring that crosses the antimeridian in one piece
    if max(lons) - min(lons) > 180:
        lons = [x + 360 if x < 0 else x for x in lons]
    lats = [p[1] for p in ring]
    a = 0.0
    for (x0, y0), (x1, y1) in zip(zip(lons, lats), list(zip(lons, lats))[1:] + [(lons[0], lats[0])]):
        a += x0 * y1 - x1 * y0
    mid = math.radians(sum(lats) / len(lats))
    km2 = abs(a) / 2 * 111.32 * 110.57 * max(0.05, math.cos(mid))
    return [_norm_lon(min(lons)), min(lats), _norm_lon(max(lons)) if max(lons) > 180 else max(lons), max(lats)], km2


# Countries whose far-flung parts would otherwise pull the camera out to sea. A video
# that says "France" means the hexagon, not French Guiana; "America", the lower 48.
FRAME_OVERRIDE = {
    "USA": [-125.0, 24.3, -66.9, 49.4], "FRA": [-5.3, 41.3, 9.7, 51.2], "NLD": [3.3, 50.7, 7.3, 53.6],
    "NOR": [4.4, 57.9, 31.3, 71.3], "PRT": [-9.6, 36.9, -6.1, 42.2], "ESP": [-9.4, 35.9, 4.4, 43.8],
    "ECU": [-81.1, -5.1, -75.1, 1.5], "CHL": [-75.8, -56.0, -66.9, -17.4], "NZL": [166.3, -47.4, 178.6, -34.3],
    "DNK": [8.0, 54.5, 15.3, 57.8], "GBR": [-8.3, 49.8, 1.9, 58.8], "RUS": [27.3, 41.1, -170.0, 77.8],
    "AUS": [112.9, -43.8, 153.8, -10.5], "JPN": [128.9, 30.9, 145.9, 45.6], "ZAF": [16.4, -35.0, 33.0, -22.1],
    "KIR": [172.5, -2.8, -150.0, 4.8], "YEM": [42.5, 12.5, 54.0, 19.0], "COL": [-79.1, -4.3, -66.8, 12.6],
    "VEN": [-73.4, 0.6, -59.8, 12.3], "BRA": [-74.0, -33.8, -34.7, 5.3], "IND": [68.1, 6.7, 97.4, 35.7],
    "CAN": [-141.0, 41.6, -52.6, 72.0], "GRL": [-73.3, 59.7, -11.3, 83.7], "MEX": [-117.2, 14.5, -86.7, 32.8],
    "ARG": [-73.6, -55.1, -53.6, -21.8], "ITA": [6.6, 36.6, 18.6, 47.1], "GRC": [19.3, 34.8, 28.3, 41.8],
    "IDN": [95.0, -11.0, 141.1, 6.1], "PHL": [116.9, 4.6, 126.6, 21.1], "CHN": [73.5, 18.2, 134.8, 53.6],
}


def core_frame(topo_name: str, obj: str, fid: str, fallback=None) -> list:
    """The box the camera frames for a feature: its main body and whatever sits close
    to it, never the island on the other side of the planet that legally belongs to it."""
    if fid in FRAME_OVERRIDE:
        return FRAME_OVERRIDE[fid]
    polys = _polygons(_load(topo_name), obj, fid)
    if not polys:
        return fallback
    parts = [_ring_box_area(p[0]) for p in polys if p and len(p[0]) > 2]
    if not parts:
        return fallback
    parts.sort(key=lambda x: -x[1])
    main_box, main_km2 = parts[0]
    chosen = [main_box]
    radius = math.sqrt(main_km2 / math.pi)
    reach = max(350.0, radius * 1.1)
    grew = True
    while grew:
        grew = False
        cur = _box_union(chosen)
        for b, km2 in parts[1:]:
            if b in chosen or km2 < main_km2 * 0.002:
                continue
            gap = _box_gap_km(cur, b)
            if gap <= reach:
                chosen.append(b)
                grew = True
    return _box_union(chosen)


def _box_gap_km(a, b) -> float:
    aw, ae = _box_span(a)
    bw, be = _box_span(b)
    best = 1e9
    for shift in (-360, 0, 360):
        dx = max(0.0, max(aw, bw + shift) - min(ae, be + shift))
        dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
        mid = math.radians((a[1] + a[3] + b[1] + b[3]) / 4)
        best = min(best, math.hypot(dx * 111.32 * max(0.05, math.cos(mid)), dy * 110.57))
    return best


# ════════════════════════════════════════════════════════════════════════════
# RESOLVE — a spoken place name to a real feature
# ════════════════════════════════════════════════════════════════════════════
def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ")).strip()
    return re.sub(r"^(the|la|le|el|al) ", "", s)


# What scripts call a country, pointed at the feature that holds it.
COUNTRY_ALIASES = {
    "america": "USA", "united states": "USA", "united states of america": "USA", "usa": "USA",
    "u s": "USA", "us": "USA", "u s a": "USA", "states": "USA",
    "britain": "GBR", "great britain": "GBR", "uk": "GBR", "u k": "GBR", "united kingdom": "GBR",
    "holland": "NLD", "netherlands": "NLD", "czech republic": "CZE", "czechia": "CZE",
    "ivory coast": "CIV", "cote d ivoire": "CIV", "burma": "MMR", "persia": "IRN", "siam": "THA",
    "ceylon": "LKA", "east timor": "TLS", "swaziland": "SWZ", "macedonia": "MKD", "turkiye": "TUR",
    "drc": "COD", "dr congo": "COD", "democratic republic of the congo": "COD", "congo kinshasa": "COD",
    "congo brazzaville": "COG", "republic of the congo": "COG", "south korea": "KOR", "korea": "KOR",
    "north korea": "PRK", "uae": "ARE", "emirates": "ARE", "vatican": "VAT", "vatican city": "VAT",
    "bosnia": "BIH", "the gambia": "GMB", "cape verde": "CPV", "palestine": "PSX", "taiwan": "TWN",
}
# Parts of a country Natural Earth only holds as groups of smaller units.
GROUPS = {
    "england": ("GBR", "region", ["North East", "North West", "Yorkshire and the Humber", "East Midlands",
                                  "West Midlands", "East", "Greater London", "South East", "South West"]),
    "scotland": ("GBR", "region", ["Highlands and Islands", "North Eastern", "Eastern", "South Western"]),
    "wales": ("GBR", "region", ["East Wales", "West Wales and the Valleys"]),
    "northern ireland": ("GBR", "region", ["Northern Ireland"]),
}
# Seas Natural Earth cuts into named pieces. "The Mediterranean" is all of them.
WATER_GROUPS = {
    "mediterranean": ["Mediterranean Sea", "Alboran Sea", "Balearic Sea", "Golfe du Lion", "Ligurian Sea",
                      "Tyrrhenian Sea", "Adriatic Sea", "Ionian Sea", "Aegean Sea", "Sea of Crete",
                      "Gulf of Gabès", "Gulf of Sidra", "Strait of Gibraltar"],
    "atlantic": ["North Atlantic Ocean", "South Atlantic Ocean"],
    "atlantic ocean": ["North Atlantic Ocean", "South Atlantic Ocean"],
    "pacific": ["North Pacific Ocean", "South Pacific Ocean"],
    "pacific ocean": ["North Pacific Ocean", "South Pacific Ocean"],
}
WATER_GROUPS["mediterranean sea"] = WATER_GROUPS["mediterranean"]

_IDX: dict = {}


def _index(kind: str) -> dict:
    """folded name -> [(record, is_primary_name)] for one gazetteer table."""
    if kind in _IDX:
        return _IDX[kind]
    idx: dict = {}
    for r in gazetteer()[kind]:
        names = [r.get("name"), r.get("en"), r.get("long"), r.get("formal"), r.get("admin"), r.get("ascii"),
                 r.get("par"), r.get("woe"), r.get("gns"), r.get("local"), r.get("label")]
        names += re.split(r"[|;,]", r.get("alt") or "")
        names += r.get("names") or []
        seen = set()
        for i, n in enumerate(names):
            k = _fold(n)
            if k and k not in seen:
                seen.add(k)
                idx.setdefault(k, []).append((r, i < 2))
    # regions inside a country (Italy's Toscana, France's Occitanie) as one group each
    if kind == "admin1":
        by_region: dict = {}
        for r in gazetteer()["admin1"]:
            if r.get("region"):
                by_region.setdefault((r["a3"], r["region"]), []).append(r)
        for (a3, region), members in by_region.items():
            if len(members) > 1:
                grp = {"id": f"grp:{a3}:{region}", "name": region, "a3": a3, "members": [m["id"] for m in members],
                       "bbox": _box_union([m.get("bbox") for m in members]), "_group": True}
                idx.setdefault(_fold(region), []).append((grp, True))
    _IDX[kind] = idx
    return idx


def country_code(hint) -> str:
    """'United States' / 'USA' / 'us' / 'Czechia' -> the Natural Earth country id, or ''."""
    if not hint:
        return ""
    raw = str(hint).strip()
    k = _fold(raw)
    if k in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[k]
    g = gazetteer()
    up = raw.upper()
    for c in g["countries"]:
        if up in (c.get("id"), c.get("a3")) or (len(up) == 2 and up == c.get("a2")):
            return c["id"]
    hits = _index("countries").get(k) or []
    return hits[0][0]["id"] if hits else ""


def _km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(a)))


def _point_box_km(lon, lat, b) -> float:
    """How far a point is from a box (0 inside it)."""
    if not b:
        return 1e9
    w, e = _box_span(b)
    for x in (lon, lon + 360, lon - 360):
        if w <= x <= e and b[1] <= lat <= b[3]:
            return 0.0
    cx = min(max(lon, w), e) if w <= lon + 360 <= e + 360 else min((w, e), key=lambda v: abs(v - lon))
    cy = min(max(lat, b[1]), b[3])
    return _km(lon, lat, _norm_lon(cx), cy)


_KINDS = {
    "country": ["countries", "admin1", "regions"], "nation": ["countries"], "empire": ["countries"],
    "state": ["admin1", "countries", "regions"], "province": ["admin1", "regions"], "region": ["admin1", "regions", "countries"],
    "county": ["admin1"], "territory": ["admin1", "countries"], "prefecture": ["admin1"], "department": ["admin1"],
    "city": ["cities"], "town": ["cities"], "village": ["cities"], "capital": ["cities"], "place": ["countries", "cities", "admin1", "regions", "water"],
    "sea": ["water"], "ocean": ["water"], "gulf": ["water"], "bay": ["water"], "strait": ["water"], "channel": ["water"],
    "lake": ["regions", "water"], "desert": ["regions"], "mountains": ["regions"], "range": ["regions"],
    "peninsula": ["regions"], "island": ["regions", "countries", "admin1"], "basin": ["regions"], "plain": ["regions"],
    "plateau": ["regions"], "continent": ["regions"], "area": ["regions", "admin1", "countries"],
}
_LEVEL = {"countries": "country", "admin1": "admin1", "cities": "point", "water": "water", "regions": "region"}


def resolve(place: dict):
    """A place as the scene designer described it -> a target the renderer can draw, or None.

    place = {"name": "California", "type": "state", "country": "United States",
             "lat": 36.7, "lon": -119.4}          (lat/lon: the designer's own guess)
    A "group" type carries "members": [place, ...] and draws as one merged shape — how
    the Soviet Union or Scandinavia appears on today's borders.
    """
    if not isinstance(place, dict):
        place = {"name": str(place)}
    name = str(place.get("query") or place.get("name") or "").strip()
    typ = _fold(place.get("type") or "place").split(" ")[-1] if place.get("type") else "place"
    # every spelling the designer offered: "Tuscany" and "Toscana" are the same region
    spellings = [name] + [str(x) for x in (place.get("names") or []) if x]
    a3 = country_code(place.get("country"))
    lat, lon = place.get("lat"), place.get("lon")
    try:
        lat, lon = (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        lat = lon = None
    key = _fold(name)
    g = gazetteer()

    if typ == "group" or place.get("members"):
        members = [resolve(m if isinstance(m, dict) else {"name": m, "type": "country"}) for m in place.get("members") or []]
        members = [m for m in members if m and m["level"] in ("country", "admin1")]
        if members:
            obj = "admin1" if all(m["level"] == "admin1" for m in members) else "countries"
            ids = [m["id"] for m in members if (m["level"] == "admin1") == (obj == "admin1")]
            return {"level": "group", "id": "grp:" + (key or "group"), "obj": obj, "ids": ids,
                    "name": name or members[0]["name"], "a3": members[0].get("a3"),
                    "a3s": sorted({m.get("a3") for m in members if m.get("a3")}),
                    "label": [lon, lat] if lon is not None else members[0]["label"],
                    "frame": _box_union([m["frame"] for m in members])}

    if key in WATER_GROUPS and typ not in ("city", "town", "capital"):
        names = set(WATER_GROUPS[key])
        recs = [w for w in g["water"] if w["name"] in names]
        if recs:
            return {"level": "water", "id": "grp:" + key, "obj": "water", "ids": [w["id"] for w in recs],
                    "name": name, "label": None, "frame": _box_union([w.get("bbox") for w in recs]), "group": True}

    if key in GROUPS and typ not in ("city", "town", "capital"):
        ga3, field, values = GROUPS[key]
        ids = [r["id"] for r in g["admin1"] if r.get("a3") == ga3 and r.get(field) in values]
        if ids:
            boxes = [g["_by_id"][i].get("bbox") for i in ids]
            return {"level": "group", "id": "grp:" + key, "obj": "admin1", "ids": ids, "name": name, "a3": ga3,
                    "a3s": [ga3], "label": [lon, lat] if lon is not None else None, "frame": _box_union(boxes)}

    if typ not in ("city", "town", "village", "capital") and key in COUNTRY_ALIASES:
        rec = g["_by_id"][COUNTRY_ALIASES[key]]
        return _target("countries", rec, name)

    kinds = _KINDS.get(typ) or ["countries", "cities", "admin1", "regions", "water"]
    best = None
    keys = []
    for sp in spellings:
        k = _fold(sp)
        if k and k not in keys:
            keys.append(k)
    for rank, kind in enumerate(kinds):
        for rec, primary in [hit for k in keys for hit in _index(kind).get(k, [])]:
            score = 10.0 - rank * 2.5 + (1.5 if primary else 0.0) + (1.0 if rec.get("_group") and typ == "region" else 0.0)
            rec_a3 = rec.get("a3") or rec.get("id")
            if a3:
                score += 4.0 if rec_a3 == a3 else -6.0
            if kind == "cities":
                score += math.log10(max(10, rec.get("pop") or 10)) * 0.6 + (1.0 if rec.get("cap") else 0.0)
                where = (rec["lon"], rec["lat"])
                off = _km(lon, lat, *where) if lat is not None else 0.0
            else:
                off = _point_box_km(lon, lat, rec.get("bbox")) if lat is not None else 0.0
            if lat is not None:
                if off > 1500:
                    continue                      # same name, wrong side of the world
                score -= off / 300.0
            if best is None or score > best[0]:
                best = (score, kind, rec)
        if best and best[0] >= 8.5:
            break
    if best:
        return _target(best[1], best[2], name)
    if lat is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        # nothing by that name: trust the coordinates and drop a pin
        return {"level": "point", "lon": lon, "lat": lat, "name": name, "a3": a3 or _country_at(lon, lat),
                "label": [lon, lat], "frame": _box_around(lon, lat, 300)}
    return None


def _box_around(lon, lat, km) -> list:
    dlat = km / 110.57
    dlon = km / (111.32 * max(0.1, math.cos(math.radians(lat))))
    return [_norm_lon(lon - dlon), max(-89.0, lat - dlat), _norm_lon(lon + dlon), min(89.0, lat + dlat)]


def _in_ring(lon, lat, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _feature_has(topo_name: str, obj: str, fid: str, lon: float, lat: float) -> bool:
    for poly in _polygons(_load(topo_name), obj, fid):
        if poly and _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def _country_at(lon, lat) -> str:
    for c in gazetteer()["countries"]:
        if _point_box_km(lon, lat, c.get("bbox")) == 0 and _feature_has("geo_mid", "countries", c["id"], lon, lat):
            return c["id"]
    return ""


def _admin1_at(a3: str, lon: float, lat: float) -> str:
    for a in gazetteer()["admin1"]:
        if a.get("a3") == a3 and _point_box_km(lon, lat, a.get("bbox")) == 0 \
                and _feature_has("geo_mid", "admin1", a["id"], lon, lat):
            return a["id"]
    return ""


def _target(kind: str, rec: dict, spoken: str) -> dict:
    if rec.get("_group"):
        return {"level": "group", "id": rec["id"], "obj": "admin1", "ids": rec["members"], "name": spoken or rec["name"],
                "a3": rec["a3"], "a3s": [rec["a3"]], "label": None, "frame": rec["bbox"]}
    if kind == "cities":
        return {"level": "point", "lon": rec["lon"], "lat": rec["lat"], "name": spoken or rec.get("en") or rec["name"],
                "a3": rec.get("a3"), "pop": rec.get("pop"), "label": [rec["lon"], rec["lat"]],
                "frame": _box_around(rec["lon"], rec["lat"], 300)}
    level = _LEVEL[kind]
    topo_obj = {"countries": ("geo_mid", "countries"), "admin1": ("geo_mid", "admin1")}.get(kind)
    frame = core_frame(topo_obj[0], topo_obj[1], rec["id"], rec.get("bbox")) if topo_obj else rec.get("bbox")
    return {"level": level, "id": rec["id"], "name": spoken or rec.get("en") or rec["name"],
            "a3": rec.get("a3") if kind == "admin1" else (rec["id"] if kind == "countries" else None),
            "label": rec.get("label"), "frame": frame, "km2": rec.get("km2")}


# ════════════════════════════════════════════════════════════════════════════
# LOOK
# ════════════════════════════════════════════════════════════════════════════
SKINS = {
    # The reference look: a teal country floating on near-black, cyan state lines with
    # a glow, the picked place in green cloth, a grey hole where it lifted out.
    "midnight": {
        "kind": "glow",
        "bg": "#040506", "bg2": "#1a1e21", "ocean": "rgba(8,30,40,0.55)", "grid": "rgba(120,210,255,0.045)",
        "land": "#0c7393", "landLight": "#1790b3", "landDark": "#075670",
        "landDim": "rgba(20,110,140,0.16)", "borderDim": "rgba(110,210,245,0.10)",
        "landNear": "rgba(16,98,125,0.62)", "borderNear": "rgba(110,210,245,0.34)",
        "border": "rgba(98,214,250,0.62)", "coast": "#78e2ff", "glow": "rgba(40,190,255,0.55)",
        "hi": "#2f9a62", "hiLight": "#46b97b", "hiDark": "#226f47", "hiEdge": "rgba(170,255,210,0.75)",
        "hiGlow": "rgba(70,235,150,0.50)", "hiWash": "rgba(70,200,130,0.30)",
        "socket": "#7b7f82", "socketLight": "#9a9ea0", "ring": "#ffffff",
        "pin": "#ff5a4e", "pinLight": "#ff8a7a", "route": "#ffc94d", "leader": "rgba(255,255,255,0.85)",
        "plate": "rgba(5,12,16,0.78)", "plateInk": "#ffffff", "icon": "#ffffff",
        "ink": "#ffffff", "inkSoft": "rgba(236,246,250,0.88)", "scrim": "0,0,0",
        "titleShadow": "0 8px 40px rgba(0,0,0,.45)", "subShadow": "0 4px 18px rgba(0,0,0,.6)",
        "vignette": "radial-gradient(ellipse 72% 70% at 50% 48%, transparent 45%, rgba(0,0,0,.62) 100%)",
        "liftShadow": "rgba(0,0,0,0.6)", "liftBody": "rgba(0,0,0,0.9)",
    },
    # An old atlas on stained paper: engraved water lines round the coasts, inked
    # borders, the place in brick red, push pins and red string for journeys.
    "paper": {
        "kind": "paper",
        "bg": "#cfb88c", "bg2": "#efe2c2", "ocean": "rgba(200,180,135,0.45)", "grid": "rgba(96,64,30,0.13)",
        "land": "#e8d6ab", "landLight": "#f1e3bf", "landDark": "#dcc493",
        "landDim": "rgba(222,202,158,0.55)", "borderDim": "rgba(96,70,40,0.28)",
        "landNear": "#e3cfa2", "borderNear": "rgba(88,62,34,0.55)",
        "border": "rgba(92,66,38,0.55)", "coast": "#4a3522", "glow": "rgba(0,0,0,0)",
        "waterLine": "rgba(92,68,40,0.55)",
        "hi": "#a63d2a", "hiLight": "#bf5139", "hiDark": "#86301f", "hiEdge": "rgba(58,24,12,0.8)",
        "hiGlow": "rgba(60,30,10,0.35)", "hiWash": "rgba(166,61,42,0.26)",
        "socket": "#c9b287", "socketLight": "#d8c49c", "ring": "rgba(166,61,42,0.45)",
        "pin": "#c0392b", "pinLight": "#ec7a68", "route": "#b3261e", "leader": "rgba(58,40,22,0.85)",
        "plate": "#f4ead2", "plateInk": "#2b2016", "icon": "#2b2016",
        "ink": "#2b2016", "inkSoft": "rgba(58,44,30,0.92)", "scrim": "239,227,197",
        "titleShadow": "0 2px 0 rgba(255,246,222,0.55)", "subShadow": "none",
        "vignette": "radial-gradient(ellipse 76% 72% at 50% 48%, transparent 58%, rgba(80,50,20,.26) 100%)",
        "liftShadow": "rgba(40,28,14,0.40)", "liftBody": "rgba(40,28,14,0.45)",
    },
    # The clean infographic: white land with a soft shadow on light grey, one strong
    # blue for the place. Reads like a news explainer.
    "clean": {
        "kind": "flat",
        "bg": "#dfe5ec", "bg2": "#f6f8fa", "ocean": "rgba(214,223,233,0.85)", "grid": "rgba(20,40,70,0.05)",
        "land": "#ffffff", "landLight": "#ffffff", "landDark": "#eef2f6",
        "landDim": "rgba(255,255,255,0.55)", "borderDim": "rgba(140,155,175,0.35)",
        "landNear": "#fafbfc", "borderNear": "rgba(140,155,175,0.7)",
        "border": "rgba(150,165,185,0.8)", "coast": "#8c9bb0", "glow": "rgba(0,0,0,0)",
        "hi": "#2563eb", "hiLight": "#3b82f6", "hiDark": "#1d4ed8", "hiEdge": "rgba(255,255,255,0.95)",
        "hiGlow": "rgba(37,99,235,0.35)", "hiWash": "rgba(37,99,235,0.18)",
        "socket": "#d3dae3", "socketLight": "#e2e7ed", "ring": "rgba(37,99,235,0.22)",
        "pin": "#ef4444", "pinLight": "#f87171", "route": "#2563eb", "leader": "rgba(17,24,39,0.7)",
        "plate": "#ffffff", "plateInk": "#111827", "icon": "#0f172a",
        "ink": "#0f172a", "inkSoft": "rgba(51,65,85,0.95)", "scrim": "246,248,250",
        "titleShadow": "none", "subShadow": "none",
        "vignette": "radial-gradient(ellipse 80% 80% at 50% 45%, transparent 60%, rgba(30,45,70,.10) 100%)",
        "liftShadow": "rgba(30,50,90,0.28)", "liftBody": "rgba(30,50,90,0.16)",
    },
}
# A channel that never says which map look it wants gets the one closest to its graphics.
SKIN_FOR_GROUND = {"paper": "paper", "collage": "paper", "divine": "paper", "clean": "clean"}


def palette(skin: str = "midnight", style: str = "", colors: dict = None) -> dict:
    """A map skin in the channel's type. Colours are the skin's own unless the style
    file overrides some of them (look.maps.colors, carried in the scene as `colors`)."""
    pal = dict(SKINS.get(skin) or SKINS["midnight"])
    pal.update({k: str(v) for k, v in (colors or {}).items() if k in pal})
    font, weight = "'Inter Display Black','Inter Black',Inter,sans-serif", "900"
    try:
        import motion
        b = motion.BRAND.get(style or "")
        if b:
            font, weight = b.get("title_font") or font, str(b.get("title_weight") or weight)
    except Exception:                                   # noqa: BLE001 - the look still renders
        pass
    pal.update({"font": font, "weight": weight, "subFont": "'Inter Semi Bold',Inter,sans-serif"})
    return pal


# ════════════════════════════════════════════════════════════════════════════
# SCENES
# ════════════════════════════════════════════════════════════════════════════
SHOTS = ("region", "pin", "route", "multi")
ICONS = {"plane": ("plane", True, 0), "flight": ("plane", True, 0), "ship": ("ship", False, 0),
         "boat": ("sailboat", False, 0), "sail": ("sailboat", False, 0), "car": ("car-side", False, 0),
         "train": ("train", False, 0), "walk": ("person-walking", False, 0), "army": ("person-military-rifle", False, 0),
         "horse": ("horse", False, 0), "truck": ("truck", False, 0)}


def _a1_count(a3: str) -> int:
    return sum(1 for a in gazetteer()["admin1"] if a.get("a3") == a3)


def _visible_km(frame, spread: float = 1.0) -> float:
    """Roughly how wide the view is once `frame` is fitted into the shot."""
    wkm, hkm = _box_km(frame)
    return max(wkm, hkm * 1.78) * spread


def _geo(frames_hi: list, frames_mid: list, near_km: float, far_km: float, a1_of: list, keep_c: set,
         keep_a1: set, extra: dict) -> dict:
    """Cut each level of detail down to what this camera can see.

    near_km: how wide the closest view is; far_km: the widest. The hi level is only
    shipped when the camera gets close enough to need it, the world outline only when
    it pulls back far enough to show the globe."""
    g = gazetteer()
    out = {"lo": _load("geo_lo") if far_km > 16000 else None, "mid": None, "hi": None}

    def cut(level, boxes):
        hit = lambda b: b and any(_box_hits(b, x) for x in boxes)
        cids = {c["id"] for c in g["countries"] if hit(c.get("bbox"))} | keep_c
        aids = {a["id"] for a in g["admin1"] if a.get("a3") in a1_of and hit(a.get("bbox"))} | keep_a1
        return _subset(_load(f"geo_{level}"), {"countries": cids, "admin1": aids})

    if near_km < 7500 and frames_hi:
        out["hi"] = cut("hi", frames_hi)
    if far_km > 5000 or out["hi"] is None:
        out["mid"] = cut("mid", frames_mid)
    if extra.get("water"):
        out["water"] = _subset(_load("water"), {"water": set(extra["water"])})
    if extra.get("regions"):
        out["regions"] = _subset(_load("regions"), {"regions": set(extra["regions"])})
    return out


def _composites(targets: list) -> dict:
    return {t["id"]: {"obj": t["obj"], "ids": t["ids"]} for t in targets if t.get("ids")}


def scene_for(targets: list, shot: str = "", title: str = "", subtitle: str = "", duration: float = 7.0,
              hit: float = 1.5, style: str = "", skin: str = "midnight", seed: int = 7, icon: str = "",
              lang: str = "", colors: dict = None) -> dict:
    """The renderer's scene for already-resolved targets. The shot is repaired rather
    than refused: one city is a pin, one area a region, two or more a multi or a route."""
    targets = [t for t in targets if t]
    if not targets:
        return None
    one = len(targets) == 1
    if shot == "route" and len(targets) < 2:
        shot = ""
    if shot not in SHOTS or (shot == "multi" and one):
        shot = ("pin" if targets[0]["level"] == "point" else "region") if one else "multi"
    if shot == "region" and targets[0]["level"] == "point":
        shot = "pin"
    if shot == "pin" and targets[0]["level"] != "point":
        shot = "region"
    sc = {"shot": shot, "duration": float(duration), "hit": float(hit), "seed": int(seed),
          "title": title if title is not None else targets[0]["name"], "subtitle": subtitle or "",
          "skin": skin, "brand": style, "lang": lang or "", "colors": dict(colors or {})}
    build = {"region": _region, "pin": _pin, "route": _route, "multi": _multi}[shot]
    sc.update(build(targets, sc, icon))
    return sc


def _region(targets, sc, icon):
    t = dict(targets[0])
    level = t["level"]
    extra, keep_c, keep_a1 = {}, set(), set()
    if level == "admin1" or (level == "group" and t["obj"] == "admin1"):
        a3 = t["a3"]
        cframe = core_frame("geo_mid", "countries", a3, None) or _box_grow(t["frame"], 3.0, 12)
        land, a1_of, near = [a3], [a3], False
        keep_a1 = set(t["ids"]) if level == "group" else {t["id"]}
        # a state in a huge country still starts close enough to read
        if _visible_km(cframe) > 9000:
            cframe = _box_grow(t["frame"], 4.0, 20)
    elif level in ("country", "group"):
        ids = t["ids"] if level == "group" else [t["id"]]
        cframe = _box_grow(t["frame"], 3.2, 26)
        land, near = list(ids), True
        a1_of = [ids[0]] if level == "country" and 1 < _a1_count(ids[0]) <= 40 else []
        keep_c = set(ids)
    else:                                   # a sea, a desert, a mountain range
        cframe = _box_grow(t["frame"], 2.2, 20)
        land, a1_of, near = [], [], True
        extra = {"water" if level == "water" else "regions": t.get("ids") or [t["id"]]}
    near_km, far_km = _visible_km(t["frame"], 2.4), _visible_km(cframe, 1.15)
    geo = _geo([_box_grow(t["frame"], 3.4), cframe if not near else _box_grow(t["frame"], 2.0)],
               [_box_grow(cframe, 1.6)], near_km, far_km, a1_of, keep_c, keep_a1, extra)
    return {"targets": [t], "context": {"frame": cframe}, "land": land, "admin1_of": a1_of, "near": near,
            "geo": geo, "composites": _composites([t])}


def _pin(targets, sc, icon):
    t = dict(targets[0])
    a3 = t.get("a3") or _country_at(t["lon"], t["lat"])
    cframe = core_frame("geo_mid", "countries", a3, None) if a3 else None
    if cframe is None or _visible_km(cframe) > 5000:
        cframe = _box_around(t["lon"], t["lat"], 1800)
    wkm, _ = _box_km(cframe)
    # close enough to read the city's surroundings, far enough to still see the country's shape
    t["frame"] = _box_around(t["lon"], t["lat"], max(170.0, min(650.0, wkm * 0.24)))
    a1_of = [a3] if a3 and _a1_count(a3) <= 60 else []
    area = _admin1_at(a3, t["lon"], t["lat"]) if a1_of else ""
    if area:
        akm2 = (gazetteer()["_by_id"][area].get("km2") or 0)
        fw, fh = _box_km(t["frame"])
        if not (0.004 < akm2 / max(1.0, fw * fh) < 0.35):
            area = ""                        # a speck or most of the view: no wash
    t["area"] = area or None
    near_km, far_km = _visible_km(t["frame"], 2.0), _visible_km(cframe, 1.15)
    geo = _geo([_box_grow(t["frame"], 3.0), cframe], [_box_grow(cframe, 1.8)], near_km, far_km, a1_of,
               {a3} if a3 else set(), {area} if area else set(), {})
    return {"targets": [t], "context": {"frame": cframe}, "land": [a3] if a3 else [], "admin1_of": a1_of,
            "near": False, "geo": geo, "composites": {}}


def _route(targets, sc, icon):
    stops = []
    for t in targets:
        lon, lat = (t["lon"], t["lat"]) if t["level"] == "point" else tuple(t.get("label") or boxc(t["frame"]))
        stops.append({"name": t.get("name", ""), "lon": lon, "lat": lat, "via": bool(t.get("via"))})
        if t.get("at") is not None:
            stops[-1]["at"] = float(t["at"])      # the second this stop is named, when known
    frame = _box_union([_box_around(s["lon"], s["lat"], 60) for s in stops])
    frame = _box_grow(frame, 1.35, 6)
    first = _box_grow(_box_around(stops[0]["lon"], stops[0]["lat"], 60), 1.0, max(4.0, _box_span(frame)[1] - _box_span(frame)[0]) * 0.4)
    land = sorted({t.get("a3") for t in targets if t.get("a3")})
    near_km, far_km = _visible_km(frame, 1.3), _visible_km(frame, 1.5)
    geo = _geo([_box_grow(frame, 1.8)], [_box_grow(frame, 2.2)], near_km, far_km, [], set(land), set(), {})
    out = {"stops": stops, "frame": frame, "first_frame": first, "land": land, "admin1_of": [], "near": True,
           "geo": geo, "targets": [], "composites": {}}
    name, rotate, angle = ICONS.get(_fold(icon), ("", False, 0))
    if name:
        try:
            import motion
            got = motion.fa_icon(name)
            if got:
                out.update({"icon": got, "icon_rotate": rotate, "icon_angle": angle})
        except Exception:                               # noqa: BLE001 - a glowing head instead
            pass
    return out


def boxc(b) -> list:
    w, e = _box_span(b)
    return [_norm_lon((w + e) / 2), (b[1] + b[3]) / 2]


def _multi(targets, sc, icon):
    for t in targets:
        if not t.get("label"):
            t["label"] = boxc(t["frame"])
    # Places too far apart for one view (Brazil and Japan) become a tour: the camera
    # flies from each to the next across the globe instead of shrinking them to specks.
    far = max((_km(a["label"][0], a["label"][1], b["label"][0], b["label"][1])
               for a in targets for b in targets), default=0.0)
    if far > 6500:
        frames = [_box_grow(t["frame"], 1.7, 14) for t in targets]
        land = sorted({t["id"] for t in targets if t["level"] == "country"})
        extra = {"water": [i for t in targets if t["level"] == "water" for i in (t.get("ids") or [t["id"]])],
                 "regions": [i for t in targets if t["level"] == "region" for i in (t.get("ids") or [t["id"]])]}
        geo = _geo([_box_grow(f, 1.6) for f in frames], [_box_grow(f, 2.2) for f in frames],
                   min(_visible_km(f, 1.3) for f in frames), 40000, [], set(land), set(), extra)
        return {"targets": targets, "frames": frames, "tour": True, "frame": frames[0], "land": [],
                "admin1_of": [], "near": True, "geo": geo, "composites": _composites(targets)}
    frame = _box_grow(_box_union([t["frame"] for t in targets]), 1.25, 8)
    land = sorted({i for t in targets if t["level"] == "country" for i in [t["id"]]} |
                  {i for t in targets if t["level"] == "group" and t["obj"] == "countries" for i in t["ids"]})
    extra = {"water": [i for t in targets if t["level"] == "water" for i in (t.get("ids") or [t["id"]])],
             "regions": [i for t in targets if t["level"] == "region" for i in (t.get("ids") or [t["id"]])]}
    keep_a1 = {t["id"] for t in targets if t["level"] == "admin1"} | \
              {i for t in targets if t["level"] == "group" and t["obj"] == "admin1" for i in t["ids"]}
    near_km, far_km = _visible_km(frame, 1.3), _visible_km(frame, 1.6)
    geo = _geo([_box_grow(frame, 1.8)], [_box_grow(frame, 2.4)], near_km, far_km, [],
               set(land), keep_a1, extra)
    return {"targets": targets, "frame": frame, "land": [], "admin1_of": [], "near": True, "geo": geo,
            "composites": _composites(targets)}


# ════════════════════════════════════════════════════════════════════════════
# PAGE + RENDER
# ════════════════════════════════════════════════════════════════════════════
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>__FONTS__
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px;overflow:hidden;background:__BG__}
#stage{position:relative;width:__W__px;height:__H__px;overflow:hidden}
canvas{position:absolute;left:0;top:0;width:__W__px;height:__H__px}
#base,#lift{will-change:filter}
.tx{position:absolute;color:__INK__;line-height:1.0;white-space:nowrap}
#title{letter-spacing:-1px}
#sub{white-space:normal;line-height:1.22;color:__INKSOFT__;font-weight:600}
#vig{position:absolute;inset:0;pointer-events:none;background:__VIG__}
#scrim{position:absolute;inset:0;pointer-events:none;opacity:0}
</style></head><body>
<div id="stage">
  <canvas id="ground"></canvas>
  <canvas id="base"></canvas>
  <canvas id="lift"></canvas>
  <canvas id="fx"></canvas>
  <div id="vig"></div>
  <div id="scrim"></div>
  <div id="title" class="tx"></div>
  <div id="sub" class="tx"></div>
</div>
<script>__LIBS__</script>
<script>const SCENE=__SCENE__;const PAL=__PAL__;const W=__W__,H=__H__;</script>
<script>__MAPJS__</script>
</body></html>"""

_FONT_CSS: dict = {}


def _font_css(stacks: str) -> str:
    """@font-face rules, base64-inlined, for the bundled faces these CSS stacks name.

    Chromium only sees fonts installed on the machine, and Inter is on neither a stock
    Mac nor a stock Windows. Only the faces a map actually uses are embedded — all of
    assets/fonts is 2.7 MB of base64 in every page."""
    import base64
    wanted = " ".join(re.sub(r"[^a-z ]", " ", stacks.lower()).split())
    if wanted in _FONT_CSS:
        return _FONT_CSS[wanted]
    out = []
    for f in sorted((HERE / "assets" / "fonts").glob("*.[ot]tf")):
        fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f.stem).replace("-", " ")
        if f" {fam.lower()} " not in f" {wanted} ":
            continue
        b64 = base64.b64encode(f.read_bytes()).decode()
        mime = "font/otf" if f.suffix == ".otf" else "font/ttf"
        out.append(f"@font-face{{font-family:'{fam}';font-weight:1 1000;"
                   f"src:url(data:{mime};base64,{b64});font-display:block}}")
    _FONT_CSS[wanted] = "".join(out)
    return _FONT_CSS[wanted]


def build_html(scene: dict) -> str:
    pal = palette(scene.get("skin", "midnight"), scene.get("brand", ""), scene.get("colors"))
    libs = "\n".join((MAPS / "lib" / n).read_text(encoding="utf-8")
                     for n in ("d3-array.min.js", "d3-geo.min.js", "topojson-client.min.js"))
    return (_PAGE
            .replace("__FONTS__", _font_css(pal["font"] + " , " + pal["subFont"]))
            .replace("__LIBS__", libs)
            .replace("__SCENE__", json.dumps(scene, separators=(",", ":")))
            .replace("__PAL__", json.dumps(pal))
            .replace("__MAPJS__", (MAPS / "map.js").read_text(encoding="utf-8"))
            .replace("__VIG__", pal["vignette"])
            .replace("__BG__", pal["bg"]).replace("__INKSOFT__", pal["inkSoft"]).replace("__INK__", pal["ink"])
            .replace("__W__", str(W)).replace("__H__", str(H)))


def render(jobs: list, workers: int = 3, fps: int = FPS, keep_html: bool = False) -> list:
    """jobs = [(scene, out_mp4)]. Same frame-by-frame Chromium loop as motion.py."""
    import motion
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="maps_"))
    prepared = []
    for i, (sc, out) in enumerate(jobs):
        html = build_html(sc)
        if keep_html:
            Path(out).with_suffix(".html").write_text(html, encoding="utf-8")
        prepared.append((html, float(sc.get("duration", 7)), Path(out), tmp / f"frames_{i:03d}"))
    workers = max(1, min(workers, len(prepared)))
    chunks = [prepared[i::workers] for i in range(workers)]

    def one(chunk):
        last = None
        for attempt in range(4):
            pw = None
            try:
                with motion._PW_START:
                    pw = sync_playwright().start()
                browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu",
                                                   "--font-render-hinting=none"])
                for html, dur, out, fdir in chunk:
                    if out.exists() and out.stat().st_size > 10_000:
                        continue                         # a retry resumes, it does not redo
                    fdir.mkdir(parents=True, exist_ok=True)
                    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.set_content(html, wait_until="load")
                    try:
                        page.wait_for_function("window.__ready===true", timeout=60000)
                    except Exception as e:              # noqa: BLE001
                        raise RuntimeError(f"map page for {out.name} never got ready: {errors[:3] or e}")
                    n = max(1, int(round(dur * fps)))
                    for k in range(n):
                        page.evaluate("(t)=>window.renderFrame(t)", k / fps)
                        page.screenshot(path=str(fdir / f"f_{k:05d}.jpg"), type="jpeg", quality=93,
                                        clip={"x": 0, "y": 0, "width": W, "height": H})
                    errors += page.evaluate("window.__errors || []")
                    if errors:
                        print(f"  maps: {out.name}: {len(errors)} frame error(s), first: {str(errors[0])[:300]}")
                    motion._frames_to_mp4(fdir, out, fps)
                    shutil.rmtree(fdir, ignore_errors=True)
                    page.close()
                browser.close()
                return
            except Exception as e:                      # noqa: BLE001 - driver flakiness, as in motion.py
                last = e
                time.sleep(3.0 * (attempt + 1))
            finally:
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:                   # noqa: BLE001
                        pass
        raise RuntimeError(f"maps: rendering failed after 4 tries: {last}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, chunks))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [Path(o) for _, o in jobs]


# ════════════════════════════════════════════════════════════════════════════
# TIMELINE — find the moments, time them to the word, drop them into the video
# ════════════════════════════════════════════════════════════════════════════
MAP_DUR = 7.0              # a region or a pin
MAP_MAX_DUR = 11.0         # a journey may run longer, never more
LEAD_S = 1.6               # the camera flies in for this long, and arrives on the word
MAP_EVERY_S = float(os.environ.get("MAPS_EVERY_S", "35"))   # at most one map per this many seconds, on average
MAP_GAP_S = float(os.environ.get("MAPS_GAP_S", "15"))       # never two maps closer than this
PROTECTED = ("introcap", "map_", "headline_")                # scenes placed on a word: never removed by another DLC

MOMENTS_PROMPT = """You are the map designer for a documentary-style YouTube video. Whenever the
narration names a place that matters to the story, the edit cuts to an animated map of it:
the real borders, the place lit up, its name on screen.

Below is the full voiceover as timed subtitles, one cue per line: #number [minute:second] text.

Pick the moments where a map makes the story clearer:
- where something happened, where someone was born, went, fled, fought, settled or ended up;
- a journey from one place to another (a route);
- several places named together that belong on one map;
- the size, shape or position of a place being part of the point.

Never a map for:
- a figure of speech ("Rome wasn't built in a day"), a brand or a person's name (Amazon, Paris Hilton);
- vague areas with no borders ("the West", "the world", "overseas") unless a real group of countries is meant;
- a place already mapped earlier in the video, unless the story returns to it much later for a new reason.

At most [INSERT MAX HERE] moments, and at least [INSERT GAP HERE] seconds apart. If the video has
fewer good moments, return fewer — an empty array is a correct answer for a video that never
really goes anywhere. Prefer the moment a place first becomes important.

Return a JSON array. One object per moment:
{
  "cue": 42,
  "words": "in California",
  "shot": "region",
  "places": [{"name": "California", "names": ["California"], "label": "California", "type": "state",
              "country": "United States", "lat": 36.78, "lon": -119.42}],
  "title": "California",
  "subtitle": "became the 31st U.S. state in 1850",
  "icon": ""
}
A route looks like this:
{"cue": 3, "words": "left Lisbon", "end_cue": 8, "end_words": "at Calicut", "shot": "route",
 "places": [{"name": "Lisbon", "type": "city", "country": "Portugal", "lat": 38.72, "lon": -9.14},
            {"name": "off West Africa", "type": "point", "lat": 5.0, "lon": -15.0, "via": true},
            {"name": "Cape of Good Hope", "type": "point", "lat": -35.2, "lon": 18.5, "via": true},
            {"name": "Calicut", "names": ["Calicut", "Kozhikode"], "label": "Calicut", "type": "city",
             "country": "India", "lat": 11.25, "lon": 75.78}],
 "title": "Calicut", "subtitle": "the sea route to India, 1498", "icon": "ship"}

Field rules:
- "cue": the number of the cue in which the place is SPOKEN.
- "words": the place name exactly as it is written in that cue, copied letter for letter
  (keep the language and the grammatical form: "v Kalifornii" stays "v Kalifornii").
- A route also gives "end_cue" and "end_words": where the destination is said. Its "cue" and
  "words" are where the journey starts being told (the departure, or the verb of travel), so the
  line draws while the voice travels and lands on the destination.
- In a multi, every place also gets its own "cue" and "words", so each lights up as it is named.
- "shot": "region" for one country, state, province, sea, ocean, desert or mountain range;
  "pin" for one city, town or exact spot; "route" for a journey, places in travel order;
  "multi" for 2 to 5 places named together.
- "places": "name" and "names" always in ENGLISH, whatever the video's language — they are
  looked up in an atlas. "label" is the same place as it is PRINTED on the map, in
  [INSERT LANGUAGE HERE], in its dictionary form (nominative), e.g. "Moskva", never "Moskvy".
  "type" is one of: country, state, province, region, city, town, sea, ocean, gulf, strait,
  lake, desert, mountains, peninsula, island, continent, group, point (an exact spot by lat/lon).
  "country" is the modern country the place is in (English name) — leave it out for a country or a sea.
  "names": the English name first, then the local spelling if it differs ("Tuscany", "Toscana").
  "lat"/"lon": your best estimate of the place's centre — it is used to catch a wrong match.
  A historical or informal area (the Soviet Union, the Roman Empire at its height, Scandinavia,
  the Balkans, Moravia) is type "group" with "members": a list of the modern countries (or
  regions) it covers, each written as a place object.
  A sea route adds in-between points out AT SEA, type "point", marked "via": true, so the line
  never crosses land: a voyage from Lisbon to India needs a point off West Africa and one south of
  the Cape of Good Hope; put them a few hundred kilometres offshore, never on the coast. A land or
  air route does not need them.
- "title": the place as the viewer knows it, 1 to 3 words, in [INSERT LANGUAGE HERE]. For a route,
  the destination or the journey's name.
- "subtitle": at most 9 words in [INSERT LANGUAGE HERE] — a concrete fact the narration states right
  there (a year, what happened, a number it says). Never add a fact the narration does not say,
  and never pair a date or a number with a different event than the one the narration ties it to.
  Empty string if there is nothing concrete.
- "icon": route only — plane, ship, boat, car, train, walk, army or horse. Empty otherwise.

TRANSCRIPT:
[INSERT TRANSCRIPT HERE]
"""


def _stamp(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _map_settings(style: str, mv) -> dict:
    look = ((getattr(mv, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
    cfg = look.get("maps") if isinstance(look.get("maps"), dict) else {}
    return {
        "on": look.get("maps") is not False and cfg.get("enabled", True) is not False,
        "skin": cfg.get("skin") if cfg.get("skin") in SKINS
                else SKIN_FOR_GROUND.get(str((look.get("skins") or ["glass"])[0]), "midnight"),
        "every_s": float(cfg.get("every_s") or MAP_EVERY_S),
        "gap_s": float(cfg.get("gap_s") or MAP_GAP_S),
        "colors": {str(k): str(v) for k, v in (cfg.get("colors") or {}).items()} if isinstance(cfg.get("colors"), dict) else {},
    }


def _word_time(words: str, cue: tuple, mv) -> float:
    """When inside its cue the place is said. SRT only times the whole cue, so the
    engine's own share-out by word length is used — the same one the intro captions use."""
    st, en, txt = cue
    timed = mv._cue_words(txt, st, en)
    want = [_fold(w) for w in str(words or "").split() if _fold(w)]
    if not timed or not want:
        return st
    folded = [_fold(w["w"]) for w in timed]
    # the place name is usually the LAST word of "words" ("in California"); find the run
    for i in range(len(folded)):
        if folded[i:i + len(want)] == want:
            j = i + len(want) - 1
            return float(timed[j if len(want) <= 3 else i]["t"])
    for i, f in enumerate(folded):
        if f and (f == want[-1] or f.startswith(want[-1][:5])):
            return float(timed[i]["t"])
    return st


def _find_cue(entries: list, n: int, words: str) -> int:
    """The cue that really holds `words`: the model's number, or the nearest one that does."""
    key = _fold(words)
    if not key:
        return n if 0 <= n < len(entries) else -1
    order = sorted(range(max(0, n - 6), min(len(entries), n + 7)), key=lambda i: abs(i - n))
    for i in order:
        if key in _fold(entries[i][2]):
            return i
    # split across two cues
    for i in order:
        if i + 1 < len(entries) and key in _fold(entries[i][2] + " " + entries[i + 1][2]):
            return i
    return -1


def plan_moments(srt: Path, job: Path, style: str, force: bool, total: float, mv) -> list:
    """Claude reads the subtitles and names the moments a map belongs on screen.
    The raw answer is cached in <job>/maps/moments.json."""
    out_dir = job / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "moments.json"
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    cfg = _map_settings(style, mv)
    entries = mv._parse_srt_full(srt.read_text(encoding="utf-8"))
    transcript = "\n".join(f"#{i + 1} [{_stamp(st)}] {txt}" for i, (st, en, txt) in enumerate(entries))
    most = max(3, int(total / cfg["every_s"]))
    prompt = (MOMENTS_PROMPT.replace("[INSERT MAX HERE]", str(most))
              .replace("[INSERT GAP HERE]", str(int(cfg["gap_s"])))
              .replace("[INSERT LANGUAGE HERE]", mv._job_language(job, style))
              .replace("[INSERT TRANSCRIPT HERE]", transcript))
    mv.log(f"maps: Claude is reading the narration for places ({len(entries)} cues, up to {most} maps)...")
    moments = mv._json_items(prompt, max_tokens=max(4000, most * 450))
    moments = [m for m in moments if isinstance(m, dict)]
    cache.write_text(json.dumps(moments, indent=2, ensure_ascii=False), encoding="utf-8")
    return moments


def build_scenes(moments: list, srt: Path, job: Path, style: str, total: float, mv,
                 not_before: float = 0.0) -> list:
    """[(start, scene)] — every moment resolved to real geography and timed to its word.
    Moments whose place cannot be found, or that crowd the one before, are dropped with a note."""
    cfg = _map_settings(style, mv)
    entries = mv._parse_srt_full(srt.read_text(encoding="utf-8"))
    lang = mv._job_language(job, style)
    seed0 = abs(hash(job.name)) % 997
    timed = []
    for m in moments:
        try:
            n = int(m.get("cue") or 0) - 1
        except (TypeError, ValueError):
            n = -1
        i = _find_cue(entries, n, m.get("words") or "")
        if i < 0:
            first = next(iter(m.get("places") or []), {}) or {}
            i = _find_cue(entries, n, first.get("name") or "")
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
            mv.log(f"  maps: skipped — could not find {m.get('words')!r} near cue {n + 1}")
            continue
        at = _t if _t is not None else _word_time(m.get("words") or "", entries[i], mv)
        m = dict(m)
        shot = str(m.get("shot") or "").lower()
        if shot == "route" and m.get("end_words"):
            try:
                ne = int(m.get("end_cue") or 0) - 1
            except (TypeError, ValueError):
                ne = -1
            j = _find_cue(entries, ne if ne >= 0 else i, m.get("end_words") or "")
            if j >= i:
                m["_end"] = _word_time(m.get("end_words") or "", entries[j], mv)
        if shot == "multi":
            for p in m.get("places") or []:
                if isinstance(p, dict) and p.get("words"):
                    try:
                        pn = int(p.get("cue") or 0) - 1
                    except (TypeError, ValueError):
                        pn = i
                    pj = _find_cue(entries, pn if pn >= 0 else i, p["words"])
                    if pj >= 0:
                        p["_at"] = _word_time(p["words"], entries[pj], mv)
        timed.append((at, m))
    timed.sort(key=lambda x: x[0])

    out, last_end = [], -1e9
    for k, (at, m) in enumerate(timed):
        places = [p for p in (m.get("places") or []) if isinstance(p, dict)]
        shot = str(m.get("shot") or "").lower()
        targets = []
        for p in places:
            if p.get("via") and p.get("lat") is not None and p.get("lon") is not None:
                # a waypoint is a spot at sea: the designer's coordinates ARE the point
                try:
                    targets.append({"level": "point", "lon": float(p["lon"]), "lat": float(p["lat"]), "via": True,
                                    "name": "", "label": [float(p["lon"]), float(p["lat"])],
                                    "frame": _box_around(float(p["lon"]), float(p["lat"]), 100)})
                except (TypeError, ValueError):
                    pass
                continue
            t = resolve(p)
            if t:
                t = dict(t, via=False, name=p.get("label") or p.get("name") or t.get("name"))
                if p.get("_at") is not None:
                    t["_at"] = p["_at"]
                targets.append(t)
            else:
                mv.log(f"  maps: no map for {p.get('name')!r} ({p.get('type')}) — not in the atlas")
        if not [t for t in targets if not t.get("via")]:
            continue
        hit_at, draw = at, 0.0
        if shot == "route":
            end = m.get("_end") or 0.0
            span = end - at if end > at else 3.5
            if span > 8.0:                        # a long telling: draw the last eight seconds of it
                hit_at, span = end - 8.0, 8.0
            draw = max(2.6, span)
            dur = min(MAP_MAX_DUR, LEAD_S + draw + 2.4)
        elif shot == "multi":
            last = max([t.get("_at") or 0.0 for t in targets] + [at])
            dur = min(10.0, max(6.0, (last - at) + LEAD_S + 2.8, 6.0 + 0.6 * len(targets)))
        else:
            dur = MAP_DUR
        if m.get("dur"):
            # the director's plan says how long it has (a map before the next scene on the next sentence)
            dur = min(dur, max(4.0, float(m["dur"])))
        if hit_at < not_before + 0.8:
            mv.log(f"  maps: skipped {m.get('title')!r} at {_stamp(at)} — inside the opening")
            continue
        start = max(not_before, hit_at - LEAD_S)
        if start < last_end + cfg["gap_s"]:
            mv.log(f"  maps: skipped {m.get('title')!r} at {_stamp(at)} — too close to the map before it")
            continue
        # A clip that ends a breath before the video does would leave a sliver the
        # timeline fills by LOOPING the clip — so a map near the end runs to the end.
        if total - (start + dur) < 3.0:
            dur = total - start
        if dur < (4.0 if m.get("dur") else 4.5) or dur > MAP_MAX_DUR + 3.0:
            continue
        for t in targets:
            if t.get("_at") is not None:
                # a place named in the last seconds still gets time on screen: it lights a
                # beat early rather than flashing up as the scene fades
                t["hit"] = round(min(max(0.3, t.pop("_at") - start), dur - 2.6), 3)
        sc = scene_for(targets, shot, str(m.get("title") or targets[-1].get("name") or ""),
                       str(m.get("subtitle") or "")[:90], dur, hit_at - start, style=style, skin=cfg["skin"],
                       seed=seed0 + k * 31, icon=str(m.get("icon") or ""),
                       lang="tr" if "turk" in lang.lower() else "", colors=cfg["colors"])
        if not sc:
            continue
        if draw:
            sc["draw_s"] = round(draw, 3)
        out.append((round(start, 3), sc))
        last_end = start + dur
    return out


def add_to_timeline(segs: list, srt: Path, job: Path, style: str, force: bool, total: float = 0.0,
                    workers: int = 3, engine=None) -> list:
    """The MAPS DLC's one entry point from the engine.

    `segs` is the video's graphics timeline, [(start, mp4, duration)]. Map scenes are
    added at the words they belong to; a regular graphic that would collide with one
    gives way, because a map is tied to a word and a graphic is not. The opening
    caption never gives way — a map that would land inside it is dropped instead.

    `engine` is make_video itself, handed in: run as `python make_video.py`, importing
    it here would load a second copy with none of the channels applied."""
    if engine is None:
        import make_video as engine
    mv = engine
    if not installed() or not srt.exists():
        return segs
    cfg = _map_settings(style, mv)
    if not cfg["on"]:
        return segs
    total = float(total or mv._audio_dur(job / "audio.mp3"))
    intro_end = 0.0
    for st, path, dur in segs:
        if Path(path).name.startswith("introcap"):
            intro_end = max(intro_end, float(st) + float(dur))
    # scenes another DLC put on a word (a headline): a map never removes one, nor lands on it
    blocked = [(float(st), float(st) + float(dur)) for st, path, dur in segs
               if Path(path).name.startswith(PROTECTED) and not Path(path).name.startswith("introcap")]
    moments = plan_moments(srt, job, style, force, total, mv)
    # a directed video (director.py) spaced its scenes itself: only a real collision counts, or its graphics vanish
    pad = 0.0 if (job / "director.json").exists() else 3.0
    # three seconds of footage after the opening: a shorter gap is filled by looping a clip
    scenes = build_scenes(moments, srt, job, style, total, mv, not_before=intro_end + 3.0 if intro_end else 0.0)
    scenes = [(st, sc) for st, sc in scenes
              if not any(st < b1 + pad and b0 - pad < st + float(sc["duration"]) for b0, b1 in blocked)]
    if not scenes:
        mv.log("maps: no place in this narration needs a map")
        return segs
    out_dir = job / "maps"
    jobs, placed = [], []
    for i, (start, sc) in enumerate(scenes):
        mp4 = out_dir / f"map_{i:02d}.mp4"
        meta = out_dir / f"map_{i:02d}.json"
        stamp = json.dumps({k: sc.get(k) for k in ("shot", "title", "subtitle", "duration", "hit", "targets", "stops",
                                                    "skin", "brand", "colors", "draw_s")},
                           sort_keys=True, default=str)
        if force or not mp4.exists() or not meta.exists() or meta.read_text(encoding="utf-8") != stamp:
            mp4.unlink(missing_ok=True)
            meta.write_text(stamp, encoding="utf-8")
            jobs.append((sc, mp4))
        placed.append((start, mp4, float(sc["duration"])))
    for stale in out_dir.glob("map_*.mp4"):
        if stale not in [p for _, p, _ in placed]:
            stale.unlink(missing_ok=True)
    if jobs:
        mv.log(f"maps: rendering {len(jobs)} map scene(s) in Chromium...")
        render(jobs, workers=workers)
    placed = [p for p in placed if p[1].exists()]

    kept = []
    for st, path, dur in segs:
        a, b = float(st), float(st) + float(dur)
        # keep 3 s of footage either side of a map, or the timeline squeezes a sliver in
        if any(a < ms + md + pad and ms - pad < b for ms, _, md in placed) and not Path(path).name.startswith(PROTECTED):
            continue
        kept.append((st, path, dur))
    merged = sorted(kept + [(s, str(p), d) for s, p, d in placed], key=lambda x: float(x[0]))
    names = ", ".join(f"{_stamp(s)} {sc.get('title')}" for s, sc in scenes[:6])
    mv.log(f"maps: {len(placed)} map scene(s) — {names}{' ...' if len(scenes) > 6 else ''}")
    return merged


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════
DEMOS = [
    ("region_california", "region", [{"name": "California", "type": "state", "country": "USA"}],
     "California", "officially became the 31st U.S. state on September 9, 1850", ""),
    ("region_czechia", "region", [{"name": "Czechia", "type": "country"}], "Czechia", "the heart of Europe", ""),
    ("pin_tokyo", "pin", [{"name": "Tokyo", "type": "city", "country": "Japan"}], "Tokyo", "37 million people in one metro area", ""),
    ("route_lisbon_calicut", "route", [{"name": "Lisbon", "type": "city", "country": "Portugal"},
                                       {"name": "Canary Islands", "type": "point", "lat": 27.5, "lon": -16.5, "via": True},
                                       {"name": "Cape Verde", "type": "point", "lat": 14.0, "lon": -25.0, "via": True},
                                       {"name": "Gulf of Guinea", "type": "point", "lat": -8.0, "lon": -6.0, "via": True},
                                       {"name": "Cape of Good Hope", "type": "point", "lat": -35.5, "lon": 18.5, "via": True},
                                       {"name": "Mozambique Channel", "type": "point", "lat": -20.0, "lon": 40.5, "via": True},
                                       {"name": "Malindi", "type": "point", "lat": -3.3, "lon": 40.6, "via": True},
                                       {"name": "Calicut", "type": "city", "country": "India"}],
     "Vasco da Gama", "1497 — the sea route to India", "ship"),
    ("multi_scandinavia", "multi", [{"name": "Norway", "type": "country"}, {"name": "Sweden", "type": "country"},
                                    {"name": "Denmark", "type": "country"}], "Scandinavia", "", ""),
    ("region_mediterranean", "region", [{"name": "Mediterranean Sea", "type": "sea"}], "Mediterranean", "", ""),
]


def check(out: Path = None) -> int:
    """Every shot in every skin, a few frames each, straight in Chromium: reports any
    script error in seconds instead of minutes into a video. Writes preview/_maps_check.png."""
    from playwright.sync_api import sync_playwright
    out = Path(out or HERE / "preview")
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for name, shot, places, title, sub, icon in DEMOS[:5]:
        tg = [dict(resolve(p) or {}, via=p.get("via", False)) for p in places]
        tg = [x for x in tg if x.get("level")]
        for skin in sorted(SKINS):
            cases.append((f"{name}/{skin}", scene_for(tg, shot, title, sub, 7.0, 1.6, icon=icon, skin=skin)))
    bad, shots = 0, []
    tmp = Path(tempfile.mkdtemp(prefix="mapcheck_"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu", "--font-render-hinting=none"])
        for i, (label, sc) in enumerate(cases):
            page = browser.new_page(viewport={"width": W, "height": H})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(build_html(sc), wait_until="load")
            try:
                page.wait_for_function("window.__ready===true", timeout=60000)
                for t in (0.2, sc["hit"] + 0.2, sc["duration"] - 1.0):
                    page.evaluate("(t)=>window.renderFrame(t)", t)
                errors += page.evaluate("window.__errors || []")
                png = tmp / f"{i:02d}.png"
                page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": W, "height": H})
                shots.append(png)
            except Exception as e:                      # noqa: BLE001
                errors.append(f"never got ready: {e}")
            page.close()
            print(f"  {'ok  ' if not errors else 'FAIL'} {label}" + (f" — {str(errors[0])[:240]}" if errors else ""))
            bad += bool(errors)
        browser.close()
    if shots:
        cols = len(SKINS)
        inputs = sum([["-i", str(p)] for p in shots], [])
        n = len(shots)
        layout = "|".join(f"{(k % cols) * 640}_{(k // cols) * 360}" for k in range(n))
        fc = "".join(f"[{k}:v]scale=640:360[s{k}];" for k in range(n)) + "".join(f"[s{k}]" for k in range(n)) + \
             f"xstack=inputs={n}:layout={layout}:fill=black"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", fc, "-frames:v", "1",
                        str(out / "_maps_check.png")], check=False)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n  {len(cases) - bad}/{len(cases)} clean" + (f", {bad} with errors" if bad else "") +
          f" — contact sheet: {out / '_maps_check.png'}")
    return bad


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Render MAPS DLC scenes.")
    ap.add_argument("cmd", choices=["demo", "show", "find", "check"])
    ap.add_argument("place", nargs="*", help='for show/find: "California" or "Lisbon > Calicut" for a route')
    ap.add_argument("--shot", default="")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--icon", default="")
    ap.add_argument("--only", default="", help="demo: render only names containing this")
    ap.add_argument("--seconds", type=float, default=7.0)
    ap.add_argument("--hit", type=float, default=1.6)
    ap.add_argument("--out", default=str(HERE / "preview"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--skin", default="midnight", choices=sorted(SKINS))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.cmd == "check":
        sys.exit(1 if check(out) else 0)
    if a.cmd == "find":
        for p in " ".join(a.place).split(">"):
            print(json.dumps(resolve({"name": p.strip()}), ensure_ascii=False)[:400])
        return
    jobs = []
    if a.cmd == "demo":
        for name, shot, places, title, sub, icon in DEMOS:
            if a.only and a.only not in name:
                continue
            tg = [dict(resolve(p) or {}, via=p.get("via", False)) for p in places]
            sc = scene_for([x for x in tg if x.get("level")], shot, title, sub, a.seconds, a.hit, icon=icon, skin=a.skin)
            jobs.append((sc, out / f"map_{name}{'' if a.skin == 'midnight' else '_' + a.skin}.mp4"))
    else:
        parts = [p.strip() for p in " ".join(a.place).split(">") if p.strip()]
        tg = [resolve({"name": p}) for p in parts]
        missing = [p for p, x in zip(parts, tg) if not x]
        if missing:
            sys.exit(f"  not found: {', '.join(missing)}")
        shot = a.shot or ("route" if len(tg) > 1 else "")
        sc = scene_for(tg, shot, a.title if a.title is not None else parts[-1], a.subtitle, a.seconds, a.hit,
                       icon=a.icon, skin=a.skin)
        jobs.append((sc, out / f"map_{_fold(' '.join(parts)).replace(' ', '_')[:40]}.mp4"))
    for _, mp4 in jobs:
        mp4.unlink(missing_ok=True)
    t0 = time.time()
    render(jobs, workers=a.workers)
    for sc, mp4 in jobs:
        png = mp4.with_suffix(".png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, sc['duration'] - 1.2):.2f}", "-i", str(mp4),
                        "-frames:v", "1", str(png)], check=False)
        print(f"  {mp4}")
    print(f"  {len(jobs)} scene(s) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    _cli()
