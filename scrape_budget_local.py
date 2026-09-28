#!/usr/bin/env python3
"""
Tracks municipal campaign-finance data for Canadian local politicians
(councillors, mayors). This is one of three budget scripts -- the
other two are scrape_budget_federal.py and
scrape_budget_provincial_territorial.py. Each one owns exactly one tab
of the shared workbook:

    ~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx
        Federal                  <- written by scrape_budget_federal.py
        Provincial_Territorial   <- written by scrape_budget_provincial_territorial.py
        Local                    <- written by this script
        Notes                    <- one row per tab, upserted by whichever script last ran

Run any of the three, in any order, as often as you like -- each only
touches its own tab and its own row in Notes, so they never clobber
each other. Run scrape_all_budgets.py to update all three in one go.

Scope note (important): there is no unified source for municipal
campaign-finance data in Canada at all -- unlike Elections Canada
(federal) or even the provincial elections bodies, most of Canada's
several hundred municipalities don't publish structured contribution
data anywhere, and the handful that do (typically larger cities, after
an election) each use their own one-off format with no common schema
or bulk download.

So as shipped, this script writes the Local tab with every local
politician from master.xlsx listed as "not covered" -- the tab,
matching logic, and workbook plumbing are all in place and ready to
switch to real numbers the moment a fetcher is added for a given
municipality. See MUNICIPAL_FETCHERS below for exactly where that
plugs in. (This script does NOT touch local politicians' contact/
office info -- that's still fully covered by scrape_local.py ->
master.xlsx; this script is about campaign *funding* specifically.)

Usage:
    python3 scrape_budget_local.py
    python3 scrape_budget_local.py --municipality toronto   # once a fetcher exists for it
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
SHEET_NAME = "Local"

# Shared column layout across all three budget tabs (Federal,
# Provincial_Territorial, Local) so the tabs are easy to compare.
FIELDS = [
    "name", "category", "party_name", "representative_set",
    "recipient_type", "total_contributions", "contribution_count",
    "avg_contribution", "match_status",
]

# --- Extension point --------------------------------------------------------
# Each entry maps a municipality slug to a fetcher function that takes
# no arguments and returns a dict shaped exactly like
# aggregate_contributions() in scrape_budget_federal.py: keyed by
# normalize_name(recipient) -> {raw_recipient_name, recipient_type,
# total_contributions, contribution_count}.
#
# None are implemented yet. A handful of the largest cities (e.g.
# Toronto, Vancouver, Calgary) have published campaign contribution
# disclosures after past elections -- worth checking that municipality's
# open-data portal (e.g. Toronto's https://open.toronto.ca) before
# writing a fetcher, since the format/availability changes election to
# election and isn't guaranteed to be current. To add one: write
# `fetch_<municipality>_contributions()`, add it to this dict, then
# rows for that municipality will automatically get real funding
# numbers instead of "not covered".
MUNICIPAL_FETCHERS = {
    # "toronto": fetch_toronto_contributions,
}

NOTES_TEXT = (
    "There is no unified source for municipal campaign-finance data in "
    "Canada -- most municipalities don't publish structured contribution "
    "data at all, and the few that do each use their own one-off format. "
    "No municipality fetcher is wired in yet, so every row here is 'not "
    "covered' pending a fetcher for that specific municipality -- see "
    "MUNICIPAL_FETCHERS in scrape_budget_local.py. (Office/contact info "
    "for these politicians, as opposed to funding, is still fully "
    "covered by scrape_local.py -> master.xlsx.)"
)


def build_budget_rows(only_municipality=None):
    """
    Merge aggregated contribution totals (if any fetchers are
    configured) with the local politicians in the existing master.xlsx,
    matched by normalized name. Every local politician in master.xlsx
    appears in the output, even with no funding source configured yet.
    """
    master_path = get_master_path()
    master_rows = read_master_tab(master_path, "Local")

    contribution_totals = {}
    if only_municipality:
        fetcher = MUNICIPAL_FETCHERS.get(only_municipality)
        if not fetcher:
            configured = sorted(MUNICIPAL_FETCHERS) or "none configured yet"
            raise SystemExit(
                f"No fetcher configured for '{only_municipality}'. Configured: {configured}"
            )
        print(f"Fetching {only_municipality}...")
        contribution_totals = fetcher()
    elif MUNICIPAL_FETCHERS:
        for slug, fetcher in MUNICIPAL_FETCHERS.items():
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
                    "municipality yet)"
                ),
            })
        budget_rows.append(entry)

    # Recipients from a configured fetcher that never matched anyone in
    # master.xlsx -- keep them too, so no funding data silently
    # disappears.
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
    only_municipality = None
    if "--municipality" in argv:
        idx = argv.index("--municipality")
        if idx + 1 < len(argv):
            only_municipality = argv[idx + 1].strip().lower()
    return only_municipality


def main():
    only_municipality = parse_args(sys.argv[1:])

    print("=== Local (municipal) political financing tracker ===")
    if not MUNICIPAL_FETCHERS:
        print(
            "No municipality fetchers configured yet -- writing the 'Local' "
            "tab with 'not covered' status for everyone. See "
            "MUNICIPAL_FETCHERS in this file to add one."
        )

    budget_rows = build_budget_rows(only_municipality=only_municipality)

    out_path = f"{get_output_dir()}/{OUTPUT_FILENAME}"
    write_budget_tab(out_path, SHEET_NAME, FIELDS, budget_rows, "Local", NOTES_TEXT)
    print(f"Done. {len(budget_rows)} row(s) saved to {out_path}")


if __name__ == "__main__":
    main()
