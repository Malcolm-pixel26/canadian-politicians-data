#!/usr/bin/env python3
"""
Look up everything currently available on one named politician, without
running a full scrape.

Two modes:

1. Cached lookup (default, fast) -- searches your existing
   ~/Desktop/output/master.xlsx for a name match. Run the full scrapers
   at least once first so there's something to search.

       python3 lookup_politician.py "Jane Doe"

2. Live lookup (--live, slower) -- goes straight to the Represent API
   instead of the cache. You must narrow it with --category, because
   scanning *all* local councils live for a name match would mean
   fetching several hundred councils one at a time. federal is fast
   (one request); provincial_territorial is fast-ish (13 legislatures);
   local can still take a while even filtered.

       python3 lookup_politician.py "Jane Doe" --live --category federal
       python3 lookup_politician.py "Jane Doe" --live --category local --place Toronto

For a federal match, this also pulls OpenParliament enrichment
(committees, first elected date) live, since that's cheap for a single
person.

This only pulls from public sources (Represent, OpenParliament) that
already publish these people's official, public-facing legislative
contact info -- it does not search social media, news, or anything
about their personal/private life.
"""
import os
import sys

from represent_common import (
    get_master_path,
    read_all_master_rows,
    normalize_name,
    fetch_all_representative_sets,
    fetch_representatives_by_url,
    flatten_representative,
    classify_set,
    MASTER_FIELDS,
)


def parse_args(argv):
    if not argv:
        print(__doc__)
        sys.exit(1)
    name = argv[0]
    live = "--live" in argv
    category = None
    if "--category" in argv:
        idx = argv.index("--category")
        if idx + 1 < len(argv):
            category = argv[idx + 1]
    place_filter = None
    if "--place" in argv:
        idx = argv.index("--place")
        if idx + 1 < len(argv):
            place_filter = argv[idx + 1].lower()
    return name, live, category, place_filter


def search_cache(name):
    master_path = get_master_path()
    if not os.path.exists(master_path):
        print(f"No master file found at {master_path} yet -- run a scraper first, "
              f"or use --live --category <federal|provincial_territorial|local>.")
        return []

    existing = read_all_master_rows(master_path)
    needle = normalize_name(name)
    matches = [row for row in existing.values() if needle in normalize_name(row.get("name", ""))]
    return matches


def search_live(name, category, place_filter):
    if category not in ("federal", "provincial_territorial", "local"):
        print("For --live, pass --category federal|provincial_territorial|local.")
        sys.exit(1)

    print(f"Fetching representative-sets to search live ({category})...")
    all_sets = fetch_all_representative_sets()
    sets_to_search = [s for s in all_sets if classify_set(s) == category]

    if place_filter:
        sets_to_search = [s for s in sets_to_search if place_filter in s["name"].lower()]
        if not sets_to_search:
            print(f"No {category} representative-sets matched --place {place_filter!r}.")
            return []

    if category == "local" and not place_filter:
        print(
            f"Warning: searching all {len(sets_to_search)} local councils live -- "
            f"this can take a while. Consider adding --place <city> to narrow it down."
        )

    needle = normalize_name(name)
    matches = []
    for i, rep_set in enumerate(sets_to_search, start=1):
        print(f"  [{i}/{len(sets_to_search)}] searching {rep_set['name']}...")
        reps = fetch_representatives_by_url(rep_set["related"]["representatives_url"], rep_set["name"])
        for r in reps:
            if needle in normalize_name(r.get("name", "")):
                matches.append(flatten_representative(r, category))
    return matches


def print_match(row):
    print("-" * 60)
    for field in MASTER_FIELDS:
        val = row.get(field, "")
        if val:
            print(f"{field.replace('_', ' ').title():>20}: {val}")


def main():
    name, live, category, place_filter = parse_args(sys.argv[1:])

    if live:
        matches = search_live(name, category, place_filter)
    else:
        matches = search_cache(name)

    if not matches:
        print(f"No matches for {name!r}.")
        return

    print(f"\nFound {len(matches)} match(es) for {name!r}:")
    federal_matches = [m for m in matches if m.get("category") == "federal"]

    for m in matches:
        print_match(m)

    # Federal matches are cheap to enrich live, on the spot, since it's
    # just one person rather than the whole House.
    if federal_matches:
        try:
            from openparliament_enrich import enrich_federal_rows
            print("\nFetching OpenParliament enrichment for federal match(es)...")
            enrich_federal_rows(federal_matches)
            for m in federal_matches:
                print("-" * 60)
                print(f"OpenParliament data for {m.get('name')}:")
                for f in ("openparliament_url", "first_elected", "committees"):
                    if m.get(f):
                        print(f"{f.replace('_', ' ').title():>20}: {m[f]}")
        except Exception as e:
            print(f"  ! OpenParliament enrichment failed: {e}")


if __name__ == "__main__":
    main()
