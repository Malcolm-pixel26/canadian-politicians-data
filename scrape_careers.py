#!/usr/bin/env python3
"""
MP career histories, from each MP's "Roles - Current and Past" page on
the House of Commons website (for example
https://www.ourcommons.ca/Members/en/pierre-poilievre(25524)/roles):

  - every term as an MP, and the riding;
  - their party in each Parliament, so floor-crossings show up;
  - offices such as minister, parliamentary secretary or party leader;
  - every committee, by session, and parliamentary associations;
  - every election they ran in, and the result.

Writes:

    ~/Desktop/output/federal/mp_careers.xlsx
        Summary       one row per MP: first elected, years as an MP, ridings,
                      elections won and lost, party history, offices, committees
        Terms, Parties, Offices, Committees, Associations, Elections
                      the full lists behind the summary, one row per entry
                      (a blank End date means they still hold it)
    ~/Desktop/output/federal/mp_careers_summary.csv

Usage:
    python3 scrape_careers.py                   # every current MP: one page each, about three minutes
    python3 scrape_careers.py --mp Poilievre    # only MPs whose name matches (repeat for several)

A run limited with --mp adds the names to the file names, so it never
overwrites the whole-House files. lookup_politician.py shows the same
summary for a single MP.

This reads the same page you'd see in a browser, finding each table by
its column headings. A page without the Member of Parliament table every
MP has isn't the page it expects, so that MP is reported as failed
rather than half-read.
"""
import argparse
import os
import re
import sys
from datetime import date

from lookup_politician import name_matches
from openparliament_enrich import house_of_commons_id
from represent_common import (
    MAX_CONSECUTIVE_FAILURES,
    FetchError,
    add_common_args,
    apply_common_args,
    explain_fetch_error,
    fetch_category,
    find_table,
    get_output_dir,
    html_tables,
    http_get,
    normalize_name,
    write_csv,
    write_workbook,
)

# Each table on the roles page: {column heading (column_key form): field}.
# The headings identify the table, wherever it is on the page.
SECTIONS = {
    "terms": {"constituency": "constituency", "provinceterritory": "province",
              "startdate": "start", "enddate": "end"},
    "parties": {"parliament": "parliament", "politicalaffiliation": "party", "startdate": "start", "enddate": "end"},
    "offices": {"parliament": "parliament", "title": "title", "role": "role", "startdate": "start", "enddate": "end"},
    "committees": {"parliamentsession": "session", "role": "role", "committee": "committee",
                   "startdate": "start", "enddate": "end"},
    "associations": {"parliament": "parliament", "role": "role", "associationorgroup": "association",
                     "startdate": "start", "enddate": "end"},
    "elections": {"date": "date", "electiontype": "election_type", "constituency": "constituency",
                  "provinceterritory": "province", "result": "result"},
}

SUMMARY_COLUMNS = [
    ("name", "Name"),
    ("party", "Party"),
    ("riding", "Riding"),
    ("province_territory", "Province"),
    ("first_elected", "First elected"),
    ("years_as_mp", "Years as MP"),
    ("parliaments", "Parliaments"),
    ("ridings", "Ridings represented"),
    ("elections_won", "Elections won"),
    ("elections_lost", "Elections lost"),
    ("party_history", "Party history"),
    ("party_changes", "Party changes"),
    ("current_offices", "Current offices"),
    ("past_offices", "Past offices"),
    ("minister_posts", "Minister posts"),
    ("parliamentary_secretary_posts", "Parliamentary secretary posts"),
    ("current_committees", "Current committees"),
    ("committees_served", "Committees served on"),
    ("associations", "Associations"),
    ("roles_url", "Source"),
]

# (sheet, section, [(field, heading)]) -- each sheet also starts with the MP's name.
DETAIL_SHEETS = [
    ("Terms", "terms", [("constituency", "Riding"), ("province", "Province"), ("start", "Start"), ("end", "End")]),
    ("Parties", "parties", [("parliament", "Parliament"), ("party", "Party"), ("start", "Start"), ("end", "End")]),
    ("Offices", "offices", [("parliament", "Parliament"), ("title", "Title"), ("role", "Role"),
                            ("start", "Start"), ("end", "End")]),
    ("Committees", "committees", [("session", "Session"), ("committee", "Committee"), ("role", "Role"),
                                  ("start", "Start"), ("end", "End")]),
    ("Associations", "associations", [("parliament", "Parliament"), ("association", "Association or group"),
                                      ("role", "Role"), ("start", "Start"), ("end", "End")]),
    ("Elections", "elections", [("date", "Date"), ("election_type", "Election"), ("constituency", "Riding"),
                                ("province", "Province"), ("result", "Result")]),
]


# --- Reading one MP's roles page -------------------------------------------------

_MONTHS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}
_LONG_DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")


