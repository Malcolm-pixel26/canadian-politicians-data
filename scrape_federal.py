#!/usr/bin/env python3
"""
Scrapes Canada's federal politicians (Members of Parliament, House of
Commons) from the Represent API and writes them to:

    ~/Desktop/output/federal/federal_mps.xlsx
    ~/Desktop/output/federal/federal_mps.csv
    ~/Desktop/output/federal/federal_mps.json

It also upserts these rows into the shared, persistent workbook:

    ~/Desktop/output/master.xlsx   (tabs: "Federal", "Changelog" -- other tabs untouched)

By default it also enriches each MP with a couple of extra public
fields from the OpenParliament API (first elected date, current
committee memberships, OpenParliament profile URL). Skip this with
--no-enrich if you just want the base Represent fields, or if you
don't have network access to openparliament.ca.

Usage:
    python3 scrape_federal.py
    python3 scrape_federal.py --no-enrich
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


def main():
    enrich = "--no-enrich" not in sys.argv

    print("=== Federal politician scraper (House of Commons) ===")
    all_sets = fetch_all_representative_sets()
    federal_sets = [s for s in all_sets if classify_set(s) == "federal"]

    if not federal_sets:
        print("Could not find a federal representative-set -- the API may have changed.")
        return

    rows = []
    for rep_set in federal_sets:
        reps = fetch_representatives_by_url(
            rep_set["related"]["representatives_url"], rep_set["name"]
        )
        rows.extend(flatten_representative(r, "federal") for r in reps)

    if enrich:
        try:
            from openparliament_enrich import enrich_federal_rows
            enrich_federal_rows(rows)
        except Exception as e:
            print(f"  ! OpenParliament enrichment failed ({e}) -- continuing with base data only.")

    out_dir = get_output_dir("federal")
    write_xlsx(os.path.join(out_dir, "federal_mps.xlsx"), rows, sheet_name="Federal MPs")
    write_csv(os.path.join(out_dir, "federal_mps.csv"), rows)
    write_json(os.path.join(out_dir, "federal_mps.json"), rows)

    changes = upsert_master(get_master_path(), rows, category_scope="federal")
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")

    print(f"Done. {len(rows)} federal politician(s) saved to {out_dir}")


if __name__ == "__main__":
    main()
