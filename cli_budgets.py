#!/usr/bin/env python3
"""
Single entry point that bundles scrape_budget_federal.py,
scrape_budget_office_spending.py, scrape_budget_provincial_territorial.py,
scrape_budget_local.py, and scrape_all_budgets.py into one program --
meant to be compiled into one Windows .exe with PyInstaller (see
build_exes.bat).

Whichever underlying script you want to run is picked with a
subcommand as the first argument; everything after that is passed
through unchanged to that script's own argument parsing, so all the
flags documented in README.md still work exactly the same.

Usage (once compiled: budget_scrapers.exe <subcommand> ...):
    budget_scrapers federal
    budget_scrapers federal --refresh
    budget_scrapers federal --since 2015
    budget_scrapers spending
    budget_scrapers spending --fiscal-year 2024-2025
    budget_scrapers provincial
    budget_scrapers provincial --province ontario
    budget_scrapers local
    budget_scrapers local --municipality toronto
    budget_scrapers all
    budget_scrapers all --refresh --since 2015

Add --help after any subcommand to see its options, e.g.
    budget_scrapers federal --help

Run with no arguments (or an unrecognized one) to see this list again.
"""
import sys
import traceback

COMMANDS = ("federal", "spending", "office_spending", "provincial", "provincial_territorial", "local", "all")


def _run(command):
    """Run one subcommand; returns its exit code."""
    if command == "federal":
        import scrape_budget_federal
        return scrape_budget_federal.main()
    elif command in ("spending", "office_spending"):
        import scrape_budget_office_spending
        return scrape_budget_office_spending.main()
    elif command in ("provincial", "provincial_territorial"):
        import scrape_budget_provincial_territorial
        return scrape_budget_provincial_territorial.main()
    elif command == "local":
        import scrape_budget_local
        return scrape_budget_local.main()
    elif command == "all":
        import scrape_all_budgets
        return scrape_all_budgets.main()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        _pause_if_frozen()
        sys.exit(1)

    command = sys.argv[1]
    # Shift argv left by one so the underlying script's own parse_args()/
    # sys.argv-scanning code sees exactly the arguments meant for it,
    # with no knowledge this is running inside a dispatcher.
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    # See cli_politicians.py for why this try/except + pause matters: a
    # double-clicked frozen .exe closes its console the instant it exits,
    # so a mid-run error would otherwise flash and vanish unread.
    try:
        exit_code = _run(command) or 0
    except Exception:
        print("\n*** The run failed with an error: ***\n")
        traceback.print_exc()
        _pause_if_frozen()
        sys.exit(1)

    _pause_if_frozen()
    sys.exit(exit_code)


def _pause_if_frozen():
    """Keep the console window open if this is a frozen PyInstaller .exe
    launched by double-click, so output/errors stay readable. Skipped
    entirely if stdin isn't an interactive console (e.g. launched from a
    script/scheduler, or with input redirected) -- input() would raise
    EOFError there instead of waiting, which would crash the run right
    at the finish line."""
    if getattr(sys, "frozen", False) and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, OSError):
            pass


if __name__ == "__main__":
    main()
