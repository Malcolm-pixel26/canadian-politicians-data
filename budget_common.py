"""
Shared plumbing for the three budget scripts (scrape_budget_federal.py,
scrape_budget_provincial_territorial.py, scrape_budget_local.py): the
column layout every data tab of MASTER_CANADA_POLITICIAN_BUDGET.xlsx
uses, and the logic that lines contribution totals up with the
politicians already in master.xlsx.

"Contribution totals" -- what aggregate_contributions() in
scrape_budget_federal.py returns, and what every PROVINCE_FETCHERS /
MUNICIPAL_FETCHERS function must return -- is a dict keyed by
normalize_name(recipient) whose values have at least:

    raw_recipient_name, recipient_type, total_contributions, contribution_count

and optionally:

    first_name, last_name            (improves matching, see below)
    non_monetary_contributions
    min_date, max_date               (ISO dates; shown as date_range)
    electoral_districts              (a set or a "; "-joined string)
"""
import os

from represent_common import (
    get_master_path,
    get_output_dir,
    normalize_name,
    read_master_tab,
    write_budget_tab,
)

OUTPUT_FILENAME = "MASTER_CANADA_POLITICIAN_BUDGET.xlsx"

# Shared column layout across all three budget tabs (Federal,
# Provincial_Territorial, Local) so the tabs are easy to compare.
BUDGET_FIELDS = [
    "name",
    "category",
    "party_name",
    "representative_set",
    "province_territory",
    "recipient_type",
    "total_contributions",         # monetary contributions
    "non_monetary_contributions",  # in-kind contributions
    "contribution_count",
    "avg_contribution",            # total_contributions / contribution_count
    "date_range",
    "electoral_districts",
    "match_status",
]

UNMATCHED_RECIPIENT_STATUS = "unmatched recipient (not in master.xlsx)"
_CONTRIBUTION_FIELDS = BUDGET_FIELDS[BUDGET_FIELDS.index("recipient_type"):-1]


def budget_workbook_path():
    return os.path.join(get_output_dir(), OUTPUT_FILENAME)


def read_master_politicians(sheet_name):
    """Every politician on one tab of master.xlsx (active and removed)."""
    return list(read_master_tab(get_master_path(), sheet_name).values())


def _short_key(first, last, full_name=""):
    """First word of the first name + the last name, e.g. "jasraj hallan"
    for "Jasraj Singh Hallan". Falls back to the first and last words of
    `full_name` when first/last aren't known."""
    first_words = normalize_name(first).split()
    last = normalize_name(last)
    if first_words and last:
        return f"{first_words[0]} {last}"
    words = normalize_name(full_name).split()
    return f"{words[0]} {words[-1]}" if len(words) >= 2 else ""


def _joined(value):
    if isinstance(value, (set, list, tuple)):
        return "; ".join(sorted(str(v) for v in value if v))
    return value or ""


def contribution_columns(entry):
    """The budget-tab columns for one contribution-totals entry."""
    count = entry.get("contribution_count") or 0
    total = entry.get("total_contributions") or 0.0
    min_date, max_date = entry.get("min_date") or "", entry.get("max_date") or ""
    date_range = entry.get("date_range") or (
        min_date if min_date == max_date else f"{min_date} to {max_date}"
    )
    return {
        "recipient_type": _joined(entry.get("recipient_type")),
        "total_contributions": round(total, 2),
        "non_monetary_contributions": round(entry.get("non_monetary_contributions") or 0.0, 2),
        "contribution_count": count,
        "avg_contribution": round(total / count, 2) if count else 0,
        "date_range": date_range,
        "electoral_districts": _joined(entry.get("electoral_districts")),
    }


def _riding_differs(row, contrib):
    """True when we know both the politician's current district and the
    districts their contributions were filed under, and they don't overlap
    -- a hint the name match may be a different person with the same name
    (ridings do get renamed, so it's a flag to check, not proof)."""
    district = normalize_name(row.get("district_name", ""))
    districts = contrib.get("electoral_districts") or ()
    if isinstance(districts, str):
        districts = districts.split(";")
    filed_under = {normalize_name(d) for d in districts if normalize_name(d)}
    return bool(district and filed_under) and district not in filed_under


def build_budget_rows(master_rows, contribution_totals, not_found_status):
    """
    One row per politician in `master_rows` -- matched to their
    contribution totals by normalized full name, or failing that by first
    word of the first name + last name when that's unambiguous (so
    "Jasraj Hallan" still finds "Jasraj Singh Hallan"); `not_found_status`
    otherwise -- followed by one row per recipient in the totals that
    matched nobody, so no funding data silently disappears.
    """
    by_short_key = {}
    for key, entry in contribution_totals.items():
        short = _short_key(entry.get("first_name", ""), entry.get("last_name", ""), entry.get("raw_recipient_name") or key)
        if short:
            by_short_key.setdefault(short, []).append(key)

    budget_rows = []
    matched_keys = set()
    for row in master_rows:
        entry = {
            "name": row.get("name", ""),
            "category": row.get("category", ""),
            "party_name": row.get("party_name", ""),
            "representative_set": row.get("representative_set", ""),
            "province_territory": row.get("province_territory", ""),
        }
        key = normalize_name(row.get("name", ""))
        status = "matched"
        if key not in contribution_totals:
            candidates = by_short_key.get(
                _short_key(row.get("first_name", ""), row.get("last_name", ""), row.get("name", "")), []
            )
            key = candidates[0] if len(candidates) == 1 else None
            status = "matched (first + last name only -- check)"
        if key:
            matched_keys.add(key)
            contrib = contribution_totals[key]
            entry.update(contribution_columns(contrib))
            if _riding_differs(row, contrib):
                status += "; riding differs -- check"
            entry["match_status"] = status
        else:
            entry.update({field: "" for field in _CONTRIBUTION_FIELDS})
            entry["match_status"] = not_found_status
        budget_rows.append(entry)

    for key, contrib in contribution_totals.items():
        if key in matched_keys:
            continue
        entry = {"name": contrib.get("raw_recipient_name") or key, "category": "", "party_name": "",
                 "representative_set": "", "province_territory": ""}
        entry.update(contribution_columns(contrib))
        entry["match_status"] = UNMATCHED_RECIPIENT_STATUS
        budget_rows.append(entry)

    return budget_rows


def collect_from_fetchers(fetchers, only=None, kind="jurisdiction"):
    """Run the configured fetchers (or just `only`) and merge their totals."""
    if only:
        fetcher = fetchers.get(only)
        if not fetcher:
            configured = ", ".join(sorted(fetchers)) or "none configured yet"
            raise SystemExit(f"No fetcher configured for {kind} '{only}'. Configured: {configured}")
        print(f"Fetching {only}...")
        return dict(fetcher())
    totals = {}
    for slug, fetcher in fetchers.items():
        print(f"Fetching {slug}...")
        totals.update(fetcher())
    return totals


def write_budget_rows(sheet_name, rows, notes_text):
    """Write one data tab (+ its Notes row) of the budget workbook."""
    return write_budget_tab(budget_workbook_path(), sheet_name, BUDGET_FIELDS, rows, sheet_name, notes_text)
