#!/usr/bin/env python3
"""
Scrapes Canada's federal politicians -- Members of Parliament (House of
Commons) from the Represent API, and senators from the Senate's own
website (see senate.py) -- and writes them to:

    ~/Desktop/output/federal/federal_mps.xlsx
    ~/Desktop/output/federal/federal_mps.csv
    ~/Desktop/output/federal/federal_mps.json
    ~/Desktop/output/federal/senators.xlsx
    ~/Desktop/output/federal/senators.csv
    ~/Desktop/output/federal/senators.json

It also upserts both into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Federal", "Changelog" -- other tabs untouched)

Senators are listed as representative set "Senate of Canada", office
"Senator", with their province as the district and their affiliation
(caucus or group) as the party. If the Senate's website can't be read,
the MPs are still saved and the senators already in master.xlsx are left
as they were.

By default it also enriches each MP with a few extra public fields:
first elected date and OpenParliament profile URL (from the
OpenParliament API) and current committee memberships (from the House
of Commons' own open data). Skip this with --no-enrich if you just want
the base Represent fields, or if you don't have network access to
openparliament.ca / ourcommons.ca.

Usage:
    python3 scrape_federal.py
    python3 scrape_federal.py --no-enrich
    python3 scrape_federal.py --output-dir D:\\politicians
"""
import argparse
import os
import sys

import senate
from changes_digest import report_run
from represent_common import (
    CSV_FIELDS,
    FEDERAL_FIELDS,
    FetchError,
    add_common_args,
    apply_common_args,
    explain_fetch_error,
    fetch_category,
    get_master_path,
    get_output_dir,
    upsert_master,
    write_csv,
    write_json,
    write_xlsx,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape Canada's federal MPs (House of Commons, from the Represent API) "
                    "and senators (from sencanada.ca)."
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="skip the OpenParliament / House of Commons extras (first elected date, committees)",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def fetch_senate():
    """Every senator (senate.fetch_senators()), or None if the Senate's
    website couldn't be read."""
    try:
        return senate.fetch_senators()
    except (FetchError, ValueError) as e:
        print(f"  ! Couldn't read the list of senators ({e}) -- the senators already in master.xlsx "
              f"are left as they were.")
        return None


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Federal politician scraper (House of Commons and Senate) ===")
    try:
        result = fetch_category("federal")
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    if not result.sets:
        print("Could not find a federal representative-set -- the API may have changed.")
        return 1
    if not result.fetched_sets:
        print("Couldn't download the House of Commons from Represent -- nothing was changed.")
        return 1

    mps = result.rows
    if not args.no_enrich:
        try:
            from openparliament_enrich import enrich_federal_rows
            enrich_federal_rows(mps)
        except Exception as e:
            print(f"  ! Enrichment failed ({e}) -- continuing with base data only.")
    senators = fetch_senate()

    out_dir = get_output_dir("federal")
    write_xlsx(os.path.join(out_dir, "federal_mps.xlsx"), mps, sheet_name="Federal MPs", fields=FEDERAL_FIELDS)
    write_csv(os.path.join(out_dir, "federal_mps.csv"), mps, fields=FEDERAL_FIELDS)
    write_json(os.path.join(out_dir, "federal_mps.json"), mps)
    if senators:
        write_xlsx(os.path.join(out_dir, "senators.xlsx"), senators, sheet_name="Senators", fields=CSV_FIELDS)
        write_csv(os.path.join(out_dir, "senators.csv"), senators, fields=CSV_FIELDS)
        write_json(os.path.join(out_dir, "senators.json"), senators)

    # The Senate only counts as checked -- so a senator missing from it is
    # marked "removed" -- when its list was actually read this run.
    attempted = list(result.fetched_sets) + ([senate.SET_NAME] if senators else [])
    changes = upsert_master(
        get_master_path(),
        mps + (senators or []),
        category_scope="federal",
        attempted_sets=attempted,
        category_complete=not result.failed_sets and senators is not None,
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")
    report_run(changes)

    print(f"Done. {len(mps)} MP(s) and {len(senators or [])} senator(s) saved to {out_dir}")
    if senators is None:
        print("Couldn't download the Senate -- re-run later to pick it up.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
