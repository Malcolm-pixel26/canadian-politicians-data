#!/usr/bin/env python3
"""
MP office spending: what each Member of Parliament's office spent, from
the House of Commons' quarterly "Members - Summary of Expenditures"
(proactive disclosure): https://www.ourcommons.ca/proactivedisclosure/en/members

It writes one tab of the shared budget workbook, next to the Federal
(campaign contributions) tab:

    ~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx
        Federal_Office_Spending  <- written by this script: one row per MP, biggest spenders first
        Notes                    <- its row says exactly which quarters are included

and every row of every quarter, exactly as published, to:

    ~/Desktop/output/federal/mp_office_spending_by_quarter.csv

The four categories, as the House of Commons defines them:
    Salaries     salaries of the MP's employees (not the MP's own pay)
    Travel       travel charged to the Member's Office Budget and the Travel Points System
    Hospitality  events held in carrying out the MP's parliamentary functions
    Contracts    everything else: service contracts, advertising, gifts,
                 constituency office leases, office operations and training

The reports count expenses when the House processes them, so a quarter
can include a former MP's last bills, and corrections appear as negative
amounts. A vacant seat's office costs are listed as "Vacant".

By default the four most recent quarters are added up (the latest full
year published). Or pick a fiscal year (April to March) or another
number of quarters:

Usage:
    python3 scrape_budget_office_spending.py
    python3 scrape_budget_office_spending.py --fiscal-year 2024-2025
    python3 scrape_budget_office_spending.py --quarters 1       # just the latest quarter
    python3 scrape_budget_office_spending.py --quarters 8       # the last two years

MPs are matched to master.xlsx's Federal tab the same way campaign
contributions are (so run scrape_federal.py first): by name, flagging a
match whose riding isn't one the spending was reported under.
"""
import argparse
import os
import re
import sys
from datetime import date

from budget_common import budget_workbook_path, match_to_master, read_master_politicians
from represent_common import (
    FetchError,
    add_common_args,
    apply_common_args,
    explain_fetch_error,
    find_table,
    get_output_dir,
    html_tables,
    http_get,
    normalize_name,
    write_budget_tab,
    write_csv,
)

REPORTS_URL = "https://www.ourcommons.ca/proactivedisclosure/en/members"
SHEET_NAME = "Federal_Office_Spending"
CATEGORIES = ("salaries", "travel", "hospitality", "contracts")
REPORT_COLUMNS = ("name", "constituency", "caucus") + CATEGORIES

FIELDS = [
    "name",
    "constituency",
    "caucus",
    "province_territory",
    "salaries",
    "travel",
    "hospitality",
    "contracts",
    "total",
    "quarters",  # how many of the selected quarters list this person
    "match_status",
]
QUARTER_CSV_FIELDS = ["fiscal_year", "quarter", "period_start", "period_end"] + list(REPORT_COLUMNS) + ["total"]

NO_SPENDING_STATUS = "no spending reported in these quarters"
NOT_IN_MASTER_STATUS = "not in master.xlsx (e.g. a former MP)"
VACANT_STATUS = "vacant seat"
DEFAULT_QUARTERS = 4


# --- Quarters -------------------------------------------------------------------
#
# The House's fiscal year runs April 1 to March 31, and its report URLs are
# named after the year a fiscal year ENDS: .../members/2026/4 is 2025-2026's
# fourth quarter (January to March 2026). Quarters are (fiscal year end, n).

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def fiscal_year_label(fiscal_year_end):
    return f"{fiscal_year_end - 1}-{fiscal_year_end}"


def quarter_dates(fiscal_year_end, quarter):
    """First and last day of a fiscal quarter."""
    year = fiscal_year_end if quarter == 4 else fiscal_year_end - 1
    first_month, (last_month, last_day) = {
        1: (4, (6, 30)), 2: (7, (9, 30)), 3: (10, (12, 31)), 4: (1, (3, 31)),
    }[quarter]
    return date(year, first_month, 1), date(year, last_month, last_day)


def quarter_url(fiscal_year_end, quarter):
    return f"{REPORTS_URL}/{fiscal_year_end}/{quarter}"


