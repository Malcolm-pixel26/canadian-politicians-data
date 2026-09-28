#!/usr/bin/env python3
"""
Riding maps: every district's boundary, joined with the people who
represent it, as files that open in a web browser, QGIS or Google My
Maps:

    ~/Desktop/output/maps/federal_map.html      double-click to explore it in a browser
    ~/Desktop/output/maps/federal_map.geojson   for QGIS (Layer > Add Layer > Add Vector Layer)
    ~/Desktop/output/maps/federal_map.kml       for Google My Maps (Add layer > Import) or Google Earth

and the same three files for provincial_territorial_map and local_map.

Each district lists everyone who represents it -- several people for a
multi-member ward, or a county's warden and councillor elected for the
same area -- with their office, party and contact details. Federal and
provincial maps are coloured by party; local ones, where few people run
under a party banner, by council. In the HTML map, clicking anywhere
lists every district on the map that contains that spot, so a city's
mayor shows up along with the ward councillor.

District shapes come from Represent (Open North), like the rest of this
project's data: the simplified "simple shapes" it publishes for web
maps. Boundaries rarely change, so they're cached under
~/Desktop/output/_cache/boundaries/ for 30 days (--refresh re-downloads
them); the representatives are always fetched fresh.

Usage:
    python3 make_maps.py                                 # all three levels
    python3 make_maps.py --level federal
    python3 make_maps.py --level provincial --province QC
    python3 make_maps.py --level local --filter Toronto
    python3 make_maps.py --refresh                       # re-download the district shapes too

Google My Maps limits: it imports at most 2,000 shapes and 5 MB per
file, and can drop "multi-part" shapes. So the KML has one shape per
island or separate piece of a district, and if it would be over 5 MB its
shapes are simplified until it fits (the GeoJSON and HTML keep full
detail). Over 2,000 shapes (e.g. every council in Canada at once)?
Make one map per province with --province.

The HTML map loads the Leaflet map library and Natural Resources
Canada's Canada Base Map from the internet; the districts and people
are inside the file itself.
"""
import argparse
import csv
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import zlib
from collections import Counter, namedtuple
from datetime import date
from xml.sax.saxutils import escape, quoteattr

import scrape_budget_office_spending as office_spending
from budget_common import match_to_master
from lookup_politician import HEAD_OF_COUNCIL_WORDS
from represent_common import (
    BASE_URL,
    MAX_CONSECUTIVE_FAILURES,
    PROVINCES_AND_TERRITORIES,
    FetchError,
    add_common_args,
    apply_common_args,
    classify_set,
    explain_fetch_error,
    fetch_all_representative_sets,
    fetch_representatives_for_sets,
    flatten_representative,
    get_output_dir,
    http_get_json,
    normalize_name,
    province_for_set,
    write_text_file,
)

# level -> (map title, output file name without extension)
LEVELS = {
    "federal": ("Federal ridings", "federal_map"),
    "provincial_territorial": ("Provincial and territorial ridings", "provincial_territorial_map"),
    "local": ("Municipal wards and districts", "local_map"),
}
LEVEL_ALIASES = {"provincial": "provincial_territorial"}


# --- District shapes ----------------------------------------------------------

SHAPE_CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

# When a map needs at least this many boundaries from one boundary set,
# the whole set is fetched in two requests -- its listing (Represent
# labels bulk shapes by name only, so this maps names to boundary URLs)
# and all of its shapes -- instead of one request per boundary.
BULK_MIN_BOUNDARIES = 3

# Represent returns at most 350 shapes per request. Bigger sets, like the
# ~5,000 census subdivisions some councils' mayors are mapped to, are
# fetched one boundary at a time.
BULK_MAX_BOUNDARIES = 350

_SLUG_RE = re.compile(r"[\w-]+")


def boundary_parts(url):
    """"/boundaries/toronto-wards-2018/ward-1/" -> ("toronto-wards-2018",
    "ward-1"); None for anything else (including a blank URL)."""
    parts = [p for p in urllib.parse.urlsplit(url or "").path.split("/") if p]
    if len(parts) == 3 and parts[0] == "boundaries" and all(_SLUG_RE.fullmatch(p) for p in parts[1:]):
        return parts[1], parts[2]
    return None


def _boundary_url(set_slug, slug):
    return f"/boundaries/{set_slug}/{slug}/"


def _shape_cache_path(set_slug):
    return os.path.join(get_output_dir("_cache", "boundaries"), f"{set_slug}.json")