def parse_long_date(text):
    """"Monday, August 18, 2025" -> "2025-08-18". "not applicable" -- the
    page's way of saying it hasn't ended -- is "". Anything else is kept
    as it was, rather than lost."""
    text = " ".join((text or "").split())
    match = _LONG_DATE_RE.search(text)
    month = _MONTHS.get(match.group(1).lower()) if match else None
    if month:
        try:
            return date(int(match.group(3)), month, int(match.group(2))).isoformat()
        except ValueError:
            pass
    return "" if text.lower() in ("", "not applicable") else text


def _parliament_number(text):
    """"45th" -> 45 (a number, so it sorts properly in Excel)."""
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else text


def parse_roles_page(page_text, url=""):
    """
    {section: [entries]} for every table in SECTIONS, from one MP's roles
    page; a section the page doesn't have is an empty list (e.g. no
    offices held). Raises ValueError if there's no Member of Parliament
    terms table, which every MP's page has.
    """
    tables = html_tables(page_text)
    career = {}
    for section, columns in SECTIONS.items():
        entries = []
        found = find_table(tables, tuple(columns))
        if found:
            keys, rows = found
            index = {column: keys.index(column) for column in columns}
            for cells in rows:
                if len(cells) < len(keys):
                    continue  # a caption or "no data" row spanning the table
                entry = {field: cells[index[column]] for column, field in columns.items()}
                for field in ("start", "end", "date"):
                    if field in entry:
                        entry[field] = parse_long_date(entry[field])
                if "parliament" in entry:
                    entry["parliament"] = _parliament_number(entry["parliament"])
                entries.append(entry)
        career[section] = entries
    if not career["terms"]:
        raise ValueError(f"no Member of Parliament table on {url or 'the page'} -- has the page changed?")
    return career


def roles_url(profile_url):
    """An MP's House of Commons profile URL (Represent's `url` for them) ->
    their roles page."""
    return profile_url.rstrip("/") + "/roles"


def fetch_career(profile_url):
    url = roles_url(profile_url)
    return parse_roles_page(http_get(url, accept="text/html").decode("utf-8", errors="replace"), url)


# --- Summarising a career ------------------------------------------------------

def _as_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _in_order(values):
    """Distinct values, first appearance first."""
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _chronological(entries, field="start"):
    return sorted(entries, key=lambda e: e.get(field) or "9999")


def summarize(career, today=None):
    """The Summary sheet's columns for one MP's career (see SUMMARY_COLUMNS)."""
    today = today or date.today()
    terms = _chronological(career["terms"])
    starts = [d for d in (_as_date(t["start"]) for t in terms) if d]
    days = 0
    for term in terms:
        start = _as_date(term["start"])
        end = _as_date(term["end"]) or (today if not term["end"] else None)
        if start and end and end >= start:
            days += (end - start).days + 1

    results = [e["result"].lower() for e in career["elections"]]
    lost = sum("defeat" in r for r in results)
    won = sum("defeat" not in r and ("elect" in r or "acclaim" in r) for r in results)

    parties = []
    for entry in _chronological(career["parties"]):
        if entry["party"] and (not parties or parties[-1] != entry["party"]):
            parties.append(entry["party"])

    offices = _chronological(career["offices"])
    roles = [o["role"].lower() for o in offices]
    current_offices = _in_order(o["title"] for o in offices if not o["end"])
    committees = career["committees"]
    return {
        "first_elected": min(starts).isoformat() if starts else "",
        "years_as_mp": round(days / 365.25, 1),
        "parliaments": len(terms),
        "ridings": "; ".join(_in_order(t["constituency"] for t in terms)),
        "elections_won": won,
        "elections_lost": lost,
        "party_history": " → ".join(parties),
        "party_changes": max(len(parties) - 1, 0),
        "current_offices": "; ".join(current_offices),
        # An office held before and again now (e.g. Leader of the Opposition) is only "current".
        "past_offices": "; ".join(_in_order(
            o["title"] for o in offices if o["end"] and o["title"] not in current_offices)),
        "minister_posts": len(_in_order(o["title"] for o, role in zip(offices, roles) if role.startswith("minister"))),
        "parliamentary_secretary_posts": len(_in_order(
            o["title"] for o, role in zip(offices, roles) if "parliamentary secretary" in role)),
        "current_committees": "; ".join(sorted(_in_order(
            c["committee"] if c["role"] in ("", "Member") else f"{c['committee']} ({c['role']})"
            for c in committees if not c["end"]))),
        "committees_served": len(_in_order(c["committee"] for c in committees)),
        "associations": len(_in_order(a["association"] for a in career["associations"])),
    }


PAST_OFFICES_SHOWN = 8


