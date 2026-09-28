#!/usr/bin/env python3
"""
A readable report of what changed among Canada's elected officials --
who left office, who's new, who came back, party switches, seat, role
and committee changes, and updated contact details -- built from the
Changelog sheet of ~/Desktop/output/master.xlsx.

Every scraper run prints a short summary of its own changes and
refreshes that day's report:

    ~/Desktop/output/changes/changes_YYYY-MM-DD.html

Run this script for a report over any period, e.g. weekly from Task
Scheduler or cron:

    python3 changes_digest.py                  # the last 7 days
    python3 changes_digest.py --days 30
    python3 changes_digest.py --since 2026-09-01

Reports can only show what the scrapers saw: a change is recorded the
first time a run notices it, so run the scrapers regularly (see
scrape_all.py) for the reports to stay current.
"""
import argparse
import html
import os
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from represent_common import (
    CATEGORY_TO_MASTER_SHEET,
    add_common_args,
    apply_common_args,
    get_master_path,
    get_output_dir,
    header_for_field,
    read_master_changelog,
    read_master_tab,
    write_text_file,
)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S UTC"

CATEGORY_TITLES = {
    "federal": "Federal (House of Commons and Senate)",
    "provincial_territorial": "Provincial and territorial legislatures",
    "local": "Municipal councils",
}

# How an "updated" field is reported. Anything not listed (photo, source
# link, extra_info, ...) only counts towards "minor updates".
FIELD_GROUPS = {
    "party_name": "party",
    "district_name": "seat",
    "elected_office": "seat",
    "roles": "roles",
    "committees": "roles",
    "name": "names",
    "first_name": "names",
    "last_name": "names",
    "email": "contact",
    "phone": "contact",
    "postal_address": "contact",
    "constituency_phone": "contact",
    "constituency_address": "contact",
    "url": "contact",
    "personal_url": "contact",
}

FIELD_LABELS = {
    "party_name": "Party",
    "district_name": "District",
    "elected_office": "Office",
    "roles": "Roles",
    "committees": "Committees",
    "first_name": "First name",
    "last_name": "Last name",
    "postal_address": "Office address",
    "constituency_phone": "Constituency phone",
    "constituency_address": "Constituency office",
    "url": "Profile page",
    "personal_url": "Personal website",
    "photo_url": "Photo",
    "source_url": "Source link",
    "extra_info": "Other details",
}


def field_label(field):
    return FIELD_LABELS.get(field) or header_for_field(field)


# (key, heading, singular/plural label for the counts line)
PERSON_SECTIONS = [
    ("removed", "Left office", ("left office", "left office")),
    ("added", "New", ("new", "new")),
    ("reactivated", "Returned", ("returned", "returned")),
]
CHANGE_SECTIONS = [
    ("party", "Party changes", ("party change", "party changes")),
    ("seat", "Seat or office changes", ("seat/office change", "seat/office changes")),
    ("roles", "Roles and committees", ("role/committee change", "role/committee changes")),
    ("names", "Name changes", ("name change", "name changes")),
    ("contact", "Contact details", ("contact update", "contact updates")),
]


