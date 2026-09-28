#!/usr/bin/env python3
"""
Scrapes Canada's local/municipal politicians (city/town/regional
councillors and mayors) from the Represent API and writes them to:

    ~/Desktop/output/local/all_local.xlsx
    ~/Desktop/output/local/all_local.csv
    ~/Desktop/output/local/all_local.json

It also upserts these rows into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Local", "Changelog" -- other tabs untouched)

NOTE: Represent tracks several hundred municipal councils across
Canada. This script has to make one API request per council (plus
extra requests for councils with many members), so it can take
10-20+ minutes to run and will print progress as it goes. It respects
Represent's free-tier rate limit (60 requests/minute).

Usage:
    python3 scrape_local.py

Optional: limit to municipalities whose name contains a keyword, e.g.
to only grab Ontario municipalities (whose Represent set names contain
"City Council", "Town Council", etc. and are typically prefixed with
the municipality name -- filtering isn't province-aware in the API, so
this is a simple substring filter over the set name):

    python3 scrape_local.py --filter Toronto

NOTE ON THE MASTER FILE: if you use --filter, only councils matching
the filter are compared against the master for "removed" detection --
councils outside the filter are left untouched, since this run never
saw them.
"""
import os
import sys
from represent_common import (
    fetch_all_representative_sets,
    fetch_representatives_by_url,
    flatten_representative,
    classify_set,
    get_output_dir,
    get_master_path,
    upsert_master,
    write_csv,
    write_json,
    write_xlsx,
)


def parse_filter_arg():
    if "--filter" in sys.argv:
        idx = sys.argv.index("--filter")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].lower()
    return None


def main():
    print("=== Local / municipal politician scraper ===")
    name_filter = parse_filter_arg()
    if name_filter:
        print(f"Filtering to representative-sets containing: {name_filter!r}")

    all_sets = fetch_all_representative_sets()
    local_sets = [s for s in all_sets if classify_set(s) == "local"]

    if name_filter:
        local_sets = [s for s in local_sets if name_filter in s["name"].lower()]

    if not local_sets:
        print("No matching local representative-sets found.")
        return

    print(f"Found {len(local_sets)} local council(s) to scrape.")

    out_dir = get_output_dir("local")
    all_rows = []

    for i, rep_set in enumerate(local_sets, start=1):
        print(f"[{i}/{len(local_sets)}] {rep_set['name']}")
        reps = fetch_representatives_by_url(
            rep_set["related"]["representatives_url"], rep_set["name"]
        )
        all_rows.extend(flatten_representative(r, "local") for r in reps)

        # Save progress periodically in case a long run gets interrupted
        # (CSV only here -- cheap to rewrite; the .xlsx is written once at the end)
        if i % 25 == 0:
            write_csv(os.path.join(out_dir, "all_local.csv"), all_rows)

    write_xlsx(os.path.join(out_dir, "all_local.xlsx"), all_rows, sheet_name="Local")
    write_csv(os.path.join(out_dir, "all_local.csv"), all_rows)
    write_json(os.path.join(out_dir, "all_local.json"), all_rows)

    if name_filter:
        print(
            "Note: --filter was used, so only matching councils are being "
            "compared against the master file for this run."
        )
    attempted_sets = {s["name"] for s in local_sets} if name_filter else None
    changes = upsert_master(
        get_master_path(), all_rows, category_scope="local", attempted_sets=attempted_sets
    )
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")

    print(f"Done. {len(all_rows)} local politician(s) saved to {out_dir}")


if __name__ == "__main__":
    main()
