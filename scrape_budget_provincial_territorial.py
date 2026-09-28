#!/usr/bin/env python3
"""
Tracks provincial/territorial campaign-finance data for Canadian
politicians. This is one of three budget scripts -- the other two are
scrape_budget_federal.py and scrape_budget_local.py. Each one owns
exactly one tab of the shared workbook:

    ~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx
        Federal                  <- written by scrape_budget_federal.py
        Provincial_Territorial   <- written by this script
        Local                    <- written by scrape_budget_local.py
        Notes                    <- one row per tab, upserted by whichever script last ran

Run any of the three, in any order, as often as you like -- each only
touches its own tab and its own row in Notes, so they never clobber
each other. Run scrape_all_budgets.py to update all three in one go.

Scope note (important): unlike the federal government, there's no
single source for this. Each of the 10 provinces + 3 territories runs
its own elections body with its own campaign-finance disclosure system
and file format, and most don't offer a bulk/CSV download the way
Elections Canada does -- several only publish contributions through a
paginated, JavaScript-rendered search tool on their own website (e.g.
Ontario's https://finances.elections.on.ca/en/contributions, which
returns 450,000+ individual contribution records one page at a time
with no underlying data file to download), which can't be read the
same way as Elections Canada's single CSV.

So as shipped, this script writes the Provincial_Territorial tab with
every provincial/territorial politician from master.xlsx listed as
"not covered" -- the tab, matching logic, and workbook plumbing are
all in place and ready to switch to real numbers the moment a fetcher
is added for a given province/territory. See PROVINCE_FETCHERS below
for exactly where that plugs in. (This script does NOT touch
provincial/territorial politicians' contact/office info -- that's
still fully covered by scrape_provincial_territorial.py -> master.xlsx;
this script is about campaign *funding* specifically.)

Usage:
    python3 scrape_budget_provincial_territorial.py
    python3 scrape_budget_provincial_territorial.py --province ontario   # once a fetcher exists for it
"""
import sys

from represent_common import (
    get_output_dir,
    get_master_path,
    read_master_tab,
    normalize_name,
    write_budget_tab,
)

OUTPUT_FILENAME = "MASTER_CANADA_POLITICIAN_BUDGET.xlsx"
SHEET_NAME = "Provincial_Territorial"

# Shared column layout across all three budget tabs (Federal,
# Provincial_Territorial, Local) so the tabs are easy to compare.
FIELDS = [
    "name", "category", "party_name", "representative_set",
    "recipient_type", "total_contributions", "contribution_count",
    "avg_contribution", "match_status",
]

# --- Extension point --------------------------------------------------------
# Each entry maps a province/territory slug to a fetcher function that
# takes no arguments and returns a dict shaped exactly like
# aggregate_contributions() in scrape_budget_federal.py: keyed by
# normalize_name(recipient) -> {raw_recipient_name, recipient_type,
# total_contributions, contribution_count}.
#
# None are implemented yet -- each province/territory needs its own
# fetcher written and tested against its real disclosure site before it
# can go here (format, pagination, and whether a bulk download even
# exists all vary). Known disclosure entry points, for reference:
#   - Ontario:   https://finances.elections.on.ca/en/contributions
#   - BC:        https://elections.bc.ca (financial reports / PDFs & CSVs)
#   - Quebec:    https://www.electionsquebec.qc.ca
#   - Alberta:   https://efpublic.elections.ab.ca
#   - Manitoba, Saskatchewan, Atlantic provinces, territories: each runs
#     its own elections office site -- check for a data/reports section.
# To add one: write `fetch_<province>_contributions()`, add it to this
# dict, then rows for that province will automatically get real funding
# numbers instead of "not covered".
PROVINCE_FETCHERS = {
    # "ontario": fetch_ontario_contributions,
}