def _long_date(day):
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def describe_quarters(quarters):
    """"July 1, 2025 to June 30, 2026 (2025-2026 Q2, Q3, Q4; 2026-2027 Q1)"."""
    ordered = sorted(quarters)
    by_year = {}
    for fiscal_year_end, quarter in ordered:
        by_year.setdefault(fiscal_year_end, []).append(f"Q{quarter}")
    labels = "; ".join(f"{fiscal_year_label(year)} {', '.join(qs)}" for year, qs in by_year.items())
    start, end = quarter_dates(*ordered[0])[0], quarter_dates(*ordered[-1])[1]
    return f"{_long_date(start)} to {_long_date(end)} ({labels})"


# --- Reading the House of Commons pages --------------------------------------------

# A quarter's own page -- not the per-MP report pages beneath it
# (.../2026/4/<id>), nor the old-format archive-members pages -- wherever
# the page mentions one: a link, a menu option's value, a data attribute.
_QUARTER_LINK_RE = re.compile(r"(?:^|[/\"'])members/(\d{4})/([1-4])/?(?=[\"'?#<\s]|$)", re.IGNORECASE)
_QUARTER_TEXT_RE = re.compile(r"(\d{4})\s*-\s*(\d{4})\s+(First|Second|Third|Fourth)\s+Quarter\s+Report", re.IGNORECASE)
_QUARTER_NUMBERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def available_quarters(page_text):
    """Every quarter the reports page links to, newest first."""
    found = {(int(m.group(1)), int(m.group(2))) for m in _QUARTER_LINK_RE.finditer(page_text)}
    if not found:  # the links' format changed? fall back to the labels, "2025-2026 Fourth Quarter Report"
        for match in _QUARTER_TEXT_RE.finditer(" ".join(re.sub(r"<[^>]*>", " ", page_text).split())):
            if int(match.group(2)) == int(match.group(1)) + 1:
                found.add((int(match.group(2)), _QUARTER_NUMBERS[match.group(3).lower()]))
    return sorted(found, reverse=True)


def parse_money(text):
    """"$102,030.93" -> 102030.93, "($200.65)" -> -200.65; blank or a dash
    -> 0.0. Footnote asterisks are ignored; anything else raises ValueError."""
    text = (text or "").replace("*", "").strip()
    if text in ("", "-", "–", "—"):
        return 0.0
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    digits = re.sub(r"[\s$(),\-]", "", text)
    if not re.fullmatch(r"\d+(?:\.\d+)?", digits):
        raise ValueError(f"unexpected amount {text!r}")
    return -float(digits) if negative else float(digits)


def parse_summary(page_text, url=""):
    """
    The rows of one quarter's Summary of Expenditures table, as dicts with
    name ("Last, First", exactly as published), constituency, caucus and
    the four amounts. Raises ValueError if the table isn't there or an
    amount can't be read -- better to stop than to add up the wrong thing.
    """
    found = find_table(html_tables(page_text), REPORT_COLUMNS)
    if not found:
        raise ValueError(f"no expenditures table (Name, Constituency, Caucus, Salaries, ...) on {url or 'the page'}")
    keys, table_rows = found
    index = {column: keys.index(column) for column in REPORT_COLUMNS}
    rows = []
    for cells in table_rows:
        if len(cells) < len(keys) or not cells[index["name"]] or normalize_name(cells[index["name"]]) == "total":
            continue
        row = {column: cells[index[column]] for column in ("name", "constituency", "caucus")}
        try:
            row.update({column: parse_money(cells[index[column]]) for column in CATEGORIES})
        except ValueError as e:
            raise ValueError(f"{e} for {row['name']} on {url or 'the page'}") from None
        rows.append(row)
    return rows


_HONORIFIC_RE = re.compile(r"^(?:the\s+)?(?:right\s+|rt\.?\s+)?hon(?:\.|ourable\b)\s*", re.IGNORECASE)


def split_report_name(value):
    """"Sgro, Hon. Judy A." -> ("Judy A.", "Sgro"); titles like Hon. and
    Right Hon. are dropped. A name without a comma ("Vacant") comes back as
    ("", "Vacant")."""
    last, comma, first = (value or "").partition(",")
    if not comma:
        return "", last.strip()
    return _HONORIFIC_RE.sub("", first.strip()).strip(), last.strip()


