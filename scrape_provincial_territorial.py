#!/usr/bin/env python3
"""
Scrapes Canada's provincial and territorial politicians (MPPs/MLAs/MNAs/
MHAs, i.e. members of each province's/territory's legislature) from the
Represent API and writes them to:

    ~/Desktop/output/provincial_territorial/all_provincial_territorial.xlsx
    ~/Desktop/output/provincial_territorial/all_provincial_territorial.csv
    ~/Desktop/output/provincial_territorial/all_provincial_territorial.json
    ~/Desktop/output/provincial_territorial/<legislature-slug>.xlsx  (one per legislature)
    ~/Desktop/output/provincial_territorial/<legislature-slug>.csv   (one per legislature)

It also upserts these rows into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Provincial_Territorial", "Changelog" -- other tabs untouched)

Represent currently covers 12 of the 13 legislatures -- there is no
Nunavut set -- and the run prints any jurisdiction it couldn't find.

Usage:
    python3 scrape_provincial_territorial.py
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
    slug_from_set,
    upsert_master,
    write_csv,
    write_json,
    write_xlsx,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape Canada's provincial/territorial legislators from the Represent API."
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Provincial / territorial politician scraper ===")
    try:
        result = fetch_category("provincial_territorial")
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    if not result.sets:
        print("Could not find any provincial/territorial representative-sets.")
        return 1

    print(f"Found {len(result.sets)} provincial/territorial legislature(s):")
    for s in result.sets:
        print(f"  - {s['name']}")
    covered = {province_for_set(s) for s in result.sets}
    missing = [name for code, name in sorted(PROVINCES_AND_TERRITORIES.items()) if code not in covered]
    if missing:
        print(f"Not available from Represent: {', '.join(missing)}")

    out_dir = get_output_dir("provincial_territorial")
    rows_by_set = {}
    for row in result.rows:
        rows_by_set.setdefault(row["representative_set"], []).append(row)

    for rep_set in result.sets:
        if rep_set["name"] not in result.fetched_sets:
            continue
        rows = rows_by_set.get(rep_set["name"], [])
        # One file per legislature, e.g. ontario-legislature.xlsx / .csv
        slug = slug_from_set(rep_set)
        write_xlsx(os.path.join(out_dir, f"{slug}.xlsx"), rows, sheet_name=rep_set["name"])
        write_csv(os.path.join(out_dir, f"{slug}.csv"), rows)

    all_rows = result.rows
    write_xlsx(
        os.path.join(out_dir, "all_provincial_territorial.xlsx"),
        all_rows,
        sheet_name="Provincial-Territorial",
    )
    write_csv(os.path.join(out_dir, "all_provincial_territorial.csv"), all_rows)
    write_json(os.path.join(out_dir, "all_provincial_territorial.json"), all_rows)

    changes = upsert_master(
        get_master_path(),
        all_rows,
        category_scope="provincial_territorial",
        attempted_sets=result.fetched_sets,
        category_complete=not result.failed_sets,
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")
    report_run(changes)

    print(f"Done. {len(all_rows)} provincial/territorial politician(s) saved to {out_dir}")
    if result.failed_sets:
        print(f"Couldn't download: {', '.join(result.failed_sets)} -- re-run later to pick them up.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
