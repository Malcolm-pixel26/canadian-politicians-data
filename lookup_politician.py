#!/usr/bin/env python3
"""
Look up everything currently available on one named politician -- or on
everyone who represents a place -- without running a full scrape.

Three modes:

1. Cached lookup (default, fast) -- searches your existing
   ~/Desktop/output/master.xlsx for a name match. Run the full scrapers
   at least once first so there's something to search.

       python3 lookup_politician.py "Jane Doe"

2. Live lookup (--live) -- goes straight to the Represent API (and the
   Senate's website, for senators) instead of the cache. Searching
   everyone at every level of government takes only a few requests
   (Represent's bulk download), so --category and --place are optional
   ways to narrow the results rather than requirements. Nunavut's MLAs
   take a request each to read, so they're only in the cached lookup.

       python3 lookup_politician.py "Jane Doe" --live
       python3 lookup_politician.py "Jane Doe" --live --category federal
       python3 lookup_politician.py "Jane Doe" --live --category local --place Toronto

3. Who represents a place (always live) -- by postal code, or by exact
   latitude,longitude (e.g. right-click your address in Google Maps).
   Lists your MP, MPP/MLA/MNA, mayor and councillor(s) in one request,
   plus your province's senators from master.xlsx if you've run
   scrape_federal.py. (Represent has no boundaries for Nunavut's
   constituencies, so a Nunavut address can't be matched to its MLA.)

       python3 lookup_politician.py --postal-code "K1A 0A6"
       python3 lookup_politician.py --point 45.4236,-75.7009

   A postal code can straddle district boundaries; when it does, every
   possible representative is listed and flagged, and --point gives the
   exact answer.

Names match on word prefixes in any order, ignoring accents and case:
"Legault", "francois leg" and "LEGAULT François" all find François
Legault; "Ann" finds "Ann" or "Anne" but not "Joanne".

For a federal match, this also pulls OpenParliament / House of Commons
enrichment (committees, first elected date) live, since that's cheap
for a single person (skip with --no-enrich).

This only pulls from public sources (Represent, OpenParliament, the
House of Commons, the Senate, the Legislative Assembly of Nunavut) that
already publish these people's official, public-facing legislative
contact info -- it does not search social media, news, or anything
about their personal/private life.
"""
import argparse
import os
import string
import sys

import nunavut
import senate
from represent_common import (
    FEDERAL_ENRICHMENT_FIELDS,
    MASTER_FIELDS,
    PROVINCES_AND_TERRITORIES,
    FetchError,
    add_common_args,
    apply_common_args,
    classify_set,
    explain_fetch_error,
    fetch_all_representative_sets,
    fetch_postal_code,
    fetch_representatives_at_point,
    fetch_rows_for_sets,
    flatten_representative,
    get_master_path,
    in_house_of_commons,
    normalize_name,
    normalize_postal_code,
    read_all_master_rows,
    read_master_tab,
)

CATEGORY_ALIASES = {
    "federal": "federal",
    "provincial": "provincial_territorial",
    "provincial_territorial": "provincial_territorial",
    "local": "local",
}

LEVEL_TITLES = {
    "federal": "Federal",
    "provincial_territorial": "Provincial / territorial",
    "local": "Local",
    "other": "Other",
}

# Head-of-council titles (accent-free, as compared) are listed before councillors.
HEAD_OF_COUNCIL_WORDS = ("mayor", "maire", "warden", "reeve", "chair", "prefet")