def fetch_page(url):
    return http_get(url, accept="text/html").decode("utf-8", errors="replace")


def fetch_quarters(quarters):
    """[(quarter, row)] for every row of every quarter's report, oldest
    quarter first."""
    rows = []
    seen = {}
    for quarter in sorted(quarters):
        url = quarter_url(*quarter)
        print(f"Fetching {fiscal_year_label(quarter[0])} Q{quarter[1]}: {url}")
        quarter_rows = parse_summary(fetch_page(url), url)
        print(f"  -> {len(quarter_rows)} row(s)")
        fingerprint = tuple(tuple(sorted(r.items())) for r in quarter_rows)
        if fingerprint in seen and quarter_rows:
            raise ValueError(f"the House of Commons returned the same report for {seen[fingerprint]} and "
                             f"{fiscal_year_label(quarter[0])} Q{quarter[1]} -- stopping rather than counting it twice")
        seen[fingerprint] = f"{fiscal_year_label(quarter[0])} Q{quarter[1]}"
        rows.extend((quarter, row) for row in quarter_rows)
    return rows


# --- Totals per MP ------------------------------------------------------------------

def _add_once(values, value):
    if value and value not in values:
        values.append(value)


def spending_by_person(quarter_rows):
    """
    Add up each person's spending across the quarters. Keyed like
    budget_common expects (normalize_name("First Last")), so it can be
    matched to master.xlsx; someone who changed ridings (e.g. after a
    by-election) is one entry listing both. Vacant seats are kept apart,
    one entry per constituency.
    """
    entries = {}
    for quarter, row in sorted(quarter_rows, key=lambda item: item[0]):
        first, last = split_report_name(row["name"])
        vacant = not first and normalize_name(last) == "vacant"
        display = last if vacant else f"{first} {last}".strip()
        key = f"vacant|{normalize_name(row['constituency'])}" if vacant else normalize_name(display)
        if not key:
            continue
        entry = entries.setdefault(key, {
            "raw_recipient_name": display, "first_name": first, "last_name": last, "vacant": vacant,
            "electoral_districts": [], "caucus": [], "quarters": set(),
            **{category: 0.0 for category in CATEGORIES},
        })
        for category in CATEGORIES:
            entry[category] += row[category]
        entry["quarters"].add(quarter)
        _add_once(entry["electoral_districts"], row["constituency"])
        _add_once(entry["caucus"], row["caucus"])
    return entries


def _spending_columns(entry):
    amounts = {category: round(entry[category], 2) for category in CATEGORIES}
    return {
        "constituency": "; ".join(entry["electoral_districts"]),
        "caucus": "; ".join(entry["caucus"]),
        **amounts,
        "total": round(sum(amounts.values()), 2),
        "quarters": len(entry["quarters"]),
    }


def build_spending_rows(master_rows, entries):
    """One row per federal politician in master.xlsx (with their spending,
    or NO_SPENDING_STATUS), then everyone in the reports who matched
    nobody -- former MPs, vacant seats -- so no spending is left out."""
    pairs, unmatched = match_to_master(master_rows, entries)
    rows = []
    for master, key, status in pairs:
        if key:
            row = _spending_columns(entries[key])
            row.update(name=master.get("name", ""), province_territory=master.get("province_territory", ""),
                       match_status=status)
        else:
            row = {"name": master.get("name", ""), "constituency": master.get("district_name", ""),
                   "caucus": master.get("party_name", ""), "province_territory": master.get("province_territory", ""),
                   **{category: "" for category in CATEGORIES}, "total": "", "quarters": 0,
                   "match_status": NO_SPENDING_STATUS}
        rows.append(row)
    for key in unmatched:
        entry = entries[key]
        row = _spending_columns(entry)
        row.update(name=entry["raw_recipient_name"], province_territory="",
                   match_status=VACANT_STATUS if entry["vacant"] else NOT_IN_MASTER_STATUS)
        rows.append(row)
    return rows


def quarter_csv_rows(quarter_rows):
    rows = []
    for (fiscal_year_end, quarter), row in quarter_rows:
        start, end = quarter_dates(fiscal_year_end, quarter)
        rows.append(dict(row, fiscal_year=fiscal_year_label(fiscal_year_end), quarter=quarter,
                         period_start=start.isoformat(), period_end=end.isoformat(),
                         total=round(sum(row[category] for category in CATEGORIES), 2)))
    return rows


