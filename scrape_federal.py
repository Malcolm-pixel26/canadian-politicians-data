#!/usr/bin/env python3
"""
Scrapes Canada's federal politicians (Members of Parliament, House of
Commons) from the Represent API and writes them to:

    ~/Desktop/output/federal/federal_mps.xlsx
    ~/Desktop/output/federal/federal_mps.csv
    ~/Desktop/output/federal/federal_mps.json

It also upserts these rows into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Federal", "Changelog" -- other tabs untouched)

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

from represent_common import (
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
        description="Scrape Canada's federal MPs (House of Commons) from the Represent API."
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="skip the OpenParliament / House of Commons extras (first elected date, committees)",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Federal politician scraper (House of Commons) ===")
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

    rows = result.rows
    if not args.no_enrich:
        try:
            from openparliament_enrich import enrich_federal_rows
            enrich_federal_rows(rows)
        except Exception as e:
            print(f"  ! Enrichment failed ({e}) -- continuing with base data only.")

    out_dir = get_output_dir("federal")
    write_xlsx(os.path.join(out_dir, "federal_mps.xlsx"), rows, sheet_name="Federal MPs", fields=FEDERAL_FIELDS)
    write_csv(os.path.join(out_dir, "federal_mps.csv"), rows, fields=FEDERAL_FIELDS)
    write_json(os.path.join(out_dir, "federal_mps.json"), rows)

    changes = upsert_master(
        get_master_path(),
        rows,
        category_scope="federal",
        attempted_sets=result.fetched_sets,
        category_complete=not result.failed_sets,
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")

    print(f"Done. {len(rows)} federal politician(s) saved to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