def _lat_lng(value):
    try:
        lat, lng = (float(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("use LATITUDE,LONGITUDE, e.g. 45.4236,-75.7009")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise argparse.ArgumentTypeError("latitude must be -90..90 and longitude -180..180")
    return lat, lng


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Look up a politician by name (from master.xlsx or live from Represent), "
                    "or everyone who represents a postal code or map point."
    )
    parser.add_argument("name", nargs="?", help='the name to search for, e.g. "Jane Doe"')
    parser.add_argument("--postal-code", metavar="CODE", help='who represents this postal code, e.g. "K1A 0A6"')
    parser.add_argument("--point", metavar="LAT,LNG", type=_lat_lng,
                        help="who represents this exact location, e.g. 45.4236,-75.7009")
    parser.add_argument("--live", action="store_true", help="query Represent now instead of master.xlsx")
    parser.add_argument(
        "--category",
        type=str.lower,
        choices=sorted(CATEGORY_ALIASES),
        help="only this level of government",
    )
    parser.add_argument(
        "--place",
        metavar="TEXT",
        help="only legislatures/councils (or districts) whose name contains TEXT, e.g. Toronto",
    )
    parser.add_argument("--no-enrich", action="store_true", help="skip live enrichment for federal matches")
    add_common_args(parser)
    args = parser.parse_args(argv)

    if sum(1 for given in (args.name, args.postal_code, args.point) if given) != 1:
        parser.error("give one of: a name, --postal-code, or --point")
    if args.postal_code:
        code = normalize_postal_code(args.postal_code)
        if not code:
            parser.error(f"{args.postal_code!r} isn't a valid Canadian postal code (e.g. K1A 0A6)")
        args.postal_code = code
    args.category = CATEGORY_ALIASES.get(args.category)
    return args


def name_matches(query, name):
    """Every word of the query starts some word of the name (any order,
    accents/case/punctuation ignored)."""
    query_words = normalize_name(query).split()
    name_words = normalize_name(name).split()
    return bool(query_words) and all(
        any(word.startswith(q) for word in name_words) for q in query_words
    )


def _in_place(row, place):
    place = place.lower()
    return place in str(row.get("representative_set", "")).lower() or place in str(
        row.get("district_name", "")
    ).lower()


def search_cache(name, category=None, place=None):
    master_path = get_master_path()
    if not os.path.exists(master_path):
        print(f"No master file found at {master_path} yet -- run a scraper first, "
              f"or use --live.")
        return []

    matches = [row for row in read_all_master_rows(master_path).values() if name_matches(name, row.get("name", ""))]
    if category:
        matches = [row for row in matches if row.get("category") == category]
    if place:
        matches = [row for row in matches if _in_place(row, place)]
    return matches


def _senate_may_match(place):
    """Whether --place could select senators: it names the Senate, or a
    province or territory (their district)."""
    place = (place or "").lower()
    return place in senate.SET_NAME.lower() or any(
        place in province.lower() for province in PROVINCES_AND_TERRITORIES.values())


def search_live(name, category=None, place=None):
    sets = fetch_all_representative_sets()
    if category:
        sets = [s for s in sets if classify_set(s) == category]
    if place:
        sets = [s for s in sets if place.lower() in s["name"].lower()]
    with_senate = category in (None, "federal") and _senate_may_match(place)
    if not sets and not with_senate:
        print(f"No representative-sets matched --place {place!r}.")
        return []

    rows = []
    if sets:
        print(f"Searching {len(sets)} legislature(s)/council(s) live...")
        rows = fetch_rows_for_sets(sets).rows
    if with_senate:
        try:
            senators = senate.fetch_senators()
        except (FetchError, ValueError) as e:
            print(f"  ! Couldn't search the Senate ({e}).")
            senators = []
        rows += [r for r in senators if not place or _in_place(r, place)]
    return [row for row in rows if name_matches(name, row.get("name", ""))]


# --- Who represents a place --------------------------------------------------

def _flatten_located(reps, province=None):
    rows = []
    seen = set()
    for rep in reps:
        key = (rep.get("representative_set_name"), rep.get("name"), rep.get("district_name"))
        if key in seen:
            continue
        seen.add(key)
        category = classify_set({"name": rep.get("representative_set_name")})
        rows.append(flatten_representative(rep, category, province=province))
    return rows


def representatives_for_postal_code(payload):
    """Everyone Represent links to a postal code: the union of its
    "centroid" and "concordance" lists, flattened like a scrape."""
    reps = (payload.get("representatives_centroid") or []) + (payload.get("representatives_concordance") or [])
    return _flatten_located(reps, province=payload.get("province") or None)


def ambiguous_offices(rows):
    """(legislature/council, office) pairs that matched more than one
    district -- the postal code straddles a boundary there, so only one
    of those people is actually the representative."""
    districts = {}
    for row in rows:
        districts.setdefault((row["representative_set"], row["elected_office"]), set()).add(row["district_name"])
    return {key for key, found in districts.items() if len(found) > 1}


def _office_rank(row):
    office = normalize_name(row.get("elected_office", ""))
    return 0 if any(word in office for word in HEAD_OF_COUNCIL_WORDS) else 1


def senators_from_master(province):
    """A province's or territory's senators, from master.xlsx -- the Senate
    isn't in Represent, and this way a lookup stays one request. [] if
    there's no master.xlsx yet or it has no senators."""
    path = get_master_path()
    if not province or not os.path.exists(path):
        return []
    return [row for row in read_master_tab(path, "Federal").values()
            if row.get("representative_set") == senate.SET_NAME and row.get("status") != "removed"
            and row.get("province_territory") == province]


def _province_of(rows):
    """The province or territory a point is in, from its MP's riding."""
    return next((r["province_territory"] for r in rows if in_house_of_commons(r) and r.get("province_territory")), "")


def _print_senators(senators):
    province = PROVINCES_AND_TERRITORIES.get(senators[0]["province_territory"], senators[0]["district_name"])
    print(f"  {senate.SET_NAME} -- {len(senators)} senator(s) for {province} (from master.xlsx)")
    for row in sorted(senators, key=lambda r: (r["last_name"], r["name"])):
        party = f" ({row['party_name']})" if row["party_name"] else ""
        contact = ", ".join(v for v in (row["email"], row["phone"]) if v)
        print(f"    {row['elected_office']} {row['name']}{party}" + (f" -- {contact}" if contact else ""))


def print_location_results(rows, where, ambiguous=frozenset()):
    print(f"\nRepresentatives for {where}:")
    for level in ("federal", "provincial_territorial", "local", "other"):
        level_rows = [r for r in rows if r["category"] == level]
        if not level_rows:
            continue
        print(f"\n{LEVEL_TITLES[level]}")
        for set_name in sorted({r["representative_set"] for r in level_rows}, key=lambda n: (n == senate.SET_NAME, n)):
            set_rows = sorted((r for r in level_rows if r["representative_set"] == set_name),
                              key=lambda r: (_office_rank(r), r["district_name"], r["name"]))
            if set_name == senate.SET_NAME:
                _print_senators(set_rows)  # one line each: a province can have 24
                continue
            print(f"  {set_name}")
            for row in set_rows:
                party = f" ({row['party_name']})" if row["party_name"] else ""
                flag = "   <- one of these, depending on the street address" \
                    if (set_name, row["elected_office"]) in ambiguous else ""
                print(f"    {row['elected_office']} {row['name']}{party} -- {row['district_name']}{flag}")
                if row["email"]:
                    print(f"        Email: {row['email']}")
                if row["phone"]:
                    print(f"        Phone: {row['phone']}")
                if row["constituency_address"] or row["constituency_phone"]:
                    office = ", ".join(v for v in (row["constituency_address"], row["constituency_phone"]) if v)
                    print(f"        Local office: {office}")
    if ambiguous:
        print("\nThis postal code straddles a district boundary, so some offices list more than one "
              "person.\nFor the exact answer, look up the address's coordinates (e.g. right-click it in "
              "Google Maps)\nand run: lookup_politician.py --point LATITUDE,LONGITUDE")


def lookup_location(args):
    try:
        if args.postal_code:
            code = args.postal_code
            try:
                payload = fetch_postal_code(code)
            except FetchError as e:
                if e.status == 404:
                    print(f"Represent doesn't know the postal code {code[:3]} {code[3:]} -- check it, "
                          f"or use --point with the address's coordinates.")
                    return 1
                raise
            rows = representatives_for_postal_code(payload)
            province = payload.get("province") or _province_of(rows)
            city = string.capwords(payload.get("city") or "")
            place = ", ".join(v for v in (city, payload.get("province")) if v)
            where = f"{code[:3]} {code[3:]}" + (f" ({place})" if place else "")
        else:
            latitude, longitude = args.point
            rows = _flatten_located(fetch_representatives_at_point(latitude, longitude))
            province = _province_of(rows)
            where = f"{latitude}, {longitude}"
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    rows += senators_from_master(province)
    if args.category:
        rows = [r for r in rows if r["category"] == args.category]
    if args.place:
        rows = [r for r in rows if _in_place(r, args.place)]
    if not rows:
        print(f"No representatives found for {where}.")
        return 1

    print_location_results(rows, where, ambiguous_offices(rows) if args.postal_code else frozenset())
    if province == nunavut.PROVINCE and args.category in (None, "provincial_territorial"):
        print("\nRepresent has no boundaries for Nunavut's constituencies, so this can't tell which MLA "
              "is yours.\nThey're all in provincial_territorial/nunavut-legislature.xlsx (from "
              "scrape_provincial_territorial.py),\nor search by name: lookup_politician.py \"NAME\" --place Nunavut")
    if not args.no_enrich:
        enrich_and_print_federal(rows)
    return 0


# --- Output --------------------------------------------------------------------

def print_match(row):
    print("-" * 60)
    for field in MASTER_FIELDS:
        val = row.get(field, "")
        if val not in ("", None):
            print(f"{field.replace('_', ' ').title():>22}: {val}")


def enrich_and_print_federal(rows):
    """MPs are cheap to enrich live, on the spot, since it's just a person
    or two rather than the whole House. (Senators have no House data.)"""
    federal = [r for r in rows if in_house_of_commons(r)]
    if not federal:
        return
    try:
        from openparliament_enrich import enrich_federal_rows
        print("\nFetching OpenParliament / House of Commons data for federal match(es)...")
        enrich_federal_rows(federal)
        for m in federal:
            print("-" * 60)
            print(f"Latest federal data for {m.get('name')}:")
            for f in FEDERAL_ENRICHMENT_FIELDS:
                if m.get(f):
                    print(f"{f.replace('_', ' ').title():>22}: {m[f]}")
            print_career(m)
    except Exception as e:
        print(f"  ! Enrichment failed: {e}")


def print_career(row):
    """A few lines on the MP's career from their House of Commons roles
    page (one request)."""
    from openparliament_enrich import house_of_commons_id
    from scrape_careers import career_lines, fetch_career, summarize  # it imports this module

    if not house_of_commons_id(row.get("url")):
        return
    try:
        lines = career_lines(summarize(fetch_career(row["url"])))
    except (FetchError, ValueError) as e:
        print(f"{'Career':>22}: unavailable ({e})")
        return
    print(f"{'Career':>22}: {lines[0]}")
    for line in lines[1:]:
        print(f"{'':>22}  {line}")


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    if args.postal_code or args.point:
        return lookup_location(args)

    if args.live:
        try:
            matches = search_live(args.name, args.category, args.place)
        except FetchError as e:
            explain_fetch_error(e)
            return 1
    else:
        matches = search_cache(args.name, args.category, args.place)

    if not matches:
        print(f"No matches for {args.name!r}.")
        return 1

    print(f"\nFound {len(matches)} match(es) for {args.name!r}:")
    for m in matches:
        print_match(m)
    if not args.no_enrich:
        enrich_and_print_federal(matches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
