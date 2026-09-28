#!/usr/bin/env python3
"""
Runs all three scrapers in sequence: federal, provincial/territorial,
then local. The representative-set list and the bulk download of every
representative are fetched once and shared by all three, and a failure
in one scraper doesn't stop the others.

Usage:
    python3 scrape_all.py
    python3 scrape_all.py --no-enrich            # passed to scrape_federal.py
    python3 scrape_all.py --province ON          # passed to scrape_local.py (so is --filter)
"""
import argparse
import sys
import traceback

import scrape_federal
import scrape_local
import scrape_provincial_territorial
from represent_common import (
    PROVINCES_AND_TERRITORIES,
    add_common_args,
    apply_common_args,
    get_master_path,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the federal, provincial/territorial and local scrapers in sequence."
    )
    parser.add_argument("--no-enrich", action="store_true", help="passed to scrape_federal.py")
    parser.add_argument("--filter", metavar="TEXT", help="passed to scrape_local.py")
    parser.add_argument(
        "--province",
        metavar="CODE",
        type=str.upper,
        choices=sorted(PROVINCES_AND_TERRITORIES),
        help="passed to scrape_local.py",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)  # --output-dir is inherited by each step via the environment

    local_argv = []
    if args.filter:
        local_argv += ["--filter", args.filter]
    if args.province:
        local_argv += ["--province", args.province]
    steps = [
        ("federal", scrape_federal.main, ["--no-enrich"] if args.no_enrich else []),
        ("provincial/territorial", scrape_provincial_territorial.main, []),
        ("local", scrape_local.main, local_argv),
    ]

    problems = []
    for label, step_main, step_argv in steps:
        try:
            exit_code = step_main(step_argv)
        except Exception:
            traceback.print_exc()
            exit_code = 1
        if exit_code:
            problems.append(label)
        print()

    print(f"All done. Full history + changelog: {get_master_path()}")
    if problems:
        print(f"These steps reported problems (see above): {', '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
