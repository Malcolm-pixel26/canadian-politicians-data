#!/usr/bin/env python3
"""
Tracks federal political financing (campaign contributions received) for
Canadian politicians, using Elections Canada's public Open Data:
https://www.elections.ca/content.aspx?section=fin&dir=oda&document=index&lang=e

This is one of three budget scripts -- the other two are
scrape_budget_provincial_territorial.py and scrape_budget_local.py.
Each one owns exactly one tab of the shared workbook:

    ~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx
        Federal                  <- written by this script
        Provincial_Territorial   <- written by scrape_budget_provincial_territorial.py
        Local                    <- written by scrape_budget_local.py
        Notes                    <- one row per tab, upserted by whichever script last ran

Run any of the three, in any order, as often as you like -- each only
touches its own tab and its own row in Notes, so they never clobber
each other. Run scrape_all_budgets.py to update all three in one go.

Scope note (important): Elections Canada's political financing regime,
and this dataset, covers FEDERAL political entities only -- candidates,
registered parties, electoral district associations (EDAs), and
nomination/leadership contestants. That's why this script only writes
the Federal tab; provincial/local funding data lives in the other two
scripts' tabs (see their scope notes for why those are largely
"not covered" for now).

Source data used here: "Contribution to all political entities, as
filed" -- a weekly-updated CSV of every disclosed contribution since
Jan 1, 2004 (large file, ~150+ MB compressed). It's downloaded once and
cached locally; re-run with --refresh to force a fresh download.

Usage:
    python3 scrape_budget_federal.py
    python3 scrape_budget_federal.py --refresh          # force re-download of the source file
    python3 scrape_budget_federal.py --since 2015        # only count contributions from 2015 onward
"""
import csv
import io
import os
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timezone

from represent_common import (
    get_output_dir,
    get_master_path,
    read_master_tab,
    normalize_name,
    write_budget_tab,
)

CONTRIBUTIONS_ZIP_URL = "https://www.elections.ca/fin/oda/od_cntrbtn_de_e.zip"
USER_AGENT = "ca-politician-scraper/1.0 (personal research script)"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # the source file is refreshed weekly upstream

OUTPUT_FILENAME = "MASTER_CANADA_POLITICIAN_BUDGET.xlsx"
SHEET_NAME = "Federal"

# Shared column layout across all three budget tabs (Federal,
# Provincial_Territorial, Local) so the tabs are easy to compare.
FIELDS = [
    "name", "category", "party_name", "representative_set",
    "recipient_type", "total_contributions", "contribution_count",
    "avg_contribution", "match_status",
]

# Column-name candidates for the fields we need, checked case-insensitively
# against whatever header Elections Canada ships. This is deliberately
# defensive -- Elections Canada doesn't publish a formal machine-readable
# schema doc for this file, and column names/casing have shifted between
# past versions of this dataset. If none of the candidates for a field
# match, that field is just left blank rather than the whole run failing;
# `inspect_columns()` below will print the actual header so you can add
# the real column name to the candidate list if needed.
FIELD_CANDIDATES = {
    "recipient_name": ["recipient_name", "political_entity_name", "recipient", "entity_name"],
    "recipient_type": ["recipient_type", "political_entity_type", "entity_type"],
    "amount": ["monetary_amount", "contribution_amount", "amount"],
    "date": ["date_received", "contribution_date", "date"],
    "contributor_name": ["contributor_name", "name_contributor", "contributor"],
}


def _cache_path():
    cache_dir = get_output_dir("_cache")
    return os.path.join(cache_dir, "od_cntrbtn_de_e.zip")


