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
import argparse
import sys
import traceback

import scrape_budget_federal
import scrape_budget_local
import scrape_budget_provincial_territorial
from budget_common import budget_workbook_path
from represent_common import add_common_args, apply_common_args


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Update all three tabs of MASTER_CANADA_POLITICIAN_BUDGET.xlsx."
    )
    parser.add_argument("--refresh", action="store_true", help="passed to scrape_budget_federal.py")
    parser.add_argument("--since", type=int, metavar="YEAR", help="passed to scrape_budget_federal.py")
    parser.add_argument("--reviewed", action="store_true", help="passed to scrape_budget_federal.py")
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    steps = [
        ("1/3: Federal", lambda: scrape_budget_federal.run(args)),
        ("2/3: Provincial/Territorial", lambda: scrape_budget_provincial_territorial.main([])),
        ("3/3: Local", lambda: scrape_budget_local.main([])),
    ]
    problems = []
    for label, step in steps:
        print(f"### {label} ###")
        try:
            exit_code = step()
        except Exception:
            traceback.print_exc()
            exit_code = 1
        if exit_code:
            problems.append(label)
        print()

    if problems:
        print(f"Budget workbook: {budget_workbook_path()} -- these steps failed (see above): {', '.join(problems)}")
        return 1
    print(f"All three budget tabs updated -> {budget_workbook_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