def notes_text(quarters):
    return (
        "MP office spending from the House of Commons' quarterly Members' Summary of Expenditures, "
        f"{describe_quarters(quarters)}. Salaries = salaries of the MP's employees (not the MP's own pay); "
        "Travel = travel charged to the Member's Office Budget and Travel Points System; Hospitality = events "
        "held in the MP's parliamentary functions; Contracts = everything else (service contracts, advertising, "
        "gifts, constituency office leases, office operations, training). Expenses are counted when the House "
        "processes them, so former MPs' last bills and corrections (negative amounts) appear, and vacant seats "
        "are listed separately. Quarters = how many of these quarters list the person. This is office spending "
        "paid from House funds, not campaign money -- the Federal tab has contributions."
    )


# --- Command line -------------------------------------------------------------------------

def _fiscal_year_arg(value):
    """"2025-2026" or "2025-26" -> 2026, the year the fiscal year ends."""
    match = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{2}|\d{4})", value.strip())
    if match:
        end = int(match.group(1)) + 1
        if match.group(2) in (str(end), str(end)[-2:]):
            return end
    raise argparse.ArgumentTypeError("use a fiscal year like 2025-2026 (April 2025 to March 2026)")


def _positive_int(value):
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("use a whole number, 1 or more")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Add up each MP's office spending from the House of Commons' quarterly expenditure reports."
    )
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--fiscal-year", type=_fiscal_year_arg, metavar="YYYY-YYYY",
                        help="every published quarter of this fiscal year (April to March), e.g. 2024-2025")
    period.add_argument("--quarters", type=_positive_int, metavar="N", default=DEFAULT_QUARTERS,
                        help=f"the N most recent quarters (default {DEFAULT_QUARTERS}: the latest full year)")
    add_common_args(parser)
    return parser.parse_args(argv)


def run(args):
    """Everything main() does after argument parsing (scrape_all_budgets.py calls this)."""
    print("=== MP office spending (House of Commons) ===")
    try:
        published = available_quarters(fetch_page(REPORTS_URL))
        if not published:
            print(f"Couldn't find the list of quarterly reports on {REPORTS_URL} -- the House of Commons "
                  f"may have changed the page. Nothing was changed.")
            return 1
        if args.fiscal_year:
            chosen = [q for q in published if q[0] == args.fiscal_year]
            if not chosen:
                years = sorted({fiscal_year_label(q[0]) for q in published})
                print(f"No reports are published for {fiscal_year_label(args.fiscal_year)}. "
                      f"Published fiscal years: {', '.join(years)}.")
                return 1
        else:
            chosen = published[:args.quarters]
            if len(chosen) < args.quarters:
                print(f"Only {len(chosen)} quarter(s) are published -- using all of them.")
        print(f"Quarters: {describe_quarters(chosen)}")
        quarter_rows = fetch_quarters(chosen)
    except FetchError as e:
        explain_fetch_error(e)
        return 1
    except ValueError as e:
        print(f"Couldn't read the House of Commons report: {e}.\nNothing was changed.")
        return 1

    master_rows = read_master_politicians("Federal")
    if not master_rows:
        print("Note: master.xlsx has no federal politicians yet -- run scrape_federal.py first, "
              "or everyone will be listed as not in master.xlsx.")
    entries = spending_by_person(quarter_rows)
    rows = build_spending_rows(master_rows, entries)
    matched = sum(1 for r in rows[:len(master_rows)] if r["match_status"].startswith("matched"))
    print(f"Matched {matched}/{len(master_rows)} federal politician(s) to the spending reports.")

    write_csv(os.path.join(get_output_dir("federal"), "mp_office_spending_by_quarter.csv"),
              quarter_csv_rows(quarter_rows), QUARTER_CSV_FIELDS)
    out_path = write_budget_tab(budget_workbook_path(), SHEET_NAME, FIELDS, rows, SHEET_NAME,
                                notes_text(chosen), sort_field="total")
    print(f"Done. {len(rows)} row(s) saved to {out_path}")
    return 0


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
