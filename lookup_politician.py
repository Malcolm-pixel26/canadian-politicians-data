#!/usr/bin/env python3
"""
Look up everything currently available on one named politician, without
running a full scrape.

Two modes:

1. Cached lookup (default, fast) -- searches your existing
   ~/Desktop/output/master.xlsx for a name match. Run the full scrapers
   at least once first so there's something to search.

       python3 lookup_politician.py "Jane Doe"

2. Live lookup (--live) -- goes straight to the Represent API instead of
   the cache. Searching everyone at every level of government takes only
   a few requests (Represent's bulk download), so --category and --place
   are optional ways to narrow the results rather than requirements.

       python3 lookup_politician.py "Jane Doe" --live
       python3 lookup_politician.py "Jane Doe" --live --category federal
       python3 lookup_politician.py "Jane Doe" --live --category local --place Toronto

Names match on word prefixes in any order, ignoring accents and case:
"Legault", "francois leg" and "LEGAULT François" all find François
Legault; "Ann" finds "Ann" or "Anne" but not "Joanne".

For a federal match, this also pulls OpenParliament / House of Commons
enrichment (committees, first elected date) live, since that's cheap
for a single person (skip with --no-enrich).

This only pulls from public sources (Represent, OpenParliament, the
House of Commons) that already publish these people's official,
public-facing legislative contact info -- it does not search social
media, news, or anything about their personal/private life.
"""
import argparse
import os
import sys

from represent_common import (
    FetchError,
    FEDERAL_ENRICHMENT_FIELDS,
    MASTER_FIELDS,
    add_common_args,
    apply_common_args,
    classify_set,
    explain_fetch_error,
    fetch_all_representative_sets,
    fetch_rows_for_sets,
    get_master_path,
    normalize_name,
    read_all_master_rows,
)

CATEGORY_ALIASES = {
    "federal": "federal",
    "provincial": "provincial_territorial",
    "provincial_territorial": "provincial_territorial",
    "local": "local",
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Look up one politician by name, from master.xlsx or live from Represent."
    )
    parser.add_argument("name", help='the name to search for, e.g. "Jane Doe"')
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


def search_live(name, category=None, place=None):
    sets = fetch_all_representative_sets()
    if category:
        sets = [s for s in sets if classify_set(s) == category]
    if place:
        sets = [s for s in sets if place.lower() in s["name"].lower()]
        if not sets:
            print(f"No representative-sets matched --place {place!r}.")
            return []

    print(f"Searching {len(sets)} legislature(s)/council(s) live...")
    result = fetch_rows_for_sets(sets)
    return [row for row in result.rows if name_matches(name, row.get("name", ""))]


def print_match(row):
    print("-" * 60)
    for field in MASTER_FIELDS:
        val = row.get(field, "")
        if val not in ("", None):
            print(f"{field.replace('_', ' ').title():>22}: {val}")


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

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

    # Federal matches are cheap to enrich live, on the spot, since it's
    # just one person rather than the whole House.
    federal_matches = [m for m in matches if m.get("category") == "federal"]
    if federal_matches and not args.no_enrich:
        try:
            from openparliament_enrich import enrich_federal_rows
            print("\nFetching OpenParliament / House of Commons data for federal match(es)...")
            enrich_federal_rows(federal_matches)
            for m in federal_matches:
                print("-" * 60)
                print(f"Latest federal data for {m.get('name')}:")
                for f in FEDERAL_ENRICHMENT_FIELDS:
                    if m.get(f):
                        print(f"{f.replace('_', ' ').title():>22}: {m[f]}")
        except Exception as e:
            print(f"  ! Enrichment failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