def _load_shape_cache(set_slug):
    """This boundary set's cached shapes that are still fresh enough to use,
    as {boundary slug: {"name", "external_id", "shape", "fetched"}}."""
    try:
        with open(_shape_cache_path(set_slug), encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(cached, dict):
        return {}
    cutoff = time.time() - SHAPE_CACHE_MAX_AGE_SECONDS
    return {slug: entry for slug, entry in cached.items()
            if isinstance(entry, dict) and entry.get("shape") and (entry.get("fetched") or 0) >= cutoff}


def _fetch_set_shapes(set_slug):
    """Every boundary in one set, from its listing plus one bulk shapes
    request: {boundary slug: {"name", "external_id", "shape"}}. {} if the
    set is too big for Represent to return at once. Boundaries whose names
    aren't unique can't be told apart in the bulk response, so they're left
    out (and fetched one at a time)."""
    listing = http_get_json(f"{BASE_URL}/boundaries/{set_slug}/?limit={BULK_MAX_BOUNDARIES}")
    boundaries = listing.get("objects") or []
    total = (listing.get("meta") or {}).get("total_count")
    if total is None or total > BULK_MAX_BOUNDARIES or len(boundaries) < total:
        return {}
    payload = http_get_json(f"{BASE_URL}/boundaries/{set_slug}/simple_shape")
    shapes_by_name = {}
    for obj in payload.get("objects") or []:
        shapes_by_name.setdefault(obj.get("name"), []).append(obj.get("simple_shape"))
    name_counts = Counter(b.get("name") for b in boundaries)
    found = {}
    for boundary in boundaries:
        parts = boundary_parts(boundary.get("url"))
        shapes = shapes_by_name.get(boundary.get("name")) or []
        if parts and parts[0] == set_slug and name_counts[boundary.get("name")] == 1 \
                and len(shapes) == 1 and shapes[0]:
            found[parts[1]] = {"name": boundary.get("name") or "",
                               "external_id": boundary.get("external_id") or "", "shape": shapes[0]}
    return found


def fetch_boundary_shapes(boundary_urls, refresh=False):
    """
    The simple shape of each boundary in `boundary_urls` (Represent paths
    such as "/boundaries/toronto-wards-2018/ward-1/"), as
    ({boundary url: {"name", "external_id", "shape"}}, [urls that failed]).

    Cached shapes are used unless `refresh` (and are the fallback if a
    refresh fails). A boundary Represent has no shape for (HTTP 404) is
    simply missing from the result: that's the source data, not a failure.
    """
    wanted = {}
    for url in boundary_urls:
        parts = boundary_parts(url)
        if parts:
            wanted.setdefault(parts[0], set()).add(parts[1])

    shapes, failed = {}, []
    consecutive_failures = 0
    for set_slug in sorted(wanted):
        cached = _load_shape_cache(set_slug)
        todo = sorted(wanted[set_slug] if refresh else wanted[set_slug] - set(cached))
        fetched, gone = {}, set()
        if len(todo) >= BULK_MIN_BOUNDARIES and consecutive_failures < MAX_CONSECUTIVE_FAILURES:
            print(f"Downloading district shapes: {set_slug}")
            try:
                fetched = _fetch_set_shapes(set_slug)
            except FetchError as e:
                print(f"  ! couldn't download them all at once ({e}) -- trying one at a time...")
        remaining = [slug for slug in todo if slug not in fetched]
        if remaining and consecutive_failures < MAX_CONSECUTIVE_FAILURES:
            print(f"Downloading {len(remaining)} district shape(s) one at a time: {set_slug}")
        for n, slug in enumerate(remaining, start=1):
            url = _boundary_url(set_slug, slug)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                failed.append(url)
                continue
            try:
                shape = http_get_json(f"{BASE_URL}{url}simple_shape")
                fetched[slug] = {"name": "", "external_id": "", "shape": shape}
                consecutive_failures = 0
            except FetchError as e:
                if e.status == 404:
                    print(f"  ! Represent has no shape for {url}")
                    gone.add(slug)
                else:
                    print(f"  ! couldn't download {url}: {e}")
                    failed.append(url)
                    consecutive_failures += 1
            if n % 25 == 0 and n < len(remaining):
                print(f"  ... {n}/{len(remaining)}")
        if fetched or gone:
            now = time.time()
            for entry in fetched.values():
                entry["fetched"] = now
            cached.update(fetched)
            for slug in gone:
                cached.pop(slug, None)
            write_text_file(_shape_cache_path(set_slug), json.dumps(cached, ensure_ascii=False))
        for slug in wanted[set_slug]:
            if slug in cached:
                shapes[_boundary_url(set_slug, slug)] = cached[slug]
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        print("  ! Represent seems to be unreachable -- skipped the remaining district shapes.")
    return shapes, failed


# --- Geometry -------------------------------------------------------------------

COORDINATE_DECIMALS = 5  # about 1 m -- finer than the simplified shapes themselves
METRES_PER_DEGREE = 111_320


def _polygons(geometry):
    """A GeoJSON Polygon or MultiPolygon as a list of polygons (each a list
    of rings); [] for anything else."""
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiPolygon":
        return list(geometry.get("coordinates") or [])
    return []


def _clean_ring(ring):
    """Round a ring's coordinates, drop repeated points and close it; None
    if fewer than three distinct corners are left."""
    out = []
    for point in ring or ():
        if len(point) < 2:
            continue
        p = [round(float(point[0]), COORDINATE_DECIMALS), round(float(point[1]), COORDINATE_DECIMALS)]
        if not out or p != out[-1]:
            out.append(p)
    if out and out[0] != out[-1]:
        out.append(list(out[0]))
    return out if len(out) >= 4 else None


def clean_polygons(polygons):
    """Rounded, closed polygons; holes that collapse are dropped, and so is
    a polygon whose outer ring collapses."""
    cleaned = []
    for polygon in polygons:
        rings = [_clean_ring(ring) for ring in polygon or ()]
        if rings and rings[0]:
            cleaned.append([rings[0]] + [r for r in rings[1:] if r])
    return cleaned


def bounding_box(polygons):
    xs = [p[0] for polygon in polygons for p in polygon[0]]
    ys = [p[1] for polygon in polygons for p in polygon[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _box_area(box):
    """Rough area of a bounding box, for drawing big districts underneath
    the smaller ones inside them."""
    min_x, min_y, max_x, max_y = box
    return (max_x - min_x) * (max_y - min_y) * math.cos(math.radians((min_y + max_y) / 2))


def _simplify_ring(ring, tolerance_m):
    """Douglas-Peucker simplification of one closed ring, keeping every
    point that's more than `tolerance_m` metres off the simplified line;
    None if the ring shrinks below a triangle (e.g. a tiny island)."""
    n = len(ring)
    if n <= 4 or tolerance_m <= 0:
        return ring
    kx = math.cos(math.radians(sum(p[1] for p in ring) / n)) * METRES_PER_DEGREE
    points = [(p[0] * kx, p[1] * METRES_PER_DEGREE) for p in ring]
    keep = [False] * n
    keep[0] = keep[-1] = True
    # The ring starts and ends at the same point, so split it at the point
    # farthest from there first.
    x0, y0 = points[0]
    far = max(range(1, n - 1), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
    keep[far] = True
    stack = [(0, far), (far, n - 1)]
    limit = tolerance_m ** 2
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        worst, worst_distance = None, limit
        for i in range(first + 1, last):
            px, py = points[i]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2)) if length2 else 0.0
            ex, ey = px - ax - t * dx, py - ay - t * dy
            distance = ex * ex + ey * ey
            if distance > worst_distance:
                worst, worst_distance = i, distance
        if worst is not None:
            keep[worst] = True
            stack.append((first, worst))
            stack.append((worst, last))
    simplified = [p for p, kept in zip(ring, keep) if kept]
    return simplified if len(simplified) >= 4 else None


def simplify_polygons(polygons, tolerance_m):
    """Simplified polygons, without the islands and holes that shrink to
    nothing -- or the originals if the whole district would disappear."""
    simplified = []
    for polygon in polygons:
        outer = _simplify_ring(polygon[0], tolerance_m)
        if outer:
            holes = (_simplify_ring(ring, tolerance_m) for ring in polygon[1:])
            simplified.append([outer] + [h for h in holes if h])
    return simplified or polygons


# --- Districts and colours ------------------------------------------------------

# Checked in order against the accent-free, lowercase party name.
PARTY_COLOURS = (
    (r"\bbloc quebecois\b", "#33B2CC"),
    (r"\bquebec solidaire\b", "#FF5505"),
    (r"\bparti quebecois\b", "#004C9D"),
    (r"\bcoalition avenir quebec\b", "#00A8E1"),
    (r"\bpeople s party\b", "#4E2A84"),
    (r"\bsaskatchewan party\b", "#1D7F45"),
    (r"\byukon party\b", "#2B6CB0"),
    (r"\b(new democratic|nouveau parti democratique|ndp|npd)\b", "#F37021"),
    (r"\b(green|vert)\b", "#3D9B35"),
    (r"\bliberal", "#D71920"),
    (r"\bconservat", "#1A4782"),
    (r"\bindependan|\bindependent", "#8C8C8C"),
)
NO_PARTY_COLOUR = "#B8B8B8"
NO_PARTY_LABEL = "No party"
# For other parties and for councils: picked by a stable hash of the name.
PALETTE = ("#8E44AD", "#16A085", "#D35400", "#2C3E50", "#C0392B", "#27AE60",
           "#2980B9", "#F39C12", "#7F8C8D", "#E84393", "#6C5CE7", "#00897B")


def _palette_colour(name):
    return PALETTE[zlib.crc32(name.encode("utf-8")) % len(PALETTE)]


def party_colour(party):
    key = normalize_name(party)
    if not key:
        return NO_PARTY_COLOUR
    for pattern, colour in PARTY_COLOURS:
        if re.search(pattern, key):
            return colour
    return _palette_colour(key)


def _rep_order(row):
    office = normalize_name(row.get("elected_office", ""))
    head = any(word in office for word in HEAD_OF_COUNCIL_WORDS)
    return (0 if head else 1, row.get("name", ""))


def _joined(values):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return "; ".join(seen)


def _rep_for_page(row, measure=None):
    """What the HTML map's popups show about one person."""
    page = {
        "name": row["name"],
        "office": row["elected_office"],
        "party": row["party_name"],
        "email": row["email"],
        "phone": row["phone"],
        "local_phone": row["constituency_phone"] if row["constituency_phone"] != row["phone"] else "",
        "url": row["url"] or row["personal_url"],
        "photo": row["photo_url"],
    }
    if measure:
        page["measure"] = measure.values.get(id(row), (None, f"{measure.title.capitalize()}: no data found"))[1]
    return page


def build_features(level, entries, shapes, measure=None):
    """
    Join people to district shapes. `entries` is a list of (flattened
    representative, boundary url) pairs; `shapes` is what
    fetch_boundary_shapes() returned. Returns (features, unplaced): one
    GeoJSON feature per district, biggest first so they're drawn under the
    smaller districts inside them, and the people who couldn't be placed.
    With a `measure`, each district also gets its value (colour_by_measure()
    then does the colouring).
    """
    by_boundary = {}
    unplaced = []
    for row, boundary_url in entries:
        parts = boundary_parts(boundary_url)
        if parts:
            by_boundary.setdefault(_boundary_url(*parts), []).append(row)
        else:
            unplaced.append(row)

    colour_by_party = level != "local"
    features = []
    for url, rows in by_boundary.items():
        shape = shapes.get(url) or {}
        polygons = clean_polygons(_polygons(shape.get("shape")))
        if not polygons:
            unplaced.extend(rows)
            continue
        rows = sorted(rows, key=_rep_order)
        district_names = Counter(r["district_name"] for r in rows if r["district_name"])
        district = district_names.most_common(1)[0][0] if district_names else (shape.get("name") or url)
        parties = [r["party_name"] for r in rows if r["party_name"]]
        if colour_by_party:
            main_party = Counter(parties).most_common(1)[0][0] if parties else ""
            colour = party_colour(main_party)
        else:
            colour = _palette_colour(rows[0]["representative_set"])
        if len(rows) == 1:
            representatives = rows[0]["name"]
        else:
            representatives = "; ".join(f"{r['name']} ({r['elected_office']})" if r["elected_office"] else r["name"]
                                        for r in rows)
        properties = {
            "district": district,
            "province_territory": _joined(r["province_territory"] for r in rows),
            "level": level,
            "representative_set": _joined(r["representative_set"] for r in rows),
            "party": _joined(parties),
            "representatives": representatives,
            "elected_office": _joined(r["elected_office"] for r in rows),
            "email": _joined(r["email"] for r in rows),
            "phone": _joined(r["phone"] for r in rows),
            "website": _joined(r["url"] or r["personal_url"] for r in rows),
            "seats": len(rows),
            "external_id": shape.get("external_id") or "",
            "boundary_url": f"{BASE_URL}{url}",
            # simplestyle-spec: geojson.io and GitHub colour the map with these
            "fill": colour,
            "fill-opacity": 0.5 if colour_by_party else 0.3,
            "stroke": "#333333",
            "stroke-width": 1,
        }
        value = None
        if measure:
            value = next((measure.values[id(r)][0] for r in rows if id(r) in measure.values), None)
            properties[measure.key] = value
        features.append({
            "type": "Feature",
            "properties": properties,
            "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            "_reps": [_rep_for_page(r, measure) for r in rows],
            "_area": _box_area(bounding_box(polygons)),
            "_value": value,
        })
    features.sort(key=lambda f: -f["_area"])
    return features, unplaced


def legend_entries(level, rows):
    """[(label, colour, number of representatives)] for the map's key --
    parties, largest first. Local maps are coloured by council and get none."""
    if level == "local":
        return []
    counts = Counter(r["party_name"] or NO_PARTY_LABEL for r in rows)
    return [(party, NO_PARTY_COLOUR if party == NO_PARTY_LABEL else party_colour(party), count)
            for party, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def describe_unplaced(row):
    where = ", ".join(v for v in (row["elected_office"], row["district_name"], row["representative_set"]) if v)
    return f"{row['name']} ({where})" if where else row["name"]


# --- Colouring federal ridings by a measure ---------------------------------------
#
# --colour-by shades each riding by a number another script has already
# downloaded: MPs' office spending (scrape_budget_office_spending.py) or
# how often they voted (scrape_votes.py). People are matched to those
# files by name, the same way the budget workbook matches them.

# Five shades of one blue, light to dark (steps 300-700 of the dataviz
# reference ramp), checked with its validator as an ordinal ramp: each
# step darker than the last by a visible margin, and the lightest still
# clear (2:1) of both the map page and a white background.
MEASURE_COLOURS = ("#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
NO_DATA_COLOUR = "#c3c2b7"
MEASURE_FILL_OPACITY = 0.85  # see-through fills would shift the shades
COLOUR_BY_TITLES = {"spending": "office spending", "attendance": "voting attendance"}

# key: the GeoJSON/KML column; title: e.g. "office spending"; description:
# the key's heading; note: the side panel's explanation; values: {id(row):
# (number, what the popup says)}; format: number -> text for the key.
Measure = namedtuple("Measure", "key title description note values format")


class MissingData(Exception):
    """The file a measure reads hasn't been written yet."""


def _dollars(value):
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def spending_measure(rows):
    """Each MP's total office spending, from the per-quarter CSV that
    scrape_budget_office_spending.py writes."""
    path = os.path.join(get_output_dir("federal"), "mp_office_spending_by_quarter.csv")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            records = list(csv.DictReader(f))
    except OSError:
        raise MissingData("No office spending data yet -- run scrape_budget_office_spending.py first.") from None
    quarter_rows = []
    for record in records:
        try:
            quarter = (int(record["fiscal_year"].split("-")[1]), int(record["quarter"]))
            amounts = {category: float(record[category] or 0) for category in office_spending.CATEGORIES}
        except (KeyError, IndexError, ValueError):
            continue
        quarter_rows.append((quarter, dict(amounts, name=record.get("name") or "",
                                           constituency=record.get("constituency") or "",
                                           caucus=record.get("caucus") or "")))
    if not quarter_rows:
        raise MissingData(f"{path} has no spending rows -- run scrape_budget_office_spending.py again.")
    entries = office_spending.spending_by_person(quarter_rows)
    values = {}
    for row, key, _ in match_to_master(rows, entries)[0]:
        if key:
            entry = entries[key]
            total = round(sum(entry[category] for category in office_spending.CATEGORIES), 2)
            values[id(row)] = (total, f"Office spending: {_dollars(total)} over {len(entry['quarters'])} quarter(s)")
    period = office_spending.describe_quarters({quarter for quarter, _ in quarter_rows})
    return Measure(
        "office_spending", "office spending", f"MP office spending, {period}",
        "Colours show each MP's office spending (staff salaries, travel, hospitality and contracts) "
        "from the House of Commons' quarterly reports. Grey: no spending found for the MP.",
        values, _dollars)


_VOTES_SUMMARY_RE = re.compile(r"votes_(\d+)-(\d+)_summary\.csv")


def attendance_measure(rows):
    """Each MP's share of recorded votes they took part in, from the
    newest whole-session summary scrape_votes.py wrote (not a --bill run)."""
    folder = get_output_dir("federal")
    sessions = []
    for name in os.listdir(folder):
        match = _VOTES_SUMMARY_RE.fullmatch(name)
        if match:
            sessions.append(((int(match.group(1)), int(match.group(2))), name))
    if not sessions:
        raise MissingData("No voting records yet -- run scrape_votes.py first (for the whole session, "
                          "not just --bill).")
    (parliament, number), name = max(sessions)
    with open(os.path.join(folder, name), encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    entries, repeated = {}, set()
    for record in records:
        try:
            share = float(record["participation_pct"])
            took_part = int(record["yes"]) + int(record["no"]) + int(record["paired"])
            held = int(record["votes"])
        except (KeyError, ValueError):
            continue
        key = normalize_name(record.get("name"))
        if key in entries:
            repeated.add(key)
        entries[key] = {"raw_recipient_name": record["name"], "electoral_districts": [record.get("riding") or ""],
                        "share": share, "detail": f"{took_part} of {held}"}
    for key in repeated | {""}:
        entries.pop(key, None)  # two people with the same name: don't guess which is which
    values = {id(row): (entries[key]["share"], f"Took part in {entries[key]['share']:g}% of recorded votes "
                                               f"({entries[key]['detail']})")
              for row, key, _ in match_to_master(rows, entries)[0] if key}
    return Measure(
        "vote_participation", "voting attendance",
        f"Share of recorded votes taken part in, session {parliament}-{number}",
        "Colours show the share of the session's recorded votes each MP took part in (voting Yes or No, "
        "or paired), from OpenParliament. Grey: no voting record found for the MP, e.g. one elected "
        "since those votes.",
        values, lambda value: f"{value:g}%")


MEASURES = {"spending": spending_measure, "attendance": attendance_measure}


def value_classes(values, count=len(MEASURE_COLOURS)):
    """Split `values` into up to `count` classes of about equal size, so each
    shade covers about as many ridings, without putting equal values in
    different classes. Returns [(lowest, highest)], lowest class first."""
    ordered = sorted(values)
    classes, start = [], 0
    for k in range(1, count + 1):
        end = max(round(len(ordered) * k / count), start)
        while 0 < end < len(ordered) and ordered[end] == ordered[end - 1]:
            end += 1
        if end > start:
            classes.append((ordered[start], ordered[end - 1]))
            start = end
    return classes


def _shades(count):
    """`count` of the MEASURE_COLOURS, spread from light to dark."""
    if count <= 1:
        return list(MEASURE_COLOURS[len(MEASURE_COLOURS) // 2:][:count])
    last = len(MEASURE_COLOURS) - 1
    return [MEASURE_COLOURS[round(i * last / (count - 1))] for i in range(count)]


def colour_by_measure(features, measure):
    """Shade each district by its class of `measure`, and return the map's
    key: [(label, colour, number of districts)]."""
    classes = value_classes([f["_value"] for f in features if f["_value"] is not None])
    colours = _shades(len(classes))
    counts = [0] * len(classes)
    no_data = 0
    for feature in features:
        value = feature["_value"]
        if value is None:
            colour = NO_DATA_COLOUR
            feature["_no_data"] = True
            no_data += 1
        else:
            index = next(i for i, (_, highest) in enumerate(classes) if value <= highest)
            colour = colours[index]
            counts[index] += 1
        feature["properties"]["fill"] = colour
        feature["properties"]["fill-opacity"] = MEASURE_FILL_OPACITY
    legend = [(measure.format(low) if low == high else f"{measure.format(low)} \u2013 {measure.format(high)}",
               colour, count) for (low, high), colour, count in zip(classes, colours, counts)]
    if no_data:
        legend.append(("No data", NO_DATA_COLOUR, no_data))
    return legend


# --- Writers ----------------------------------------------------------------------

def geojson_text(name, features):
    """A GeoJSON FeatureCollection (RFC 7946: WGS 84 longitude/latitude)."""
    collection = {
        "type": "FeatureCollection",
        "name": name,  # QGIS uses this as the layer name
        "features": [{k: v for k, v in f.items() if not k.startswith("_")} for f in features],
    }
    return json.dumps(collection, ensure_ascii=False, separators=(",", ":"))


# Google My Maps imports KML files up to 5 MB, with up to 2,000 shapes.
KML_MAX_BYTES = 5_000_000
KML_MAX_PLACEMARKS = 2_000
# Simplification tried, in metres, until the KML fits (0 = as downloaded).
KML_SIMPLIFY_STEPS_METRES = (0, 25, 50, 100, 200, 400, 800, 1600)

KML_FIELDS = ("district", "province_territory", "representative_set", "party", "representatives",
              "elected_office", "email", "phone", "website", "seats", "external_id")

_XML_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_text(value):
    return escape(_XML_ILLEGAL_CHARS.sub("", str(value)))


def _blank_if_none(value):
    return "" if value is None else value


def _kml_colour(hex_colour, alpha):
    """"#D71920" -> KML's aabbggrr, e.g. "ff2019d7"."""
    rr, gg, bb = hex_colour[1:3], hex_colour[3:5], hex_colour[5:7]
    return f"{alpha}{bb}{gg}{rr}".lower()


def _kml_coordinates(ring):
    return " ".join(f"{x:.{COORDINATE_DECIMALS}f},{y:.{COORDINATE_DECIMALS}f}" for x, y in ring)


def _kml_polygon(polygon):
    rings = [f"<outerBoundaryIs><LinearRing><coordinates>{_kml_coordinates(polygon[0])}"
             "</coordinates></LinearRing></outerBoundaryIs>"]
    rings += [f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coordinates(ring)}"
              "</coordinates></LinearRing></innerBoundaryIs>" for ring in polygon[1:]]
    return f"<Polygon>{''.join(rings)}</Polygon>"


def _kml_description(feature):
    lines = []
    for rep in feature["_reps"]:
        role = ", ".join(v for v in (rep["office"], rep["party"]) if v)
        contact = ", ".join(v for v in (rep["email"], rep["phone"]) if v)
        lines.append(" -- ".join(v for v in (rep["name"], role, contact, rep.get("measure", "")) if v))
    return "\n".join(lines)


def kml_text(title, description, features, tolerance_m=0):
    """
    A KML document, returned with its number of placemarks. Written for
    Google My Maps, which may drop MultiGeometry, Folder and HTML
    descriptions on import: one placemark per polygon (islands and other
    separate pieces of a district repeat its name and data), no folders,
    and plain-text descriptions.
    """
    styles = {}
    placemarks = []
    for feature in features:
        props = feature["properties"]
        alpha = f"{round(255 * props['fill-opacity']):02x}"  # the same see-through fill as the other formats
        style_id = styles.setdefault((props["fill"], alpha), f"s{len(styles)}")
        fields = KML_FIELDS + tuple(key for key in ("office_spending", "vote_participation") if key in props)
        data = "".join(f"<Data name={quoteattr(field)}><value>{_xml_text(_blank_if_none(props[field]))}</value></Data>"
                       for field in fields)
        head = (f"<Placemark><name>{_xml_text(props['district'])}</name>"
                f"<description>{_xml_text(_kml_description(feature))}</description>"
                f"<styleUrl>#{style_id}</styleUrl><ExtendedData>{data}</ExtendedData>")
        polygons = feature["geometry"]["coordinates"]
        if tolerance_m:
            polygons = simplify_polygons(polygons, tolerance_m)
        placemarks.extend(f"{head}{_kml_polygon(polygon)}</Placemark>\n" for polygon in polygons)
    style_xml = "".join(
        f'<Style id="{style_id}"><LineStyle><color>ff333333</color><width>1</width></LineStyle>'
        f"<PolyStyle><color>{_kml_colour(colour, alpha)}</color></PolyStyle></Style>\n"
        for (colour, alpha), style_id in styles.items()
    )
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
        f"<name>{_xml_text(title)}</name>\n<description>{_xml_text(description)}</description>\n"
        f"{style_xml}{''.join(placemarks)}</Document>\n</kml>\n"
    )
    return text, len(placemarks)


def write_kml(path, title, description, features):
    """Write the KML, simplified just enough to fit Google My Maps' 5 MB
    import limit, and say so if it had to be (or still doesn't fit)."""
    for tolerance in KML_SIMPLIFY_STEPS_METRES:
        text, placemarks = kml_text(title, description, features, tolerance)
        size = len(text.encode("utf-8"))
        if size <= KML_MAX_BYTES:
            break
    final = write_text_file(path, text)
    print(f"Wrote {placemarks} shape(s) -> {final}")
    if tolerance:
        print(f"  (Google My Maps imports KML files of up to 5 MB, so these shapes were simplified to "
              f"about {tolerance} m; the GeoJSON and HTML keep full detail.)")
    if size > KML_MAX_BYTES:
        print(f"  ! At {size / 1e6:.1f} MB this is still too big for Google My Maps -- make smaller maps "
              f"with --province, or open it in Google Earth instead.")
    if placemarks > KML_MAX_PLACEMARKS:
        print(f"  ! Google My Maps imports at most {KML_MAX_PLACEMARKS:,} shapes per layer -- make smaller "
              f"maps with --province, or open it in Google Earth instead.")
    return final


def _script_json(value):
    """JSON that's safe inside a <script> element: no "</script>" or
    "<!--" can appear, whatever the data contains."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                          (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(char, escaped)
    return text


def html_text(page):
    """The self-contained HTML map. `page` holds its title, subtitle,
    legend, notes and features (see make_map())."""
    features = [{"type": "Feature", "geometry": f["geometry"],
                 "properties": {"district": f["properties"]["district"],
                                "province": f["properties"]["province_territory"],
                                "set": f["properties"]["representative_set"],
                                "colour": f["properties"]["fill"],
                                "dashed": bool(f.get("_no_data")),
                                "reps": f["_reps"]}}
                for f in page["features"]]
    head, tail = HTML_TEMPLATE.split("__DATA__")
    return head.replace("__TITLE__", html.escape(page["title"])) + _script_json(dict(page, features=features)) + tail


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
  html, body { height: 100%; margin: 0; }
  body { font: 14px/1.4 Arial, Helvetica, sans-serif; color: #222; background: #eef0f2; }
  #map { position: absolute; top: 0; right: 0; bottom: 0; left: 0; }
  #map.offline { display: flex; align-items: center; justify-content: center; padding: 24px; text-align: center; }
  #panel { position: absolute; top: 10px; right: 10px; z-index: 1000; width: 300px; box-sizing: border-box;
           max-height: calc(100% - 40px); overflow: auto; background: #fff; border-radius: 6px;
           box-shadow: 0 1px 5px rgba(0, 0, 0, .35); padding: 10px 12px; }
  #panel.collapsed .body { display: none; }
  #panel .header { display: flex; align-items: flex-start; gap: 8px; }
  #panel h1 { flex: 1; font-size: 17px; margin: 0; }
  #panel .toggle { font: inherit; font-size: 12px; }
  #panel .sub, #panel .note { color: #555; font-size: 12px; margin: 4px 0 8px; }
  #panel input { width: 100%; box-sizing: border-box; padding: 6px 8px; font: inherit;
                 border: 1px solid #999; border-radius: 4px; }
  #panel .status { color: #a33; font-size: 12px; min-height: 1em; margin-top: 2px; }
  #panel ul { list-style: none; padding: 0; margin: 6px 0; }
  #panel .legend li { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
  #panel .swatch { flex: none; width: 14px; height: 14px; border: 1px solid #555; border-radius: 2px; }
  #panel .count { margin-left: auto; color: #555; }
  #panel .key-title { font-weight: bold; font-size: 12px; margin-top: 8px; }
  #panel .unplaced li { font-size: 12px; margin: 2px 0; }
  .popup h3 { margin: 0; font-size: 14px; }
  .popup .district + .district { border-top: 1px solid #ddd; margin-top: 8px; padding-top: 8px; }
  .popup .rep { display: flex; gap: 8px; margin-top: 6px; }
  .popup .rep img { flex: none; width: 48px; height: 60px; object-fit: cover; border-radius: 3px; }
  .popup .meta { color: #555; }
  .popup .measure { font-weight: bold; }
  @media (max-width: 600px) { #panel { left: 10px; width: auto; max-height: 45%; } }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
var MAP_DATA = __DATA__;
</script>
<script>
(function () {
  "use strict";
  var data = MAP_DATA;
  var features = data.features;
  var mapDiv = document.getElementById("map");
  var panel = document.getElementById("panel");

  // Every piece of text from the data goes in through textContent, never
  // as HTML, and only http(s) links are followed.
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function webUrl(url) { return /^https?:\\/\\//i.test(url || "") ? url : ""; }
  function fold(text) {
    return String(text || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
  }

  // --- Side panel: title, search, legend, notes ---
  var header = el("div", "header");
  header.appendChild(el("h1", "", data.title));
  var toggle = el("button", "toggle", "Hide");
  toggle.type = "button";
  toggle.addEventListener("click", function () {
    var collapsed = panel.classList.toggle("collapsed");
    toggle.textContent = collapsed ? "Show" : "Hide";
  });
  header.appendChild(toggle);
  panel.appendChild(header);
  var body = el("div", "body");
  panel.appendChild(body);
  body.appendChild(el("div", "sub", data.subtitle));
  var search = el("input");
  search.type = "search";
  search.placeholder = "Find a district or person";
  search.setAttribute("list", "district-names");
  search.setAttribute("aria-label", "Find a district or person");
  var names = el("datalist");
  names.id = "district-names";
  var status = el("div", "status");
  body.appendChild(search);
  body.appendChild(names);
  body.appendChild(status);
  if (data.legend_title) { body.appendChild(el("div", "key-title", data.legend_title)); }
  if (data.legend.length) {
    var legend = el("ul", "legend");
    data.legend.forEach(function (item) {
      var li = el("li");
      var swatch = el("span", "swatch");
      swatch.style.background = item.colour;
      li.appendChild(swatch);
      li.appendChild(el("span", "", item.label));
      li.appendChild(el("span", "count", String(item.count)));
      legend.appendChild(li);
    });
    body.appendChild(legend);
  }
  data.notes.forEach(function (note) { body.appendChild(el("div", "note", note)); });
  if (data.unplaced.length) {
    var unplaced = el("ul", "unplaced");
    data.unplaced.forEach(function (who) { unplaced.appendChild(el("li", "", who)); });
    body.appendChild(unplaced);
  }

  if (typeof L === "undefined") {
    mapDiv.className = "offline";
    mapDiv.textContent = "The map couldn't load: this page needs an internet connection for the map " +
      "library and background map. (The districts themselves are inside the file.)";
    search.disabled = true;
    return;
  }

  // --- The map ---
  var map = L.map(mapDiv, {preferCanvas: true, zoomSnap: 0.5});
  map.attributionControl.addAttribution(
    'Districts &amp; representatives: <a href="https://represent.opennorth.ca/">Represent</a> (Open North)');
  map.createPane("labels");
  map.getPane("labels").style.zIndex = 450;
  map.getPane("labels").style.pointerEvents = "none";
  var tiles = "https://geoappext.nrcan.gc.ca/arcgis/rest/services/BaseMaps/";
  var background = L.layerGroup([
    L.tileLayer(tiles + "CBMT_CBCT_GEOM_3857/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 18,
      attribution: 'Base map &copy; <a href="https://natural-resources.canada.ca/">Natural Resources Canada</a> ' +
        '(<a href="https://open.canada.ca/en/open-government-licence-canada">OGL-Canada</a>)'
    }),
    L.tileLayer(tiles + "CBMT_TXT_3857/MapServer/tile/{z}/{y}/{x}", {maxZoom: 18, pane: "labels"})
  ]).addTo(map);

  var layers = [];
  var districts = L.geoJSON(features, {
    style: function (feature) {
      return {color: "#333", weight: 0.8, opacity: 0.8, dashArray: feature.properties.dashed ? "4 3" : null,
              fillColor: feature.properties.colour, fillOpacity: data.fill_opacity};
    },
    onEachFeature: function (feature, layer) {
      layers.push(layer);
      layer.bindTooltip(el("span", "", feature.properties.district), {sticky: true});
      layer.on("mouseover", function () { layer.setStyle({weight: 2.5, color: "#000", opacity: 1}); });
      layer.on("mouseout", function () { districts.resetStyle(layer); });
    }
  }).addTo(map);
  L.control.layers({"Canada Base Map (NRCan)": background, "No background": L.layerGroup()},
                   {"Districts": districts}, {position: "bottomleft"}).addTo(map);
  if (features.length) {
    map.fitBounds(districts.getBounds(), {padding: [20, 20]});
  } else {
    map.setView([56, -96], 4);
  }

  // --- Popups: everyone representing the districts at a spot ---
  function repBlock(rep) {
    var row = el("div", "rep");
    var photo = webUrl(rep.photo);
    if (photo) {
      var img = el("img");
      img.src = photo;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.onerror = function () { img.remove(); };
      row.appendChild(img);
    }
    var info = el("div");
    var url = webUrl(rep.url);
    var name = el(url ? "a" : "strong", "", rep.name);
    if (url) {
      name.href = url;
      name.target = "_blank";
      name.rel = "noopener noreferrer";
    }
    info.appendChild(name);
    var role = [rep.office, rep.party].filter(Boolean).join(", ");
    if (role) { info.appendChild(el("div", "meta", role)); }
    if (rep.measure) { info.appendChild(el("div", "measure", rep.measure)); }
    if (/^[^\\s@<>"]+@[^\\s@<>"]+$/.test(rep.email || "")) {
      var mail = el("a", "", rep.email);
      mail.href = "mailto:" + rep.email;
      var line = el("div");
      line.appendChild(mail);
      info.appendChild(line);
    }
    if (rep.phone) { info.appendChild(el("div", "", rep.phone)); }
    if (rep.local_phone) { info.appendChild(el("div", "", rep.local_phone + " (local office)")); }
    row.appendChild(info);
    return row;
  }
  function popupFor(indexes) {
    var box = el("div", "popup");
    indexes.forEach(function (i) {
      var p = features[i].properties;
      var section = el("div", "district");
      section.appendChild(el("h3", "", p.district));
      var where = [p.set, p.province].filter(Boolean).join(" \\u00b7 ");
      if (where) { section.appendChild(el("div", "meta", where)); }
      p.reps.forEach(function (rep) { section.appendChild(repBlock(rep)); });
      box.appendChild(section);
    });
    return box;
  }
  function openPopup(latlng, indexes) {
    L.popup({maxWidth: 340, maxHeight: 380}).setLatLng(latlng).setContent(popupFor(indexes)).openOn(map);
  }

  function polygonsOf(geometry) {
    return geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  }
  var boxes = features.map(function (feature) {
    var box = [Infinity, Infinity, -Infinity, -Infinity];
    polygonsOf(feature.geometry).forEach(function (polygon) {
      polygon[0].forEach(function (p) {
        box[0] = Math.min(box[0], p[0]); box[1] = Math.min(box[1], p[1]);
        box[2] = Math.max(box[2], p[0]); box[3] = Math.max(box[3], p[1]);
      });
    });
    return box;
  });
  function ringContains(ring, x, y) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) { inside = !inside; }
    }
    return inside;
  }
  function contains(feature, x, y) {
    return polygonsOf(feature.geometry).some(function (polygon) {
      if (!ringContains(polygon[0], x, y)) { return false; }
      for (var k = 1; k < polygon.length; k++) {
        if (ringContains(polygon[k], x, y)) { return false; }
      }
      return true;
    });
  }
  function boxArea(box) { return (box[2] - box[0]) * (box[3] - box[1]); }
  map.on("click", function (e) {
    var x = e.latlng.lng, y = e.latlng.lat, hits = [];
    for (var i = 0; i < features.length; i++) {
      var b = boxes[i];
      if (x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3] && contains(features[i], x, y)) { hits.push(i); }
    }
    if (hits.length) {
      hits.sort(function (a, b) { return boxArea(boxes[a]) - boxArea(boxes[b]); });
      openPopup(e.latlng, hits);
    }
  });

  // --- Search ---
  var choices = features.map(function (feature, i) {
    var people = feature.properties.reps.map(function (rep) { return rep.name; }).join(", ");
    var label = feature.properties.district + (people ? " \\u2014 " + people : "");
    var option = el("option");
    option.value = label;
    names.appendChild(option);
    return {label: fold(label), index: i};
  });
  function find() {
    var query = fold(search.value.trim());
    if (!query) { status.textContent = ""; return; }
    var match = choices.filter(function (c) { return c.label === query; })[0] ||
                choices.filter(function (c) { return c.label.indexOf(query) !== -1; })[0];
    if (!match) {
      status.textContent = "Nothing on this map matches that.";
      return;
    }
    status.textContent = "";
    var bounds = layers[match.index].getBounds();
    map.fitBounds(bounds, {maxZoom: 13, padding: [20, 20]});
    openPopup(bounds.getCenter(), [match.index]);
  }
  search.addEventListener("change", find);
  search.addEventListener("keydown", function (e) { if (e.key === "Enter") { find(); } });
})();
</script>
</body>
</html>
"""


# --- Putting it together --------------------------------------------------------------

def _level_arg(value):
    value = value.strip().lower()
    level = LEVEL_ALIASES.get(value, value)
    if level != "all" and level not in LEVELS:
        raise argparse.ArgumentTypeError("choose federal, provincial, local or all")
    return level


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Map every district with the people who represent it (HTML, GeoJSON for QGIS, "
                    "KML for Google My Maps)."
    )
    parser.add_argument("--level", type=_level_arg, action="append", metavar="LEVEL",
                        help="federal, provincial, local or all (default: all; repeat for several)")
    parser.add_argument("--province", metavar="CODE", type=str.upper, choices=sorted(PROVINCES_AND_TERRITORIES),
                        help="only districts in this province/territory, e.g. --province ON")
    parser.add_argument("--filter", metavar="TEXT",
                        help="only legislatures/councils whose name contains TEXT, e.g. --filter Toronto")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download district shapes instead of using the 30-day cache")
    parser.add_argument("--colour-by", "--color-by", dest="colour_by", choices=sorted(MEASURES),
                        help="shade federal ridings by MPs' office spending or voting attendance instead of "
                             "party (run scrape_budget_office_spending.py or scrape_votes.py first)")
    add_common_args(parser)
    args = parser.parse_args(argv)
    if args.colour_by and any(level != "federal" for level in args.level or ()):
        parser.error("--colour-by only works for federal maps: office spending and voting records are "
                     "published for MPs only")
    return args


def _file_stem(level, args):
    stem = LEVELS[level][1]
    if args.colour_by:
        stem += f"_{args.colour_by}"
    if args.province:
        stem += f"_{args.province}"
    if args.filter:
        slug = re.sub(r"[^a-z0-9]+", "-", normalize_name(args.filter)).strip("-")
        if slug:
            stem += f"_{slug}"
    return stem


def _title(level, args):
    title = LEVELS[level][0]
    if args.colour_by:
        title += f" by {COLOUR_BY_TITLES[args.colour_by]}"
    qualifiers = [PROVINCES_AND_TERRITORIES[args.province]] if args.province else []
    if args.filter:
        qualifiers.append(f'"{args.filter}"')
    return f"{title} — {', '.join(qualifiers)}" if qualifiers else title


def make_map(level, args, all_sets):
    """
    Build and write one level's map. Returns (whether a map was written,
    [problems]) -- no problems means everything was downloaded.
    """
    name_filter = (args.filter or "").lower()
    sets = [s for s in all_sets if classify_set(s) == level
            and (not name_filter or name_filter in (s.get("name") or "").lower())
            and (not args.province or level == "federal" or province_for_set(s) == args.province)]
    if not sets:
        print("No legislature or council matches these filters -- skipped.")
        return False, []

    reps_by_url, failed_sets = fetch_representatives_for_sets(sets)
    problems = [f"couldn't download {', '.join(failed_sets)}"] if failed_sets else []
    entries = []
    for rep_set in sets:
        province = province_for_set(rep_set)
        for rep in reps_by_url.get(rep_set["url"]) or []:
            row = flatten_representative(rep, level, province=province)
            if args.province and row["province_territory"] != args.province:
                continue
            entries.append((row, (rep.get("related") or {}).get("boundary_url") or ""))
    if not entries:
        print("Nobody to map -- no representatives were downloaded for these filters.")
        return False, problems
    rows = [row for row, _ in entries]
    measure = None
    if args.colour_by:
        try:
            measure = MEASURES[args.colour_by](rows)
        except MissingData as e:
            print(e)
            return False, problems + [str(e)]
        print(f"Shading by {measure.title}: data for {len(measure.values)} of {len(rows)} MP(s).")

    shapes, failed_shapes = fetch_boundary_shapes([url for _, url in entries], refresh=args.refresh)
    if failed_shapes:
        problems.append(f"couldn't download {len(failed_shapes)} district shape(s)")
    features, unplaced = build_features(level, entries, shapes, measure)
    if not features:
        # Don't replace a good map from an earlier run with an empty one.
        print("None of these districts has a shape to draw -- no map written.")
        return False, problems or ["no district shapes"]
    if measure:
        legend = colour_by_measure(features, measure)
    else:
        legend = legend_entries(level, rows)

    title = _title(level, args)
    subtitle = (f"{len(features)} district(s), {len(rows) - len(unplaced)} representative(s). "
                f"Made {date.today().isoformat()} from Represent data.")
    notes = [measure.note] if measure else []
    if level == "local":
        notes.append("Colours tell councils apart. Click anywhere to see everyone whose district covers "
                     "that spot, e.g. both the ward councillor and the mayor.")
    else:
        notes.append("Click a district to see who represents it.")
    if failed_sets:
        notes.append(f"Couldn't download: {', '.join(failed_sets)}.")
    if failed_shapes:
        notes.append("Some district shapes couldn't be downloaded this time -- run make_maps.py again "
                     "to fill them in.")
    if unplaced:
        notes.append(f"Not on the map ({len(unplaced)}):")

    stem = _file_stem(level, args)
    out_dir = get_output_dir("maps")
    page = {
        "title": title,
        "subtitle": subtitle,
        "legend_title": measure.description if measure else "",
        "legend": [{"label": label, "colour": colour, "count": count} for label, colour, count in legend],
        "notes": notes,
        "unplaced": sorted(describe_unplaced(r) for r in unplaced),
        "fill_opacity": MEASURE_FILL_OPACITY if measure else 0.3 if level == "local" else 0.5,
        "features": features,
    }
    final = write_text_file(os.path.join(out_dir, f"{stem}.html"), html_text(page))
    print(f"Wrote {len(features)} district(s) -> {final}")
    final = write_text_file(os.path.join(out_dir, f"{stem}.geojson"), geojson_text(stem, features))
    print(f"Wrote {len(features)} district(s) -> {final}")
    description = (f"{subtitle} One placemark per polygon: a district in several pieces (islands) "
                   f"has one per piece.")
    write_kml(os.path.join(out_dir, f"{stem}.kml"), title, description, features)

    if unplaced:
        print(f"{len(unplaced)} representative(s) couldn't be placed on the map: "
              + "; ".join(describe_unplaced(r) for r in unplaced[:10])
              + ("; ..." if len(unplaced) > 10 else ""))
    return True, problems


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    levels = []
    for level in args.level or (["federal"] if args.colour_by else ["all"]):
        for chosen in (list(LEVELS) if level == "all" else [level]):
            if chosen not in levels:
                levels.append(chosen)

    print("=== Riding maps ===")
    try:
        all_sets = fetch_all_representative_sets()
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    made, problems = 0, []
    for level in levels:
        print(f"\n### {LEVELS[level][0]} ###")
        try:
            written, level_problems = make_map(level, args, all_sets)
        except FetchError as e:
            explain_fetch_error(e)
            written, level_problems = False, ["download failed"]
        made += written
        problems.extend(f"{LEVELS[level][0]}: {problem}" for problem in level_problems)

    print()
    if not made:
        print("No maps were made." + (" See the errors above." if problems else
                                      " Nothing matches the filters you gave."))
        return 1
    print(f"Maps are in {get_output_dir('maps')}")
    if problems:
        print("Some data was missing -- re-run later to fill it in: " + "; ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
