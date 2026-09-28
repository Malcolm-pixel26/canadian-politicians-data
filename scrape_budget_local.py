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
import argparse
import sys

from budget_common import (
    BUDGET_FIELDS,
    OUTPUT_FILENAME,
    build_budget_rows as _build_budget_rows,
    collect_from_fetchers,
    read_master_politicians,
    write_budget_rows,
)
from represent_common import add_common_args, apply_common_args

SHEET_NAME = "Local"
FIELDS = BUDGET_FIELDS

# --- Extension point --------------------------------------------------------
# Each entry maps a municipality slug to a fetcher function that takes
# no arguments and returns a "contribution totals" dict, shaped exactly
# like aggregate_contributions() in scrape_budget_federal.py and
# described in budget_common.py: keyed by normalize_name(recipient) ->
# {raw_recipient_name, recipient_type, total_contributions,
# contribution_count, ...}.
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

NOT_COVERED_STATUS = "not covered (no data source configured for this municipality yet)"

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
    configured) with the local politicians in the existing master.xlsx.
    Every local politician in master.xlsx appears in the output, even
    with no funding source configured yet.
    """
    totals = {}
    if only_municipality or MUNICIPAL_FETCHERS:
        totals = collect_from_fetchers(MUNICIPAL_FETCHERS, only_municipality, kind="municipality")
    return _build_budget_rows(read_master_politicians("Local"), totals, NOT_COVERED_STATUS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Write the Local tab of MASTER_CANADA_POLITICIAN_BUDGET.xlsx."
    )
    parser.add_argument(
        "--municipality",
        metavar="SLUG",
        type=lambda s: s.strip().lower(),
        help="only run this municipality's fetcher (once one exists), e.g. --municipality toronto",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Local (municipal) political financing tracker ===")
    if not MUNICIPAL_FETCHERS:
        print(
            "No municipality fetchers configured yet -- writing the 'Local' "
            "tab with 'not covered' status for everyone. See "
            "MUNICIPAL_FETCHERS in this file to add one."
        )

    budget_rows = build_budget_rows(only_municipality=args.municipality)
    out_path = write_budget_rows(SHEET_NAME, budget_rows, NOTES_TEXT)
    print(f"Done. {len(budget_rows)} row(s) saved to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
