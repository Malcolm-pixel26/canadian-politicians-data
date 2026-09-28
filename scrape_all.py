#!/usr/bin/env python3
"""
Runs all three scrapers in sequence: federal, provincial/territorial,
then local. Local is by far the slowest (several hundred councils),
so it runs last.

Usage:
    python3 scrape_all.py
"""
import scrape_federal
import scrape_provincial_territorial
import scrape_local
from represent_common import get_master_path


def main():
    scrape_federal.main()
    print()
    scrape_provincial_territorial.main()
    print()
    scrape_local.main()
    print()
    print(f"All done. Full history + changelog: {get_master_path()}")


if __name__ == "__main__":
    main()
