#!/usr/bin/env python3
"""
Scrapes Canada's local/municipal politicians (city/town/regional
councillors and mayors) from the Represent API and writes them to:

    ~/Desktop/output/local/all_local.xlsx
    ~/Desktop/output/local/all_local.csv
    ~/Desktop/output/local/all_local.json

It also upserts these rows into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Local", "Changelog" -- other tabs untouched)

Represent tracks about 108 municipal councils (roughly 3,000 people).
They're downloaded in bulk -- a handful of requests in total, well under
Represent's free-tier rate limit -- so a full run takes seconds, not the
10-20 minutes the one-request-per-council approach used to.

Usage:
    python3 scrape_local.py

Optional: limit to councils whose name contains a keyword, and/or to
one province or territory (two-letter code):

    python3 scrape_local.py --filter Toronto
    python3 scrape_local.py --province QC

NOTE ON THE MASTER FILE: if you use --filter or --province, only the
councils that match are compared against the master for "removed"
detection -- councils outside the filter are left untouched, since this
run never saw them.
"""
import argparse
import os
import sys

from changes_digest import report_run
from represent_common import (
    FetchError,
    PROVINCES_AND_TERRITORIES,
    add_common_args,
    apply_common_args,
    explain_fetch_error,
    fetch_category,
    get_master_path,
    get_output_dir,
    province_for_set,
    upsert_master,
    write_csv,
    write_json,
    write_xlsx,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape Canada's municipal councillors and mayors from the Represent API."
    )
    parser.add_argument(
        "--filter",
        metavar="TEXT",
        help="only councils whose name contains TEXT (case-insensitive), e.g. --filter Toronto",
    )
    parser.add_argument(
        "--province",
        metavar="CODE",
        type=str.upper,
        choices=sorted(PROVINCES_AND_TERRITORIES),
        help="only councils in this province/territory, e.g. --province ON",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Local / municipal politician scraper ===")
    name_filter = args.filter.lower() if args.filter else None
    if name_filter:
        print(f"Filtering to representative-sets containing: {name_filter!r}")
    if args.province:
        print(f"Filtering to councils in: {PROVINCES_AND_TERRITORIES[args.province]}")

    def wanted(rep_set):
        if name_filter and name_filter not in rep_set["name"].lower():
            return False
        if args.province and province_for_set(rep_set) != args.province:
            return False
        return True

    narrowed = bool(name_filter or args.province)
    try:
        result = fetch_category("local", set_filter=wanted if narrowed else None)
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    if not result.sets:
        print("No matching local representative-sets found.")
        return 1

    print(f"Found {len(result.sets)} local council(s) to scrape.")

    out_dir = get_output_dir("local")
    all_rows = result.rows
    write_xlsx(os.path.join(out_dir, "all_local.xlsx"), all_rows, sheet_name="Local")
    write_csv(os.path.join(out_dir, "all_local.csv"), all_rows)
    write_json(os.path.join(out_dir, "all_local.json"), all_rows)

    if narrowed:
        print(
            "Note: a filter was used, so only matching councils are being "
            "compared against the master file for this run."
        )
    changes = upsert_master(
        get_master_path(),
        all_rows,
        category_scope="local",
        attempted_sets=result.fetched_sets,
        category_complete=not narrowed and not result.failed_sets,
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")
    report_run(changes)

    print(f"Done. {len(all_rows)} local politician(s) saved to {out_dir}")
    if result.failed_sets:
        print(f"Couldn't download {len(result.failed_sets)} council(s): {', '.join(result.failed_sets)}"
              f" -- re-run later to pick them up.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
