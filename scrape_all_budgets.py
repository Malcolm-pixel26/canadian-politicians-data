#!/usr/bin/env python3
"""
Runs all three budget scripts in sequence -- federal, provincial/
territorial, then local -- each updating its own tab of:

    ~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx

This is the budget-tracking equivalent of scrape_all.py (which scrapes
office/contact info instead of funding). Run scrape_all.py (or the
individual scrape_federal.py / scrape_provincial_territorial.py /
scrape_local.py scripts) first if you haven't populated master.xlsx
yet -- these budget scripts match against whatever's already in there.

Usage:
    python3 scrape_all_budgets.py
    python3 scrape_all_budgets.py --refresh --since 2015   # flags are passed through to scrape_budget_federal.py
"""
import sys

import scrape_budget_federal
import scrape_budget_provincial_territorial
import scrape_budget_local
from represent_common import get_output_dir


def main():
    argv = sys.argv[1:]

    print("### 1/3: Federal ###")
    force_refresh, since_year = scrape_budget_federal.parse_args(argv)
    zip_path = scrape_budget_federal.download_contributions_zip(force=force_refresh)
    scrape_budget_federal.inspect_columns(zip_path)
    contribution_totals = scrape_budget_federal.read_contributions_from_zip(zip_path, since_year=since_year)
    budget_rows = scrape_budget_federal.build_budget_rows(contribution_totals)
    out_path = f"{get_output_dir()}/{scrape_budget_federal.OUTPUT_FILENAME}"
    scrape_budget_federal.write_budget_tab(
        out_path, scrape_budget_federal.SHEET_NAME, scrape_budget_federal.FIELDS,
        budget_rows, "Federal", scrape_budget_federal.NOTES_TEXT,
    )
    print()

    print("### 2/3: Provincial/Territorial ###")
    scrape_budget_provincial_territorial.main()
    print()

    print("### 3/3: Local ###")
    scrape_budget_local.main()
    print()

    print(f"All three budget tabs updated -> {out_path}")


if __name__ == "__main__":
    main()
