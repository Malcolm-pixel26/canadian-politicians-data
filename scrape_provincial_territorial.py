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

Represent currently covers 12 of the 13 legislatures. The 13th,
Nunavut's, is read from the Legislative Assembly of Nunavut's own website
instead (see nunavut.py) and saved the same way, as
nunavut-legislature.xlsx / .csv -- for as long as Represent doesn't have
it. The run prints any other jurisdiction it couldn't find.

Usage:
    python3 scrape_provincial_territorial.py
"""
import argparse
import os
import sys

import nunavut
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


def fetch_nunavut():
    """nunavut.fetch_members(), or None if the Assembly's website couldn't be read."""
    try:
        return nunavut.fetch_members()
    except (FetchError, ValueError) as e:
        print(f"  ! Couldn't read Nunavut's MLAs ({e}) -- the ones already in master.xlsx are left as they were.")
        return None


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
    missing = [name for code, name in sorted(PROVINCES_AND_TERRITORIES.items())
               if code not in covered and code != nunavut.PROVINCE]
    if missing:
        print(f"Not available from Represent: {', '.join(missing)}")
    # Represent has never had Nunavut; if it ever does, it's used instead.
    from_assembly = nunavut.PROVINCE not in covered
    found = fetch_nunavut() if from_assembly else None

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

    if found:
        write_xlsx(os.path.join(out_dir, f"{nunavut.SLUG}.xlsx"), found.rows, sheet_name=nunavut.SET_NAME)
        write_csv(os.path.join(out_dir, f"{nunavut.SLUG}.csv"), found.rows)

    all_rows = result.rows + (found.rows if found else [])
    write_xlsx(
        os.path.join(out_dir, "all_provincial_territorial.xlsx"),
        all_rows,
        sheet_name="Provincial-Territorial",
    )
    write_csv(os.path.join(out_dir, "all_provincial_territorial.csv"), all_rows)
    write_json(os.path.join(out_dir, "all_provincial_territorial.json"), all_rows)

    # Nunavut only counts as checked -- so an MLA missing from it is marked
    # "removed" -- when every member's page was read this run.
    nunavut_complete = bool(found and found.complete)
    attempted = list(result.fetched_sets) + ([nunavut.SET_NAME] if nunavut_complete else [])
    failed = list(result.failed_sets) + ([nunavut.SET_NAME] if from_assembly and not nunavut_complete else [])
    changes = upsert_master(
        get_master_path(),
        all_rows,
        category_scope="provincial_territorial",
        attempted_sets=attempted,
        category_complete=not failed,
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")
    report_run(changes)

    print(f"Done. {len(all_rows)} provincial/territorial politician(s) saved to {out_dir}")
    if failed:
        print(f"Couldn't download: {', '.join(failed)} -- re-run later to pick them up.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