NOTES_TEXT = (
    "Provincial/territorial campaign-finance disclosure is run separately "
    "by each of the 10 provinces + 3 territories, in different formats, "
    "and most don't offer a bulk CSV download the way Elections Canada "
    "does. No province/territory fetcher is wired in yet, so every row "
    "here is 'not covered' pending a fetcher for that specific "
    "jurisdiction -- see PROVINCE_FETCHERS in "
    "scrape_budget_provincial_territorial.py. (Office/contact info for "
    "these politicians, as opposed to funding, is still fully covered "
    "by scrape_provincial_territorial.py -> master.xlsx.)"
)


def build_budget_rows(only_province=None):
    """
    Merge aggregated contribution totals (if any fetchers are
    configured) with the provincial/territorial politicians in the
    existing master.xlsx, matched by normalized name. Every
    provincial/territorial politician in master.xlsx appears in the
    output, even with no funding source configured yet.
    """
    master_path = get_master_path()
    master_rows = read_master_tab(master_path, "Provincial_Territorial")

    contribution_totals = {}
    if only_province:
        fetcher = PROVINCE_FETCHERS.get(only_province)
        if not fetcher:
            configured = sorted(PROVINCE_FETCHERS) or "none configured yet"
            raise SystemExit(
                f"No fetcher configured for '{only_province}'. Configured: {configured}"
            )
        print(f"Fetching {only_province}...")
        contribution_totals = fetcher()
    elif PROVINCE_FETCHERS:
        for slug, fetcher in PROVINCE_FETCHERS.items():
            print(f"Fetching {slug}...")
            contribution_totals.update(fetcher())

    budget_rows = []
    matched_keys = set()

    for row in master_rows.values():
        key = normalize_name(row.get("name", ""))
        contrib = contribution_totals.get(key)
        entry = {
            "name": row.get("name", ""),
            "category": row.get("category", ""),
            "party_name": row.get("party_name", ""),
            "representative_set": row.get("representative_set", ""),
        }
        if contrib:
            matched_keys.add(key)
            count = contrib["contribution_count"]
            total = contrib["total_contributions"]
            entry.update({
                "recipient_type": contrib.get("recipient_type", ""),
                "total_contributions": round(total, 2),
                "contribution_count": count,
                "avg_contribution": round(total / count, 2) if count else 0,
                "match_status": "matched",
            })
        else:
            entry.update({
                "recipient_type": "",
                "total_contributions": "",
                "contribution_count": "",
                "avg_contribution": "",
                "match_status": (
                    "not covered (no data source configured for this "
                    "province/territory yet)"
                ),
            })
        budget_rows.append(entry)

    # Recipients from a configured fetcher that never matched anyone in
    # master.xlsx (e.g. a party entity rather than an individual) --
    # keep them too, so no funding data silently disappears.
    for key, contrib in contribution_totals.items():
        if key in matched_keys:
            continue
        count = contrib["contribution_count"]
        total = contrib["total_contributions"]
        budget_rows.append({
            "name": contrib["raw_recipient_name"],
            "category": "",
            "party_name": "",
            "representative_set": "",
            "recipient_type": contrib.get("recipient_type", ""),
            "total_contributions": round(total, 2),
            "contribution_count": count,
            "avg_contribution": round(total / count, 2) if count else 0,
            "match_status": "unmatched recipient (not in master.xlsx)",
        })

    return budget_rows


def parse_args(argv):
    only_province = None
    if "--province" in argv:
        idx = argv.index("--province")
        if idx + 1 < len(argv):
            only_province = argv[idx + 1].strip().lower()
    return only_province


def main():
    only_province = parse_args(sys.argv[1:])

    print("=== Provincial/territorial political financing tracker ===")
    if not PROVINCE_FETCHERS:
        print(
            "No province/territory fetchers configured yet -- writing the "
            "'Provincial_Territorial' tab with 'not covered' status for "
            "everyone. See PROVINCE_FETCHERS in this file to add one."
        )

    budget_rows = build_budget_rows(only_province=only_province)

    out_path = f"{get_output_dir()}/{OUTPUT_FILENAME}"
    write_budget_tab(out_path, SHEET_NAME, FIELDS, budget_rows, "Provincial_Territorial", NOTES_TEXT)
    print(f"Done. {len(budget_rows)} row(s) saved to {out_path}")


if __name__ == "__main__":
    main()