def parse_timestamp(value):
    """A Changelog timestamp as an aware UTC datetime (None if unreadable)."""
    try:
        return datetime.strptime(str(value), TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _local(moment):
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _local_day_bounds(day):
    """[start, end) of a local calendar day, as aware datetimes."""
    start = datetime.combine(day, time.min).astimezone()
    return start, datetime.combine(day + timedelta(days=1), time.min).astimezone()


# --- Building the digest --------------------------------------------------------

def _people(master_path):
    """Every row of every master tab, keyed by (category, record id)."""
    people = {}
    for category, sheet in CATEGORY_TO_MASTER_SHEET.items():
        for key, row in read_master_tab(master_path, sheet).items():
            people[(category, key)] = row
    return people


def _first_import_times(changelog, people=None):
    """
    When each legislature or council first appears in the Changelog, keyed
    by (category, its name), and each category's first entry, keyed by
    category. The "added" entries of that moment are a first import -- a
    first run, or a whole legislature newly covered, like the Senate --
    which is summarized, not listed as new faces. `people` (see _people())
    says which legislature each entry belongs to; without it, only whole
    categories are recognised.
    """
    people = people or {}
    first = {}
    for entry in changelog:
        stamp = str(entry.get("timestamp") or "")
        if not stamp:
            continue
        category = entry.get("category")
        legislature = (people.get((category, entry.get("key"))) or {}).get("representative_set", "")
        for key in (category, (category, legislature)):
            if key not in first or stamp < first[key]:
                first[key] = stamp
    return first


def _empty_section():
    return {"removed": [], "added": [], "reactivated": [], "party": [], "seat": [], "roles": [],
            "names": [], "contact": [], "minor": Counter(), "first_import": 0}


def build_digest(entries, people=None, first_import_at=None):
    """
    Group Changelog entries into {category: section}, where a section has
    lists of people (removed / added / reactivated), lists of field
    changes (party / seat / roles / names / contact), a Counter of minor
    field updates, and the number of people in a first import.

    `people` ({(category, key): master row}) supplies each person's
    office, district and party -- including for people who left, whose
    rows stay in master.xlsx marked "removed".
    """
    people = people or {}
    first_import_at = first_import_at or {}
    digest = {}
    for entry in sorted(entries, key=lambda e: str(e.get("timestamp") or "")):
        category = entry.get("category") or "other"
        section = digest.setdefault(category, _empty_section())
        row = people.get((category, entry.get("key"))) or {}
        moment = parse_timestamp(entry.get("timestamp"))
        person = {
            "name": entry.get("name") or row.get("name", ""),
            "office": row.get("elected_office", ""),
            "district": row.get("district_name", ""),
            "legislature": row.get("representative_set", ""),
            "party": row.get("party_name", ""),
            "when": _local(moment) if moment else str(entry.get("timestamp") or ""),
        }
        change_type = entry.get("change_type")
        first_import = first_import_at.get((category, person["legislature"])) or first_import_at.get(category)
        if change_type == "added" and entry.get("timestamp") == first_import:
            section["first_import"] += 1
        elif change_type in ("added", "removed", "reactivated"):
            section[change_type].append(person)
        elif change_type == "updated":
            field = entry.get("field", "")
            group = FIELD_GROUPS.get(field)
            if group:
                section[group].append(dict(person, field=field, old=entry.get("old_value", ""),
                                           new=entry.get("new_value", "")))
            else:
                section["minor"][field] += 1
    return digest


def _plural(n, labels):
    return f"{n} {labels[0] if n == 1 else labels[1]}"


def counts_line(section):
    parts = []
    if section["first_import"]:
        parts.append(f"first import of {section['first_import']} people")
    for key, _, labels in PERSON_SECTIONS + CHANGE_SECTIONS:
        if section[key]:
            parts.append(_plural(len(section[key]), labels))
    minor = sum(section["minor"].values())
    if minor:
        parts.append(_plural(minor, ("minor update", "minor updates")))
    return ", ".join(parts) or "no changes"


def _describe_person(p):
    where = " for ".join(v for v in (p["office"], p["district"]) if v)
    extra = ", ".join(v for v in (where, p["party"]) if v)
    return f"{p['name']} ({extra})" if extra else p["name"]


def _describe_change(c):
    label = field_label(c["field"])
    return f"{c['name']}: {label} {c['old'] or '(blank)'} -> {c['new'] or '(blank)'}"


# --- Rendering ------------------------------------------------------------------

def render_text(digest, max_items=5):
    """A short plain-text summary (what the scrapers print after a run)."""
    lines = []
    for category in list(CATEGORY_TITLES) + sorted(set(digest) - set(CATEGORY_TITLES)):
        section = digest.get(category)
        if not section:
            continue
        lines.append(f"{CATEGORY_TITLES.get(category, category)}: {counts_line(section)}")
        for key, heading, _ in [CHANGE_SECTIONS[0]] + PERSON_SECTIONS + CHANGE_SECTIONS[1:3]:
            items = section[key]
            if not items:
                continue
            lines.append(f"  {heading}:")
            describe = _describe_person if key in ("removed", "added", "reactivated") else _describe_change
            for item in items[:max_items]:
                lines.append(f"    - {describe(item)}")
            if len(items) > max_items:
                lines.append(f"    ... and {len(items) - max_items} more (see the full report)")
    return "\n".join(lines)


_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:72rem;padding:0 1rem;color:#1f2933;background:#fff}
h1{margin-bottom:.2rem}h2{margin-top:2.2rem;border-bottom:2px solid #d9e2ec;padding-bottom:.3rem}
.sub,.counts,.minor,footer{color:#52606d}.counts{font-weight:600}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.2rem;font-size:.92rem}
th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #e4e7eb;vertical-align:top}
th{background:#f5f7fa}td.old{color:#8a1c1c}td.new{color:#1a6b3a}
@media print{body{margin:0}h2{break-after:avoid}}
"""


def _table(headings, rows, classes=None):
    classes = classes or {}
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headings)
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="{classes[i]}">{html.escape(str(v))}</td>' if i in classes else f"<td>{html.escape(str(v))}</td>"
            for i, v in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(digest, title, subtitle):
    """A self-contained HTML report (every value is HTML-escaped: the data
    comes from third-party websites)."""
    out = [f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
           f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>",
           f"<h1>{html.escape(title)}</h1><p class=\"sub\">{html.escape(subtitle)}</p>"]
    if not digest:
        out.append("<p>No changes were recorded in this period.</p>")
    for category in list(CATEGORY_TITLES) + sorted(set(digest) - set(CATEGORY_TITLES)):
        section = digest.get(category)
        if not section:
            continue
        out.append(f"<h2>{html.escape(CATEGORY_TITLES.get(category, category))}</h2>")
        out.append(f"<p class=\"counts\">{html.escape(counts_line(section).capitalize())}</p>")
        if section["first_import"]:
            out.append(f"<p>First import: {section['first_import']} people were added to master.xlsx "
                       f"(not listed individually).</p>")
        for key, heading, _ in [CHANGE_SECTIONS[0]] + PERSON_SECTIONS + CHANGE_SECTIONS[1:]:
            items = section[key]
            if not items:
                continue
            items = sorted(items, key=lambda i: (i["legislature"], i["name"], i["when"]))
            out.append(f"<h3>{html.escape(heading)} ({len(items)})</h3>")
            if key in ("removed", "added", "reactivated"):
                out.append(_table(
                    ["Name", "Office", "District", "Legislature / council", "Party", "Recorded"],
                    [[p["name"], p["office"], p["district"], p["legislature"], p["party"], p["when"]] for p in items]))
            else:
                out.append(_table(
                    ["Name", "Legislature / council", "District", "What", "Before", "After", "Recorded"],
                    [[c["name"], c["legislature"], c["district"], field_label(c["field"]),
                      c["old"] or "(blank)", c["new"] or "(blank)", c["when"]] for c in items],
                    classes={4: "old", 5: "new"}))
        if section["minor"]:
            details = ", ".join(f"{field_label(f).lower()} ({n})" for f, n in section["minor"].most_common())
            out.append(f"<p class=\"minor\">Plus {sum(section['minor'].values())} minor update(s): "
                       f"{html.escape(details)}.</p>")
    out.append(f"<footer><p>Generated {html.escape(_local(datetime.now(timezone.utc)))} from "
               f"master.xlsx's Changelog. Times are local.</p></footer></body></html>")
    return "".join(out)


# --- Writing reports ------------------------------------------------------------

def write_report(master_path, start, end, path, title):
    """Build and write the HTML report for changes recorded in [start, end).
    Returns the digest."""
    changelog = read_master_changelog(master_path)
    entries = [e for e in changelog
               if (moment := parse_timestamp(e.get("timestamp"))) and start <= moment < end]
    people = _people(master_path)
    digest = build_digest(entries, people, _first_import_times(changelog, people))
    subtitle = f"{_describe_period(start, end)} -- {len(entries)} recorded change(s)"
    write_text_file(path, render_html(digest, title, subtitle))
    return digest


def _describe_period(start, end):
    """"2026-09-01 to 2026-09-28" for whole local days (end is exclusive)."""
    first, last = start.astimezone().date(), (end - timedelta(microseconds=1)).astimezone().date()
    return first.isoformat() if first == last else f"{first.isoformat()} to {last.isoformat()}"


def daily_report_path(day):
    return os.path.join(get_output_dir("changes"), f"changes_{day.isoformat()}.html")


def write_daily_report(day=None, master_path=None):
    """(Re)write the report for one local calendar day (default: today)."""
    day = day or date.today()
    start, end = _local_day_bounds(day)
    path = daily_report_path(day)
    write_report(master_path or get_master_path(), start, end, path, f"What changed on {day.isoformat()}")
    return path


def report_run(changes, master_path=None):
    """
    Called by the scrapers right after updating master.xlsx: print a short
    summary of this run's `changes` (the entries upsert_master returned)
    and refresh today's HTML report. Never raises -- the data is already
    saved, so a reporting problem must not fail the run.
    """
    try:
        master_path = master_path or get_master_path()
        if changes:
            people = _people(master_path)
            firsts = _first_import_times(read_master_changelog(master_path), people)
            digest = build_digest(changes, people, firsts)
            print("\n" + render_text(digest))
        path = write_daily_report(master_path=master_path)
        print(f"Today's change report: {path}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! Couldn't write the change report ({e}) -- master.xlsx itself is up to date.")


# --- Command line -------------------------------------------------------------------

def _iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD, e.g. 2026-09-01")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Write a readable report of what changed in master.xlsx over a period."
    )
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=7, metavar="N", help="the last N days (default 7)")
    window.add_argument("--since", type=_iso_date, metavar="YYYY-MM-DD", help="from this date until now")
    add_common_args(parser)
    args = parser.parse_args(argv)
    if args.since is None and args.days < 1:
        parser.error("--days must be at least 1")
    return args


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    master_path = get_master_path()
    if not os.path.exists(master_path):
        print(f"No master file at {master_path} yet -- run a scraper first.")
        return 1

    today = date.today()
    first_day = args.since or today - timedelta(days=args.days - 1)
    start, _ = _local_day_bounds(first_day)
    _, end = _local_day_bounds(today)
    path = os.path.join(get_output_dir("changes"), f"changes_{first_day.isoformat()}_to_{today.isoformat()}.html")
    digest = write_report(master_path, start, end, path,
                          f"What changed, {first_day.isoformat()} to {today.isoformat()}")
    print(render_text(digest, max_items=10) or "No changes were recorded in this period.")
    print(f"\nFull report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
