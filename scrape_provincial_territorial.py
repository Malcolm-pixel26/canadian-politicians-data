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

Usage:
    python3 scrape_provincial_territorial.py
"""
import os
from represent_common import (
    fetch_all_representative_sets,
    fetch_representatives_by_url,
    flatten_representative,
    classify_set,
    slug_from_set,
    get_output_dir,
    get_master_path,
    upsert_master,
    write_csv,
    write_json,
    write_xlsx,
)


def main():
    print("=== Provincial / territorial politician scraper ===")
    all_sets = fetch_all_representative_sets()
    prov_sets = [s for s in all_sets if classify_set(s) == "provincial_territorial"]

    if not prov_sets:
        print("Could not find any provincial/territorial representative-sets.")
        return

    print(f"Found {len(prov_sets)} provincial/territorial legislature(s):")
    for s in prov_sets:
        print(f"  - {s['name']}")

    out_dir = get_output_dir("provincial_territorial")
    all_rows = []

    for rep_set in prov_sets:
        reps = fetch_representatives_by_url(
            rep_set["related"]["representatives_url"], rep_set["name"]
        )
        rows = [flatten_representative(r, "provincial_territorial") for r in reps]
        all_rows.extend(rows)

        # One file per legislature, e.g. ontario-legislature.xlsx / .csv
        slug = slug_from_set(rep_set)
        write_xlsx(os.path.join(out_dir, f"{slug}.xlsx"), rows, sheet_name=rep_set["name"])
        write_csv(os.path.join(out_dir, f"{slug}.csv"), rows)

    write_xlsx(
        os.path.join(out_dir, "all_provincial_territorial.xlsx"),
        all_rows,
        sheet_name="Provincial-Territorial",
    )
    write_csv(os.path.join(out_dir, "all_provincial_territorial.csv"), all_rows)
    write_json(os.path.join(out_dir, "all_provincial_territorial.json"), all_rows)

    changes = upsert_master(get_master_path(), all_rows, category_scope="provincial_territorial")
    print(f"Master workbook: {len(changes)} change(s) recorded this run.")

    print(f"Done. {len(all_rows)} provincial/territorial politician(s) saved to {out_dir}")


if __name__ == "__main__":
    main()