def _is_valid_zip(path):
    """True if `path` exists and is a readable, non-corrupt zip file."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            # testzip() scans every member's CRC and returns the name of
            # the first bad one (or None if all are fine) -- catches a
            # truncated/corrupt download that a plain open() would miss.
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def download_contributions_zip(force=False):
    path = _cache_path()
    if not force and os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < CACHE_MAX_AGE_SECONDS:
            if _is_valid_zip(path):
                print(f"Using cached contributions file ({age/3600:.1f}h old): {path}")
                return path
            print("Cached contributions file exists but is corrupt/truncated -- re-downloading...")
        else:
            print("Cached contributions file is stale, re-downloading...")

    print(f"Downloading {CONTRIBUTIONS_ZIP_URL} (this is a large file, may take a while)...")
    # Download to a temp file first and only replace the real cache file
    # once we've verified it's a complete, valid zip. This is what
    # actually prevents a dropped connection / interrupted download from
    # leaving behind a corrupt cache file that then fails on every
    # subsequent run (with --refresh included, if the retry download also
    # got interrupted) until someone manually deletes it.
    tmp_path = path + ".part"
    try:
        req = urllib.request.Request(CONTRIBUTIONS_ZIP_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp_path, "wb") as out:
            chunk = resp.read(1024 * 1024)
            total = 0
            while chunk:
                out.write(chunk)
                total += len(chunk)
                print(f"\r  ...{total / (1024*1024):.0f} MB downloaded", end="", flush=True)
                chunk = resp.read(1024 * 1024)
        print()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(
            f"Could not download {CONTRIBUTIONS_ZIP_URL}: {e}. "
            f"If this environment has no internet access, this script can't run here -- "
            f"try it from your own machine instead."
        )

    if not _is_valid_zip(tmp_path):
        os.remove(tmp_path)
        raise RuntimeError(
            f"Download from {CONTRIBUTIONS_ZIP_URL} finished but the file isn't a "
            f"valid zip (likely an interrupted/dropped connection). Nothing was "
            f"cached -- try running this again."
        )

    # Atomic on both POSIX and Windows: replaces any existing (e.g. stale
    # or corrupt) cache file in one step, no window where a reader could
    # see a half-written file.
    os.replace(tmp_path, path)
    return path


def _match_field(header, candidates):
    lower_header = {h.lower().strip(): h for h in header}
    for candidate in candidates:
        if candidate in lower_header:
            return lower_header[candidate]
    # loose fallback: substring match
    for candidate in candidates:
        for h_lower, h_orig in lower_header.items():
            if candidate in h_lower:
                return h_orig
    return None


def inspect_columns(zip_path):
    """Print the actual header row of the contributions CSV -- handy for
    fixing FIELD_CANDIDATES if Elections Canada changes their schema."""
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(csv_name) as f:
            header_line = io.TextIOWrapper(f, encoding="utf-8-sig").readline()
    print("Actual CSV header found:")
    print(header_line.strip())


def parse_amount(value):
    if not value:
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def aggregate_contributions(csv_lines, since_year=None):
    """
    Given an iterable of CSV text lines (header first), aggregate total
    contributions and contribution counts per recipient (normalized
    name), plus raw recipient name/type for reference. Returns a dict
    keyed by normalized recipient name.

    Split out from the zip-reading code so it's unit-testable with a
    small synthetic CSV, without needing the real ~150MB download.
    """
    reader = csv.reader(csv_lines)
    header = next(reader)

    recipient_col = _match_field(header, FIELD_CANDIDATES["recipient_name"])
    type_col = _match_field(header, FIELD_CANDIDATES["recipient_type"])
    amount_col = _match_field(header, FIELD_CANDIDATES["amount"])
    date_col = _match_field(header, FIELD_CANDIDATES["date"])

    if not recipient_col or not amount_col:
        raise RuntimeError(
            "Could not find recipient/amount columns in the contributions CSV. "
            "The header was: " + ", ".join(header) + ". "
            "Update FIELD_CANDIDATES in scrape_funding.py with the real column names."
        )

    idx = {name: header.index(col) for name, col in {
        "recipient": recipient_col, "type": type_col, "amount": amount_col, "date": date_col,
    }.items() if col is not None}

    totals = {}
    rows_seen = 0
    rows_used = 0
    for row in reader:
        rows_seen += 1
        if len(row) <= idx["recipient"] or len(row) <= idx["amount"]:
            continue

        if since_year and "date" in idx and len(row) > idx["date"]:
            date_val = row[idx["date"]]
            year = None
            for part in date_val.replace("/", "-").split("-"):
                if len(part) == 4 and part.isdigit():
                    year = int(part)
                    break
            if year and year < since_year:
                continue

        recipient_raw = row[idx["recipient"]].strip()
        if not recipient_raw:
            continue
        key = normalize_name(recipient_raw)

        entry = totals.setdefault(key, {
            "raw_recipient_name": recipient_raw,
            "recipient_type": row[idx["type"]].strip() if "type" in idx and len(row) > idx["type"] else "",
            "total_contributions": 0.0,
            "contribution_count": 0,
        })
        entry["total_contributions"] += parse_amount(row[idx["amount"]])
        entry["contribution_count"] += 1
        rows_used += 1

    print(f"Processed {rows_seen} contribution row(s), used {rows_used} (matched fields / date filter).")
    return totals


def read_contributions_from_zip(zip_path, since_year=None):
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(csv_name) as f:
            text_stream = io.TextIOWrapper(f, encoding="utf-8-sig")
            return aggregate_contributions(text_stream, since_year=since_year)


NOTES_TEXT = (
    "Federal campaign contributions only, from Elections Canada's public "
    "political financing Open Data (updated weekly). Covers candidates, "
    "parties, and electoral district associations. Amounts are "
    "contributions RECEIVED (fundraising), not government/office budgets. "
    "Provincial/territorial and local funding live on their own tabs -- "
    "see their Notes rows for coverage."
)


def build_budget_rows(contribution_totals):
    """
    Merge aggregated contribution totals with the federal politicians in
    the existing master.xlsx, matched by normalized name. Every federal
    politician in master.xlsx appears in the output, even with no
    funding match (e.g. a name that didn't match any recipient name in
    the contributions data).
    """
    master_path = get_master_path()
    master_rows = read_master_tab(master_path, "Federal")

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
                "recipient_type": contrib["recipient_type"],
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
                "match_status": "no funding data found",
            })
        budget_rows.append(entry)

    # Recipients in the contributions data that never matched a federal
    # politician in master.xlsx (e.g. you haven't scraped federal yet, or
    # a party/EDA entry rather than an individual candidate) -- keep them
    # too, so no funding data silently disappears.
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
            "recipient_type": contrib["recipient_type"],
            "total_contributions": round(total, 2),
            "contribution_count": count,
            "avg_contribution": round(total / count, 2) if count else 0,
            "match_status": "unmatched recipient (not in master.xlsx)",
        })

    return budget_rows


def parse_args(argv):
    force_refresh = "--refresh" in argv
    since_year = None
    if "--since" in argv:
        idx = argv.index("--since")
        if idx + 1 < len(argv):
            since_year = int(argv[idx + 1])
    return force_refresh, since_year


def main():
    force_refresh, since_year = parse_args(sys.argv[1:])

    print("=== Federal political financing tracker ===")
    zip_path = download_contributions_zip(force=force_refresh)
    try:
        inspect_columns(zip_path)
        contribution_totals = read_contributions_from_zip(zip_path, since_year=since_year)
    except zipfile.BadZipFile:
        # Cache passed validation at download time but is unreadable now
        # (e.g. disk issue, something else touched the file) -- recover
        # once by forcing a fresh download rather than just failing.
        print("Cached file is unreadable after all -- forcing a fresh download...")
        zip_path = download_contributions_zip(force=True)
        inspect_columns(zip_path)
        contribution_totals = read_contributions_from_zip(zip_path, since_year=since_year)

    budget_rows = build_budget_rows(contribution_totals)

    out_path = os.path.join(get_output_dir(), OUTPUT_FILENAME)
    write_budget_tab(out_path, SHEET_NAME, FIELDS, budget_rows, "Federal", NOTES_TEXT)
    print(f"Done. {len(budget_rows)} row(s) saved to {out_path}")


if __name__ == "__main__":
    main()


# --- Provincial/territorial and local funding -------------------------------
# This script only ever writes the Federal tab. Provincial/territorial
# funding lives in scrape_budget_provincial_territorial.py (writes the
# Provincial_Territorial tab) and local funding in scrape_budget_local.py
# (writes the Local tab) -- see those files, including their
# PROVINCE_FETCHERS / MUNICIPAL_FETCHERS extension points, for adding a
# real data source for a specific province or municipality.
