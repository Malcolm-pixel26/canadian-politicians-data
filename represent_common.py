"""
Shared helpers for scraping Canadian elected officials from the
Represent API (Open North): https://represent.opennorth.ca/

Represent aggregates elected officials at ALL levels of Canadian
government (federal, provincial/territorial, and municipal/local) in
one free API, so we don't need a separate scraper for every
legislature's website.

Free tier limit: 60 requests/minute. We throttle a bit under that.

Everything is standard library except the Excel writer, which uses
openpyxl (install with: pip install openpyxl).
"""
import json
import csv
import os
import re
import time
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_URL = "https://represent.opennorth.ca"
REQUEST_DELAY_SECONDS = 1.3  # ~46 requests/minute, safely under the 60/min cap
USER_AGENT = "ca-politician-scraper/1.0 (personal research script)"


# --- Low-level HTTP -------------------------------------------------------

def _get_json(url):
    """GET a URL and parse JSON, with a small retry loop for transient errors."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            print(f"  ! request failed ({e}) -- retrying in 3s...")
            time.sleep(3)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


# --- Output location --------------------------------------------------------

def get_master_path():
    """Path to the single persistent master workbook, shared across all scrapers."""
    return os.path.join(get_output_dir(), "master.xlsx")


def get_output_dir(*subfolders):
    """
    Return (and create) a path inside ~/Desktop/output.
    Works on macOS, Windows, and Linux as long as the user's home
    directory has a Desktop folder.
    """
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "output", *subfolders)
    os.makedirs(out, exist_ok=True)
    return out


# --- Fetching data from Represent -------------------------------------------

def fetch_all_representative_sets():
    """Return every representative-set object (one per legislature/council)."""
    sets = []
    url = f"{BASE_URL}/representative-sets/?limit=100"
    while url:
        print(f"Fetching representative sets: {url}")
        payload = _get_json(url)
        sets.extend(payload.get("objects", []))
        next_link = payload.get("meta", {}).get("next")
        url = f"{BASE_URL}{next_link}" if next_link else None
        if url:
            time.sleep(REQUEST_DELAY_SECONDS)
    return sets


def fetch_representatives_by_url(representatives_url, set_name=None):
    """Return every representative belonging to one representative-set."""
    reps = []
    url = f"{BASE_URL}{representatives_url}?limit=100"
    label = set_name or representatives_url
    while url:
        payload = _get_json(url)
        reps.extend(payload.get("objects", []))
        next_link = payload.get("meta", {}).get("next")
        url = f"{BASE_URL}{next_link}" if next_link else None
        if url:
            time.sleep(REQUEST_DELAY_SECONDS)
    print(f"  -> {label}: {len(reps)} representative(s)")
    return reps


def normalize_name(name):
    """
    Lowercase, strip accents/punctuation, and collapse whitespace so names
    from different sources (Represent vs. OpenParliament vs. Wikipedia)
    can be compared. Not perfect (doesn't handle nicknames), but good
    enough for matching "Marc Garneau" == "Marc Garneau".
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slug_from_set(rep_set):
    """Derive a filesystem-friendly slug from a representative-set's url."""
    url = rep_set.get("url", "")
    parts = [p for p in url.split("/") if p]
    if parts:
        return parts[-1]
    return (rep_set.get("name") or "unknown").lower().replace(" ", "-")


# --- Classifying representative-sets by level of government ----------------

# Represent doesn't expose a clean "level" field on representative-sets,
# so we classify by the naming conventions Open North uses.
FEDERAL_MARKERS = ("house of commons",)

PROVINCIAL_MARKERS = (
    "legislature",
    "legislative assembly",
    "national assembly",
    "assemblée nationale",
    "assemblée législative",
    "house of assembly",
)

MUNICIPAL_MARKERS = ("council", "conseil")


def classify_set(rep_set):
    """
    Classify a representative-set as 'federal', 'provincial_territorial',
    'local', or 'other'.
    """
    name = (rep_set.get("name") or "").lower()

    if any(marker in name for marker in FEDERAL_MARKERS):
        return "federal"

    if any(marker in name for marker in PROVINCIAL_MARKERS) and not any(
        marker in name for marker in MUNICIPAL_MARKERS
    ):
        return "provincial_territorial"

    if any(marker in name for marker in MUNICIPAL_MARKERS):
        return "local"

    return "other"


# --- Flattening + writing output --------------------------------------------

CSV_FIELDS = [
    "category",
    "representative_set",
    "name",
    "first_name",
    "last_name",
    "elected_office",
    "district_name",
    "party_name",
    "email",
    "phone",
    "postal_address",
    "url",
    "personal_url",
    "photo_url",
    "gender",
    "source_url",
]


def flatten_representative(rep, category):
    """Turn one API representative object into a flat dict for CSV export."""
    offices = rep.get("offices") or []
    primary_office = next(
        (o for o in offices if o.get("type") == "legislature"),
        offices[0] if offices else {},
    )
    return {
        "category": category,
        "representative_set": rep.get("representative_set_name", ""),
        "name": rep.get("name", ""),
        "first_name": rep.get("first_name", ""),
        "last_name": rep.get("last_name", ""),
        "elected_office": rep.get("elected_office", ""),
        "district_name": rep.get("district_name", ""),
        "party_name": rep.get("party_name", ""),
        "email": rep.get("email", ""),
        "phone": primary_office.get("tel", ""),
        "postal_address": (primary_office.get("postal") or "").replace("\n", ", "),
        "url": rep.get("url", ""),
        "personal_url": rep.get("personal_url", ""),
        "photo_url": rep.get("photo_url", ""),
        "gender": rep.get("gender", ""),
        "source_url": rep.get("source_url", ""),
    }


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote {len(rows)} row(s) -> {path}")


def write_json(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(rows)} row(s) -> {path}")


# --- Master workbook (persistent, upserted across runs) --------------------
#
# Unlike the per-category files above (which are just a fresh snapshot each
# run), the master workbook at ~/Desktop/output/master.xlsx is meant to be
# updated in place every time you re-run a scraper:
#   - new people are appended
#   - people already in the master have their fields updated if anything
#     changed (email, party, riding, etc.)
#   - people who disappear from a re-scraped category are marked
#     status="removed" rather than deleted, so history isn't lost
#   - every add/update/removal is appended as a row on the shared
#     "Changelog" sheet
#
# Each level of government gets its own tab -- Federal, Provincial_
# Territorial, Local -- exactly like MASTER_CANADA_POLITICIAN_BUDGET.xlsx's
# tab layout. scrape_federal.py only ever touches the Federal tab,
# scrape_local.py only ever touches Local, etc., so the three scrapers can
# run in any order without clobbering each other's data. Changelog is the
# one shared/append-only sheet across all three.
#
# We key each person on Represent's own representative URL
# (e.g. "/representatives/house-of-commons/jane-doe/"), which is stable
# across runs for the same seat -- it's the closest thing to a durable ID
# Represent gives us.

MASTER_FIELDS = CSV_FIELDS + [
    "first_elected",       # federal-only, from OpenParliament enrichment
    "committees",          # federal-only, from OpenParliament enrichment
    "openparliament_url",  # federal-only, from OpenParliament enrichment
    "first_seen",
    "last_seen",
    "status",              # "active" or "removed"
]

CHANGELOG_FIELDS = [
    "timestamp",
    "category",
    "key",
    "name",
    "change_type",  # "added", "updated", "removed"
    "field",
    "old_value",
    "new_value",
]

# category_scope value -> the tab it lives on in master.xlsx
CATEGORY_TO_MASTER_SHEET = {
    "federal": "Federal",
    "provincial_territorial": "Provincial_Territorial",
    "local": "Local",
}

MASTER_TAB_ORDER = ["Federal", "Provincial_Territorial", "Local", "Changelog"]


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def master_key(row):
    """The stable identifier for a person: their Represent representative URL."""
    return row.get("url") or f"{row.get('representative_set','')}|{row.get('name','')}"


def _reorder_master_sheets(wb):
    order = [name for name in MASTER_TAB_ORDER if name in wb.sheetnames]
    order += [name for name in wb.sheetnames if name not in order]
    wb._sheets = [wb[name] for name in order]


def read_master_tab(path, sheet_name):
    """
    Load one level-of-government tab of master.xlsx into a dict of
    {key: row}. Returns {} if the file or that tab doesn't exist yet.
    """
    if not os.path.exists(path):
        return {}

    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    rows_by_key = {}
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for excel_row in ws.iter_rows(min_row=2):
            values = [c.value for c in excel_row]
            if all(v is None for v in values):
                continue
            row = {
                MASTER_FIELDS[i]: (values[i] if i < len(values) and values[i] is not None else "")
                for i in range(len(MASTER_FIELDS))
            }
            key = master_key(row)
            if key:
                rows_by_key[key] = row
    wb.close()
    return rows_by_key


def read_all_master_rows(path):
    """
    Load every level-of-government tab combined into one dict of
    {key: row} -- for things like lookup_politician.py's cached search,
    which needs to search across all categories at once. Returns {} if
    the file doesn't exist yet.
    """
    combined = {}
    for sheet_name in ("Federal", "Provincial_Territorial", "Local"):
        combined.update(read_master_tab(path, sheet_name))
    return combined


def read_master_changelog(path):
    """Load the shared Changelog sheet. Returns [] if it doesn't exist yet."""
    if not os.path.exists(path):
        return []

    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    changelog_rows = []
    if "Changelog" in wb.sheetnames:
        ws = wb["Changelog"]
        for excel_row in ws.iter_rows(min_row=2):
            values = [c.value for c in excel_row]
            if all(v is None for v in values):
                continue
            row = {
                CHANGELOG_FIELDS[i]: (values[i] if i < len(values) and values[i] is not None else "")
                for i in range(len(CHANGELOG_FIELDS))
            }
            changelog_rows.append(row)
    wb.close()
    return changelog_rows


def upsert_master(master_path, new_rows, category_scope, attempted_sets=None):
    """
    Merge `new_rows` (a freshly-scraped batch, all belonging to
    `category_scope`, e.g. "federal") into that category's own tab of the
    persistent master workbook, and return the changelog entries
    generated by this run.

    Only touches the one tab for `category_scope` -- the other two levels'
    tabs are loaded and re-saved untouched, so running e.g.
    scrape_federal.py alone never wipes out your provincial/local data.

    `attempted_sets`, if given, is the set of representative_set names
    actually scraped this run (e.g. when scrape_local.py is run with
    --filter). Removal-detection then only applies within those sets --
    councils that were never attempted this run are left alone instead
    of being wrongly marked "removed".
    """
    sheet_name = CATEGORY_TO_MASTER_SHEET.get(category_scope, category_scope.title())
    existing = read_master_tab(master_path, sheet_name)
    changelog_rows = read_master_changelog(master_path)
    now = _now_iso()
    new_changelog_entries = []

    seen_keys_this_batch = set()

    for new_row in new_rows:
        key = master_key(new_row)
        if not key:
            continue
        seen_keys_this_batch.add(key)

        if key not in existing:
            merged = dict(new_row)
            merged["first_elected"] = new_row.get("first_elected", "")
            merged["committees"] = new_row.get("committees", "")
            merged["openparliament_url"] = new_row.get("openparliament_url", "")
            merged["first_seen"] = now
            merged["last_seen"] = now
            merged["status"] = "active"
            existing[key] = merged
            new_changelog_entries.append({
                "timestamp": now,
                "category": category_scope,
                "key": key,
                "name": new_row.get("name", ""),
                "change_type": "added",
                "field": "",
                "old_value": "",
                "new_value": "",
            })
        else:
            old_row = existing[key]
            compare_fields = [f for f in CSV_FIELDS if f not in ("category",)] + [
                "first_elected", "committees", "openparliament_url",
            ]
            for field in compare_fields:
                old_val = str(old_row.get(field, "") or "")
                new_val = str(new_row.get(field, "") or "")
                if new_val and old_val != new_val:
                    new_changelog_entries.append({
                        "timestamp": now,
                        "category": category_scope,
                        "key": key,
                        "name": new_row.get("name", old_row.get("name", "")),
                        "change_type": "updated",
                        "field": field,
                        "old_value": old_val,
                        "new_value": new_val,
                    })
                    old_row[field] = new_val
            old_row["last_seen"] = now
            if old_row.get("status") != "active":
                old_row["status"] = "active"

    # Anything previously on this tab that wasn't in today's batch has
    # presumably left office / been removed from Represent.
    for key, row in existing.items():
        if attempted_sets is not None and row.get("representative_set") not in attempted_sets:
            continue
        if key not in seen_keys_this_batch:
            if row.get("status") != "removed":
                row["status"] = "removed"
                new_changelog_entries.append({
                    "timestamp": now,
                    "category": category_scope,
                    "key": key,
                    "name": row.get("name", ""),
                    "change_type": "removed",
                    "field": "",
                    "old_value": "",
                    "new_value": "",
                })

    all_changelog_rows = changelog_rows + new_changelog_entries
    write_master_tab(master_path, sheet_name, list(existing.values()), all_changelog_rows)
    return new_changelog_entries


def write_master_tab(path, sheet_name, tab_rows, changelog_rows):
    """
    Write/replace exactly one level-of-government tab of master.xlsx
    (creating the file if it doesn't exist yet), and rewrite the shared
    Changelog sheet with the full (existing + new) changelog, newest
    first. Any other level-of-government tab already in the workbook is
    preserved as-is.
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    header_font = Font(name="Arial", bold=True)
    body_font = Font(name="Arial")

    if os.path.exists(path):
        wb = load_workbook(path)
        # drop openpyxl's default blank "Sheet" if it's still sitting
        # there empty from a previous run that only just created the file
        if (
            "Sheet" in wb.sheetnames
            and wb["Sheet"].max_row == 1
            and wb["Sheet"].max_column == 1
            and wb["Sheet"]["A1"].value is None
        ):
            del wb["Sheet"]
    else:
        wb = Workbook()
        wb.remove(wb.active)

    # --- This category's tab ---
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    for col_idx, field in enumerate(MASTER_FIELDS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=field.replace("_", " ").title())
        cell.font = header_font

    tab_rows_sorted = sorted(tab_rows, key=lambda r: r.get("name", ""))
    for row_idx, row in enumerate(tab_rows_sorted, start=2):
        for col_idx, field in enumerate(MASTER_FIELDS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(field, ""))
            cell.font = body_font

    ws.freeze_panes = "A2"
    if tab_rows_sorted:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(MASTER_FIELDS))}{len(tab_rows_sorted) + 1}"
    for col_idx, field in enumerate(MASTER_FIELDS, start=1):
        max_len = len(field)
        for row in tab_rows_sorted:
            val = row.get(field, "")
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    # --- Shared Changelog sheet (full rewrite, newest first) ---
    if "Changelog" in wb.sheetnames:
        del wb["Changelog"]
    ws2 = wb.create_sheet("Changelog")
    for col_idx, field in enumerate(CHANGELOG_FIELDS, start=1):
        cell = ws2.cell(row=1, column=col_idx, value=field.replace("_", " ").title())
        cell.font = header_font

    changelog_sorted = sorted(changelog_rows, key=lambda r: r.get("timestamp", ""), reverse=True)
    for row_idx, row in enumerate(changelog_sorted, start=2):
        for col_idx, field in enumerate(CHANGELOG_FIELDS, start=1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=row.get(field, ""))
            cell.font = body_font

    ws2.freeze_panes = "A2"
    if changelog_sorted:
        ws2.auto_filter.ref = f"A1:{get_column_letter(len(CHANGELOG_FIELDS))}{len(changelog_sorted) + 1}"
    for col_idx, field in enumerate(CHANGELOG_FIELDS, start=1):
        max_len = len(field)
        for row in changelog_sorted:
            val = row.get(field, "")
            if val:
                max_len = max(max_len, len(str(val)))
        ws2.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    _reorder_master_sheets(wb)
    wb.save(path)
    print(
        f"'{sheet_name}' tab updated -> {path} "
        f"({len(tab_rows)} row(s), {len(changelog_rows)} changelog entrie(s))"
    )


# --- Shared multi-tab budget workbook (federal / provincial / local) -------
#
# MASTER_CANADA_POLITICIAN_BUDGET.xlsx holds one tab per level of
# government (Federal, Provincial_Territorial, Local) plus a Notes tab.
# Three separate scripts (scrape_budget_federal.py,
# scrape_budget_provincial_territorial.py, scrape_budget_local.py) each
# own exactly one data tab. write_budget_tab() below rewrites only the
# tab it's given and upserts that category's row in Notes -- every other
# tab in the workbook is loaded and re-saved untouched, so the three
# scripts can run in any order, independently, without wiping out each
# other's data.

BUDGET_TAB_ORDER = ["Federal", "Provincial_Territorial", "Local", "Notes"]


def _reorder_budget_sheets(wb):
    order = [name for name in BUDGET_TAB_ORDER if name in wb.sheetnames]
    order += [name for name in wb.sheetnames if name not in order]
    wb._sheets = [wb[name] for name in order]


def _upsert_notes_row(wb, header_font, body_font, category, note_text):
    from openpyxl.styles import Font

    if "Notes" not in wb.sheetnames:
        ws = wb.create_sheet("Notes")
        headers = ["Category", "Scope / Notes", "Last Updated"]
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 110
        ws.column_dimensions["C"].width = 20
        ws.freeze_panes = "A2"
    else:
        ws = wb["Notes"]

    target_row = None
    for row_idx in range(2, ws.max_row + 1):
        if ws.cell(row=row_idx, column=1).value == category:
            target_row = row_idx
            break
    if target_row is None:
        target_row = ws.max_row + 1

    ws.cell(row=target_row, column=1, value=category).font = body_font
    ws.cell(row=target_row, column=2, value=note_text).font = Font(name="Arial", italic=True)
    ws.cell(row=target_row, column=3, value=_now_iso()).font = body_font


def write_budget_tab(path, sheet_name, fields, rows, notes_category, notes_text,
                      sort_field="total_contributions"):
    """
    Write/replace exactly one tab of the shared MASTER_CANADA_POLITICIAN_
    BUDGET.xlsx workbook (creating the file if it doesn't exist yet), and
    upsert that category's row on the Notes tab. Any other tabs already
    in the workbook -- other categories' budget data, other categories'
    notes -- are preserved as-is.
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    header_font = Font(name="Arial", bold=True)
    body_font = Font(name="Arial")

    if os.path.exists(path):
        wb = load_workbook(path)
        # drop openpyxl's default blank "Sheet" if it's still sitting
        # there empty from a previous run that only just created the file
        if (
            "Sheet" in wb.sheetnames
            and wb["Sheet"].max_row == 1
            and wb["Sheet"].max_column == 1
            and wb["Sheet"]["A1"].value is None
        ):
            del wb["Sheet"]
    else:
        wb = Workbook()
        wb.remove(wb.active)

    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    for col_idx, field in enumerate(fields, start=1):
        cell = ws.cell(row=1, column=col_idx, value=field.replace("_", " ").title())
        cell.font = header_font

    def _sort_key(row):
        val = row.get(sort_field)
        return val if isinstance(val, (int, float)) else -1

    rows_sorted = sorted(rows, key=_sort_key, reverse=True) if sort_field else list(rows)

    for row_idx, row in enumerate(rows_sorted, start=2):
        for col_idx, field in enumerate(fields, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(field, ""))
            cell.font = body_font

    ws.freeze_panes = "A2"
    if rows_sorted:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(fields))}{len(rows_sorted) + 1}"
    for col_idx, field in enumerate(fields, start=1):
        max_len = len(field)
        for row in rows_sorted:
            val = row.get(field, "")
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    _upsert_notes_row(wb, header_font, body_font, notes_category, notes_text)
    _reorder_budget_sheets(wb)

    wb.save(path)
    print(f"'{sheet_name}' tab updated -> {path} ({len(rows_sorted)} row(s))")


def write_xlsx(path, rows, sheet_name="Representatives"):
    """Write rows (list of dicts using CSV_FIELDS keys) to a formatted .xlsx file."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError(
            "openpyxl is required for Excel output. Install it with: pip install openpyxl"
        )

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]  # Excel sheet name limit

    header_font = Font(name="Arial", bold=True)
    body_font = Font(name="Arial")

    # Header row
    for col_idx, field in enumerate(CSV_FIELDS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=field.replace("_", " ").title())
        cell.font = header_font

    # Data rows
    for row_idx, row in enumerate(rows, start=2):
        for col_idx, field in enumerate(CSV_FIELDS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(field, ""))
            cell.font = body_font

    # Freeze header row and add autofilter
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(CSV_FIELDS))}{len(rows) + 1}"

    # Rough auto-sizing based on the longest value seen per column (capped)
    for col_idx, field in enumerate(CSV_FIELDS, start=1):
        max_len = len(field)
        for row in rows:
            val = row.get(field, "")
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    wb.save(path)
    print(f"Wrote {len(rows)} row(s) -> {path}")
