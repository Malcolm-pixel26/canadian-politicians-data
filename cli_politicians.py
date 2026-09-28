#!/usr/bin/env python3
"""
Single entry point that bundles scrape_federal.py,
scrape_provincial_territorial.py, scrape_local.py, scrape_all.py,
lookup_politician.py, scrape_votes.py, changes_digest.py and
make_maps.py into one program -- meant to be compiled into one Windows
.exe with PyInstaller (see build_exes.bat).

Whichever underlying script you want to run is picked with a
subcommand as the first argument; everything after that is passed
through unchanged to that script's own argument parsing, so all the
flags documented in README.md still work exactly the same.

Usage (once compiled: politician_scrapers.exe <subcommand> ...):
    politician_scrapers federal
    politician_scrapers federal --no-enrich
    politician_scrapers provincial
    politician_scrapers local
    politician_scrapers local --filter Toronto
    politician_scrapers all
    politician_scrapers lookup "Jane Doe"
    politician_scrapers lookup "Jane Doe" --live --category federal
    politician_scrapers lookup "Jane Doe" --live --category local --place Toronto
    politician_scrapers lookup --postal-code "K1A 0A6"
    politician_scrapers votes
    politician_scrapers votes --bill C-30 --activity
    politician_scrapers digest --days 7
    politician_scrapers maps
    politician_scrapers maps --level local --province ON

Add --help after any subcommand to see its options, e.g.
    politician_scrapers local --help

Run with no arguments (or an unrecognized one) to see this list again.
"""
import sys
import traceback

COMMANDS = ("federal", "provincial", "provincial_territorial", "local", "all", "lookup", "votes", "digest", "maps")


def _run(command):
    """Run one subcommand; returns its exit code."""
    if command == "federal":
        import scrape_federal
        return scrape_federal.main()
    elif command in ("provincial", "provincial_territorial"):
        import scrape_provincial_territorial
        return scrape_provincial_territorial.main()
    elif command == "local":
        import scrape_local
        return scrape_local.main()
    elif command == "all":
        import scrape_all
        return scrape_all.main()
    elif command == "lookup":
        import lookup_politician
        return lookup_politician.main()
    elif command == "votes":
        import scrape_votes
        return scrape_votes.main()
    elif command == "digest":
        import changes_digest
        return changes_digest.main()
    elif command == "maps":
        import make_maps
        return make_maps.main()


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

    # When this is a double-clicked, frozen .exe (not run from an existing
    # terminal), Windows closes the console the instant the process exits --
    # success or failure. Without this try/except + pause, a mid-run error
    # (e.g. a network hiccup partway through the 10-20 minute local scrape)
    # flashes its traceback and disappears before it can be read, and it
    # just looks like the run silently stopped early / produced no data.
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
