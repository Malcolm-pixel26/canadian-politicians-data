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

Source data: "Contributions to all political entities from January 2004
to present", a weekly-updated CSV of every disclosed contribution, in
two versions:
    - as submitted (default, ~158 MB): as filed by each political
      entity -- the most complete and current;
    - as reviewed (--reviewed, ~80 MB): includes agents' updates and
      Elections Canada's corrections, but can lag behind recent filings.
It's downloaded once and cached locally; re-run with --refresh to force
a fresh download.

Matching contributions to politicians: the file's "Recipient" column is
"Last, First" ("Whelan, Susan"), so it never matched master.xlsx's
"First Last" names; the recipient first/last name columns are used
instead. A person's contributions as a candidate, leadership contestant
and nomination contestant are combined (recipient_type lists which),
and a match whose current riding isn't among the districts they
received contributions in is flagged "riding differs -- check".

Usage:
    python3 scrape_budget_federal.py
    python3 scrape_budget_federal.py --refresh          # force re-download of the source file
    python3 scrape_budget_federal.py --since 2015        # only elections / fiscal periods dated 2015 or later
    python3 scrape_budget_federal.py --reviewed          # use the "as reviewed" dataset
"""
import argparse
import csv
import http.client
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

from budget_common import (
    BUDGET_FIELDS,
    OUTPUT_FILENAME,
    build_budget_rows as _build_budget_rows,
    read_master_politicians,
    write_budget_rows,
)
from represent_common import (
    RETRYABLE_STATUSES,
    USER_AGENT,
    add_common_args,
    apply_common_args,
    get_output_dir,
    normalize_name,
)

CONTRIBUTION_DATASETS = {
    "submitted": "https://www.elections.ca/fin/oda/od_cntrbtn_de_e.zip",
    "reviewed": "https://www.elections.ca/fin/oda/od_cntrbtn_audt_e.zip",
}
CONTRIBUTIONS_ZIP_URL = CONTRIBUTION_DATASETS["submitted"]
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # the source file is refreshed weekly upstream
DOWNLOAD_ATTEMPTS = 3

SHEET_NAME = "Federal"
FIELDS = BUDGET_FIELDS
NOT_FOUND_STATUS = "no funding data found"

# Column-name candidates for the fields we need, compared after
# normalizing both sides (lowercase, every run of non-alphanumerics -> "_"),
# so "Fiscal/Election date", "fiscal_election_date" and "FISCAL ELECTION
# DATE" are all the same column. The first candidates are the names in
# Elections Canada's current header.
#
# There's deliberately no loose "contains" fallback: it used to pick the
# first column containing "date" or "amount", i.e. possibly the wrong one.
# If Elections Canada renames a column, the run stops and prints the real
# header so you can add the new name here.
FIELD_CANDIDATES = {
    "entity_type": ["political_entity", "recipient_type", "political_entity_type", "entity_type"],
    "recipient_name": ["recipient", "recipient_name", "political_entity_name", "entity_name"],
    "recipient_first": ["recipient_first_name"],
    "recipient_last": ["recipient_last_name"],
    "district": ["electoral_district"],
    "date": ["fiscal_election_date", "contribution_received_date", "date_received", "contribution_date"],
    "amount": ["monetary_amount", "contribution_amount", "amount"],
    "non_monetary": ["non_monetary_amount"],
}

# "Political Entity" values that are people rather than parties or
# electoral district associations: Candidates, Leadership contestants,
# Nomination contestants.
PERSON_ENTITY_MARKERS = ("candidate", "contestant")


def _cache_path(dataset="submitted"):
    filename = CONTRIBUTION_DATASETS[dataset].rsplit("/", 1)[-1]
    return os.path.join(get_output_dir("_cache"), filename)


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


def _download_once(url, tmp_path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp_path, "wb") as out:
        total = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
            print(f"\r  ...{total / (1024 * 1024):.0f} MB downloaded", end="", flush=True)
    print()


def download_contributions_zip(force=False, dataset="submitted"):
    url = CONTRIBUTION_DATASETS[dataset]
    path = _cache_path(dataset)
    if not force and os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < CACHE_MAX_AGE_SECONDS:
            if _is_valid_zip(path):
                print(f"Using cached contributions file ({age/3600:.1f}h old): {path}")
                return path
            print("Cached contributions file exists but is corrupt/truncated -- re-downloading...")
        else:
            print("Cached contributions file is stale, re-downloading...")

    print(f"Downloading {url} (this is a large file, may take a while)...")
    # Download to a temp file first and only replace the real cache file
    # once we've verified it's a complete, valid zip, so a dropped
    # connection can never leave behind a corrupt cache file that then
    # fails on every subsequent run. A failed or truncated download is
    # retried a couple of times before giving up.
    tmp_path = path + ".part"
    last_error = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_once(url, tmp_path)
            if _is_valid_zip(tmp_path):
                # Atomic on both POSIX and Windows: replaces any existing
                # (stale or corrupt) cache file in one step.
                os.replace(tmp_path, path)
                return path
            last_error = "the file isn't a valid zip (likely an interrupted/dropped connection)"
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code} {e.reason}"
            if e.code not in RETRYABLE_STATUSES:
                break
        except (OSError, http.client.HTTPException) as e:
            last_error = f"{type(e).__name__}: {e}"
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        if attempt < DOWNLOAD_ATTEMPTS:
            print(f"  ! download failed ({last_error}) -- retrying in {10 * attempt}s...")
            time.sleep(10 * attempt)

    raise RuntimeError(
        f"Could not download {url}: {last_error}. Nothing was cached. "
        f"If this environment has no internet access, this script can't run here -- "
        f"try it from your own machine instead."
    )


def _decoded_lines(binary_file):
    """
    Text lines of a CSV member. Each line is decoded as UTF-8, falling
    back to Windows-1252 (and finally Latin-1, which never fails) for any
    line that isn't -- government CSV exports are often cp1252, and one
    stray byte shouldn't abort a 150 MB run.
    """
    first = True
    for raw in binary_file:
        if first:
            first = False
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                yield raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue


def _csv_members(zf):
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise RuntimeError(f"No CSV file found inside {zf.filename}")
    return names


def inspect_columns(zip_path):
    """Print the actual header row of each contributions CSV -- handy for
    fixing FIELD_CANDIDATES if Elections Canada changes their schema."""
    with zipfile.ZipFile(zip_path) as zf:
        for csv_name in _csv_members(zf):
            with zf.open(csv_name) as f:
                header_line = next(_decoded_lines(f), "")
            print(f"Actual CSV header found ({csv_name}):")
            print(header_line.strip())


def _normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _match_field(header, candidates):
    """Index of the first header column matching one of `candidates`, or None."""
    positions = {}
    for i, name in enumerate(header):
        positions.setdefault(_normalize_header(name), i)
    for candidate in candidates:
        if candidate in positions:
            return positions[candidate]
    return None


def parse_amount(value):
    if not value:
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _year(value):
    for part in (value or "").replace("/", "-").split("-"):
        if len(part) == 4 and part.isdigit():
            return int(part)
    return None


_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def _iso_date(value):
    match = _ISO_DATE_RE.search(value or "")
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""


def aggregate_contributions(csv_lines, since_year=None, totals=None):
    """
    Given an iterable of CSV text lines (header first), total up
    contributions per recipient. Returns a dict keyed by normalized
    recipient name ("susan whelan" -- people are keyed "first last", the
    order master.xlsx uses), shaped as described in budget_common.py.
    Pass `totals` to accumulate several files into the same dict.

    Split out from the zip-reading code so it's unit-testable with a
    small synthetic CSV, without needing the real ~150MB download.
    """
    totals = {} if totals is None else totals
    reader = csv.reader(csv_lines)
    header = next(reader, None)
    if not header:
        return totals

    col = {name: _match_field(header, candidates) for name, candidates in FIELD_CANDIDATES.items()}
    if col["amount"] is None or (col["recipient_name"] is None and col["recipient_last"] is None):
        raise RuntimeError(
            "Could not find recipient/amount columns in the contributions CSV. "
            "The header was: " + ", ".join(header) + ". "
            "Update FIELD_CANDIDATES in scrape_budget_federal.py with the real column names."
        )

    def cell(row, name):
        i = col[name]
        return row[i].strip() if i is not None and i < len(row) else ""

    rows_seen = 0
    rows_used = 0
    for row in reader:
        rows_seen += 1
        date_value = cell(row, "date")
        if since_year:
            year = _year(date_value)
            if year and year < since_year:
                continue

        entity = cell(row, "entity_type")
        first, last = cell(row, "recipient_first"), cell(row, "recipient_last")
        recipient = cell(row, "recipient_name")
        if entity:
            is_person = any(marker in entity.lower() for marker in PERSON_ENTITY_MARKERS)
        else:
            is_person = bool(first or last)
        if is_person and not (first or last) and "," in recipient:
            last, _, first = recipient.partition(",")  # "Whelan, Susan"
            first, last = first.strip(), last.strip()
        display = f"{first} {last}".strip() if is_person and (first or last) else recipient
        key = normalize_name(display)
        if not key:
            continue

        entry = totals.get(key)
        if entry is None:
            entry = totals[key] = {
                "raw_recipient_name": display,
                "first_name": first if is_person else "",
                "last_name": last if is_person else "",
                "recipient_type": set(),
                "total_contributions": 0.0,
                "non_monetary_contributions": 0.0,
                "contribution_count": 0,
                "min_date": "",
                "max_date": "",
                "electoral_districts": set(),
            }
        if entity:
            entry["recipient_type"].add(entity)
        entry["total_contributions"] += parse_amount(cell(row, "amount"))
        entry["non_monetary_contributions"] += parse_amount(cell(row, "non_monetary"))
        entry["contribution_count"] += 1
        iso = _iso_date(date_value)
        if iso:
            if not entry["min_date"] or iso < entry["min_date"]:
                entry["min_date"] = iso
            if iso > entry["max_date"]:
                entry["max_date"] = iso
        district = cell(row, "district")
        if is_person and district:
            entry["electoral_districts"].add(district)
        rows_used += 1

    since_note = f", dated {since_year} or later" if since_year else ""
    print(f"Processed {rows_seen} contribution row(s), used {rows_used}{since_note}.")
    return totals


def read_contributions_from_zip(zip_path, since_year=None):
    """Aggregate every CSV in the zip (in case Elections Canada ever splits it)."""
    totals = {}
    with zipfile.ZipFile(zip_path) as zf:
        for csv_name in _csv_members(zf):
            with zf.open(csv_name) as f:
                aggregate_contributions(_decoded_lines(f), since_year=since_year, totals=totals)
    return totals


def notes_text(dataset="submitted", since_year=None):
    version = "as submitted" if dataset == "submitted" else "as reviewed by Elections Canada"
    since = f" Only elections/fiscal periods dated {since_year} or later are counted." if since_year else ""
    return (
        "Federal campaign contributions only, from Elections Canada's public "
        f"political financing Open Data ({version}; updated weekly). Covers "
        "candidates, parties, electoral district associations and leadership/"
        "nomination contestants; a person's contributions across those roles "
        "are combined (see Recipient Type). Amounts are contributions RECEIVED "
        f"(fundraising), not government/office budgets.{since} "
        "Provincial/territorial and local funding live on their own tabs -- "
        "see their Notes rows for coverage."
    )


NOTES_TEXT = notes_text()


def build_budget_rows(contribution_totals):
    """
    Merge aggregated contribution totals with the federal politicians in
    the existing master.xlsx (matching rules: budget_common.build_budget_rows).
    Every federal politician in master.xlsx appears in the output, even
    with no funding match, followed by every recipient that matched nobody
    (parties, EDAs, past candidates...).
    """
    return _build_budget_rows(read_master_politicians("Federal"), contribution_totals, NOT_FOUND_STATUS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Total federal campaign contributions per politician from Elections Canada open data."
    )
    parser.add_argument("--refresh", action="store_true", help="force a fresh download of the source file")
    parser.add_argument(
        "--since", type=int, metavar="YEAR", help="only count elections / fiscal periods dated YEAR or later"
    )
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="use the 'as reviewed' dataset (Elections Canada's corrections included, ~80 MB, "
             "may lag recent filings) instead of 'as submitted' (~158 MB)",
    )
    add_common_args(parser)
    return parser.parse_args(argv)


def run(args):
    """Everything main() does after argument parsing (scrape_all_budgets.py calls this)."""
    dataset = "reviewed" if args.reviewed else "submitted"
    print("=== Federal political financing tracker ===")
    zip_path = download_contributions_zip(force=args.refresh, dataset=dataset)
    try:
        inspect_columns(zip_path)
        contribution_totals = read_contributions_from_zip(zip_path, since_year=args.since)
    except zipfile.BadZipFile:
        # Cache passed validation at download time but is unreadable now
        # (e.g. disk issue, something else touched the file) -- recover
        # once by forcing a fresh download rather than just failing.
        print("Cached file is unreadable after all -- forcing a fresh download...")
        zip_path = download_contributions_zip(force=True, dataset=dataset)
        inspect_columns(zip_path)
        contribution_totals = read_contributions_from_zip(zip_path, since_year=args.since)

    master_rows = read_master_politicians("Federal")
    if not master_rows:
        print("Note: master.xlsx has no federal politicians yet -- run scrape_federal.py first, "
              "or every recipient will be listed as unmatched.")
    budget_rows = _build_budget_rows(master_rows, contribution_totals, NOT_FOUND_STATUS)
    matched = sum(1 for r in budget_rows[:len(master_rows)] if r["match_status"].startswith("matched"))
    print(f"Matched {matched}/{len(master_rows)} federal politician(s) to Elections Canada recipients.")

    out_path = write_budget_rows(SHEET_NAME, budget_rows, notes_text(dataset, args.since))
    print(f"Done. {len(budget_rows)} row(s) saved to {out_path}")
    return 0


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())


# --- Provincial/territorial and local funding -------------------------------
# This script only ever writes the Federal tab. Provincial/territorial
# funding lives in scrape_budget_provincial_territorial.py (writes the
# Provincial_Territorial tab) and local funding in scrape_budget_local.py
# (writes the Local tab) -- see those files, including their
# PROVINCE_FETCHERS / MUNICIPAL_FETCHERS extension points, for adding a
# real data source for a specific province or municipality.