def career_lines(summary):
    """A few readable lines about one MP's career, for lookup_politician.py."""
    lines = []
    if summary["first_elected"]:
        lines.append(f"MP since {summary['first_elected']} ({summary['years_as_mp']} years over "
                     f"{summary['parliaments']} Parliament(s)): {summary['ridings']}")
    lines.append(f"Elections: {summary['elections_won']} won, {summary['elections_lost']} lost")
    if summary["party_history"]:
        lines.append(f"Party: {summary['party_history']}")
    if summary["current_offices"]:
        lines.append(f"Now: {summary['current_offices']}")
    past = [title for title in summary["past_offices"].split("; ") if title]
    if past:
        label = "Past offices: " if len(past) <= PAST_OFFICES_SHOWN else \
            f"Past offices ({PAST_OFFICES_SHOWN} most recent of {len(past)}): "
        shown = past[-PAST_OFFICES_SHOWN:]
        lines.append(label + shown[0])
        lines.extend(" " * len(label) + title for title in shown[1:])
    lines.append(f"Committees: {summary['committees_served']} over their career"
                 + (f", now {summary['current_committees']}" if summary["current_committees"] else ""))
    return lines


# --- Command line ---------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Every current MP's career from the House of Commons: terms, parties, offices, "
                    "committees and elections."
    )
    parser.add_argument("--mp", metavar="NAME", action="append",
                        help="only MPs whose name matches, e.g. --mp Poilievre (repeat for several)")
    add_common_args(parser)
    return parser.parse_args(argv)


def _file_suffix(queries):
    slug = "_".join(re.sub(r"[^a-z0-9]+", "-", normalize_name(q)).strip("-") for q in queries or ())
    return f"_{slug[:60].strip('-_')}" if slug.strip("-_") else ""


def write_outputs(careers, suffix=""):
    """careers: [(Represent row, career, summary)]."""
    out_dir = get_output_dir("federal")
    summary_rows = []
    sheets = [{"title": "Summary", "fields": [c[0] for c in SUMMARY_COLUMNS],
               "headers": [c[1] for c in SUMMARY_COLUMNS], "rows": summary_rows, "freeze": "B2"}]
    for row, career, summary in careers:
        summary_rows.append(dict(summary, name=row["name"], party=row["party_name"], riding=row["district_name"],
                                 province_territory=row["province_territory"], roles_url=roles_url(row["url"])))
    for title, section, columns in DETAIL_SHEETS:
        rows = [dict(entry, name=row["name"]) for row, career, _ in careers for entry in career[section]]
        sheets.append({"title": title, "fields": ["name"] + [c[0] for c in columns],
                       "headers": ["Name"] + [c[1] for c in columns], "rows": rows})
    write_workbook(os.path.join(out_dir, f"mp_careers{suffix}.xlsx"), sheets)
    write_csv(os.path.join(out_dir, f"mp_careers{suffix}_summary.csv"), summary_rows, [c[0] for c in SUMMARY_COLUMNS])
    return out_dir


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== MP career histories (House of Commons) ===")
    try:
        mps = [r for r in fetch_category("federal").rows if house_of_commons_id(r.get("url"))]
    except FetchError as e:
        explain_fetch_error(e)
        return 1
    if args.mp:
        mps = [r for r in mps if any(name_matches(query, r["name"]) for query in args.mp)]
        if not mps:
            print(f"No current MP's name matches {', '.join(args.mp)}.")
            return 1
    mps.sort(key=lambda r: normalize_name(r["name"]))
    print(f"Reading the roles page of {len(mps)} MP(s) on ourcommons.ca...")

    careers, failed = [], []
    consecutive_failures = 0
    for n, row in enumerate(mps, start=1):
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"  ! ourcommons.ca seems to be unreachable -- skipping the remaining {len(mps) - n + 1} MP(s).")
            failed.extend(r["name"] for r in mps[n - 1:])
            break
        try:
            career = fetch_career(row["url"])
        except (FetchError, ValueError) as e:
            print(f"  ! couldn't read {row['name']}'s roles page: {e}")
            failed.append(row["name"])
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        careers.append((row, career, summarize(career)))
        if n % 25 == 0 and n < len(mps):
            print(f"  ... {n}/{len(mps)}")

    if not careers:
        print("No career history could be read, so nothing was written.")
        return 1
    if len(careers) <= 5:
        for row, _, summary in careers:
            print(f"\n{row['name']} ({row['district_name']})")
            for line in career_lines(summary):
                print(f"  {line}")
        print()
    out_dir = write_outputs(careers, _file_suffix(args.mp))
    print(f"Done. {len(careers)} MP career(s) -> {out_dir}")
    if failed:
        print(f"Couldn't read {len(failed)} MP(s): {', '.join(failed[:10])}{', ...' if len(failed) > 10 else ''}"
              f" -- re-run later to fill them in.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
