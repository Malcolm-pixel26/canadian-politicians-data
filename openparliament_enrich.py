"""
Optional enrichment for federal MPs from two free, public, no-key
sources:

  - OpenParliament (https://openparliament.ca/api/): first elected date
    and the MP's OpenParliament profile URL (bio, voting record, speeches).
  - The House of Commons' own per-MP XML (https://www.ourcommons.ca/en/open-data,
    "Individual members of Parliament and their current roles"): current
    committee memberships.

Committees used to be read from OpenParliament's "memberships" list, but
those are House *seat* terms ("Conservative MP for Edmonton Manning"),
never committees -- so the committees column always came back blank.
The House of Commons XML lists them directly
(CommitteeMemberRoles/CommitteeMemberRole/CommitteeName).

Matching:
  - House of Commons: exact, by person ID. Represent's `url` for each MP
    *is* their ourcommons.ca profile (…/Members/en/jasraj-hallan(105630)),
    and appending /xml gives the roles XML -- no name matching involved.
  - OpenParliament: its list endpoint shares no ID with Represent, so we
    match on normalized name, then fall back to riding + last name (e.g.
    Represent's "Jasraj Hallan" is OpenParliament's "Jasraj Singh Hallan",
    which exact-name matching missed), and then confirm the pairing with
    the House of Commons ID OpenParliament includes in each MP's detail
    record (other_info.parl_mp_id). Unmatched MPs just get blank
    enrichment fields rather than crashing the run.

The two sources are fetched in parallel (one thread each, each throttled
to its own host's polite rate), so enrichment takes about as long as the
old OpenParliament-only pass did.
"""
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from represent_common import (
    FEDERAL_ENRICHMENT_FIELDS,
    FRESH_FIELDS_KEY,
    MAX_CONSECUTIVE_FAILURES,
    FetchError,
    http_get,
    http_get_json,
    normalize_name,
)

OP_BASE_URL = "https://api.openparliament.ca"
# OpenParliament asks clients to pin the API version they expect.
OP_HEADERS = {"API-Version": "v1"}

# House of Commons person ID inside an ourcommons.ca profile URL,
# e.g. https://www.ourcommons.ca/Members/en/jasraj-hallan(105630)
_COMMONS_ID_RE = re.compile(r"ourcommons\.ca/members/[a-z]{2}/[^/?#]*\((\d+)\)", re.IGNORECASE)

PROGRESS_EVERY = 50


def house_of_commons_id(url):
    """The House of Commons person ID in an ourcommons.ca profile URL, or ""."""
    match = _COMMONS_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _mark_fresh(row, *fields):
    row.setdefault(FRESH_FIELDS_KEY, set()).update(fields)


# --- OpenParliament ---------------------------------------------------------

def op_get_all(path, **params):
    """Every object from an OpenParliament listing (e.g. "/votes/"),
    following its pagination.next_url links. `params` become the query
    string (format=json and limit=500 are added)."""
    query = urllib.parse.urlencode(dict({"format": "json", "limit": 500}, **params))
    url = f"{OP_BASE_URL}{path}?{query}"
    objects = []
    visited = set()
    while url and url not in visited:
        visited.add(url)
        payload = http_get_json(url, headers=OP_HEADERS)
        objects.extend(payload.get("objects") or [])
        next_link = (payload.get("pagination") or {}).get("next_url")
        url = urllib.parse.urljoin(OP_BASE_URL, next_link) if next_link else None
    return objects


def fetch_politician_list(include_former=False):
    """Every politician OpenParliament lists: current MPs by default, or
    current + former with include_former=True."""
    if include_former:
        return op_get_all("/politicians/", include="all")  # there's no "current" parameter
    return op_get_all("/politicians/")


def fetch_all_politicians(include_former=False):
    """OpenParliament politicians as a dict keyed by normalized name."""
    return {
        normalize_name(p.get("name", "")): p
        for p in fetch_politician_list(include_former)
        if p.get("name")
    }


def _riding_name(politician):
    name = (politician.get("current_riding") or {}).get("name") or ""
    return name.get("en", "") if isinstance(name, dict) else str(name)


def _last_names_agree(row, politician):
    """True if the row's last name ends the OpenParliament name, e.g.
    "hallan" / "jasraj singh hallan"."""
    last = normalize_name(row.get("last_name") or "").split()
    if not last:
        last = normalize_name(row.get("name") or "").split()[-1:]
    pol_tokens = normalize_name(politician.get("name") or "").split()
    return bool(last) and pol_tokens[-len(last):] == last


def match_rows_to_politicians(rows, politicians):
    """[(row, politician, how)] for every row that pairs with exactly one
    politician -- by normalized name, or failing that by riding + last name."""
    by_name, by_riding = {}, {}
    for pol in politicians:
        by_name.setdefault(normalize_name(pol.get("name") or ""), []).append(pol)
        by_riding.setdefault(normalize_name(_riding_name(pol)), []).append(pol)
    by_name.pop("", None)
    by_riding.pop("", None)

    matched = []
    for row in rows:
        candidates = by_name.get(normalize_name(row.get("name") or ""), [])
        how = "name"
        if len(candidates) != 1:
            riding = normalize_name(row.get("district_name") or "")
            candidates = [p for p in by_riding.get(riding, []) if _last_names_agree(row, p)]
            how = "riding"
        if len(candidates) == 1:
            matched.append((row, candidates[0], how))
    return matched


def first_elected_from_detail(detail):
    """Earliest start date across an MP's House membership terms."""
    starts = [
        m.get("start_date")
        for m in detail.get("memberships") or []
        if isinstance(m, dict) and m.get("start_date")
    ]
    return min(starts) if starts else ""


def _parl_mp_ids(detail):
    ids = (detail.get("other_info") or {}).get("parl_mp_id") or []
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    return {str(i) for i in ids}


def _enrich_from_openparliament(rows, include_former=False):
    politicians = fetch_politician_list(include_former)
    matches = match_rows_to_politicians(rows, politicians)
    print(f"  OpenParliament: {len(politicians)} politician(s) listed, {len(matches)}/{len(rows)} MP(s) matched")

    done = by_riding = rejected = consecutive_failures = 0
    for n, (row, pol, how) in enumerate(matches, start=1):
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print("  ! OpenParliament seems to be unreachable -- skipping the remaining MPs.")
            break
        op_path = pol.get("url") or ""
        try:
            detail = http_get_json(f"{OP_BASE_URL}{op_path}?format=json", headers=OP_HEADERS)
            consecutive_failures = 0
        except FetchError as e:
            print(f"  ! OpenParliament: skipping {row.get('name')!r} ({e})")
            consecutive_failures += 1
            continue
        commons_id = house_of_commons_id(row.get("url"))
        op_ids = _parl_mp_ids(detail)
        if commons_id and op_ids and commons_id not in op_ids:
            print(f"  ! OpenParliament: {row.get('name')!r} matched {pol.get('name')!r} by {how}, "
                  f"but their House of Commons IDs differ -- leaving it blank")
            rejected += 1
            continue
        row["openparliament_url"] = f"https://openparliament.ca{op_path}" if op_path.startswith("/") else op_path
        row["first_elected"] = first_elected_from_detail(detail)
        _mark_fresh(row, "openparliament_url", "first_elected")
        done += 1
        by_riding += how == "riding"
        if n % PROGRESS_EVERY == 0:
            print(f"  ... OpenParliament {n}/{len(matches)}")

    note = f", {rejected} rejected by the ID check" if rejected else ""
    print(f"  -> OpenParliament: enriched {done}/{len(rows)} MP(s) "
          f"({by_riding} matched by riding rather than exact name{note})")


# --- House of Commons roles XML --------------------------------------------

def _local_tag(tag):
    return tag.rsplit("}", 1)[-1]


def committees_from_profile_xml(xml_bytes):
    """
    Current committee memberships from a House of Commons member profile
    XML (…/Members/en/<name>(<id>)/xml): the committee name for ordinary
    members, "Name (Chair)" / "Name (Vice-Chair)" etc. otherwise. Roles
    with an end date are skipped.
    """
    if b"<!DOCTYPE" in xml_bytes[:4096].upper():
        raise ET.ParseError("unexpected DOCTYPE in House of Commons XML")
    root = ET.fromstring(xml_bytes)
    committees = set()
    for element in root.iter():
        if _local_tag(element.tag) != "CommitteeMemberRole":
            continue
        fields = {_local_tag(child.tag): (child.text or "").strip() for child in element}
        name = fields.get("CommitteeName")
        if not name or fields.get("ToDateTime"):
            continue
        role = fields.get("AffiliationRoleName", "")
        committees.add(name if role in ("", "Member") else f"{name} ({role})")
    return sorted(committees)


def _enrich_committees(rows):
    targets = [r for r in rows if house_of_commons_id(r.get("url"))]
    done = failed = consecutive_failures = 0
    for n, row in enumerate(targets, start=1):
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print("  ! ourcommons.ca seems to be unreachable -- skipping the remaining MPs.")
            break
        xml_url = row["url"].rstrip("/") + "/xml"
        try:
            committees = committees_from_profile_xml(http_get(xml_url, accept="application/xml"))
        except (FetchError, ET.ParseError) as e:
            failed += 1
            consecutive_failures += 1
            if failed <= 5:
                print(f"  ! House of Commons roles unavailable for {row.get('name')!r} ({e})")
            continue
        consecutive_failures = 0
        row["committees"] = "; ".join(committees)
        _mark_fresh(row, "committees")
        done += 1
        if n % PROGRESS_EVERY == 0:
            print(f"  ... House of Commons {n}/{len(targets)}")
    note = f" ({failed} failed)" if failed else ""
    print(f"  -> House of Commons: committee data for {done}/{len(rows)} MP(s){note}")


# --- Entry point ----------------------------------------------------------

def enrich_federal_rows(rows, include_former=False):
    """
    Given a list of flattened federal representative rows (from
    represent_common.flatten_representative), add:
        - openparliament_url
        - first_elected
        - committees  (semicolon-separated)
    Rows that can't be matched keep blank enrichment fields (nothing else
    about them is changed). Each field that was actually looked up is also
    listed under row[FRESH_FIELDS_KEY], so master.xlsx can record e.g. an
    MP leaving all committees, without wiping data when enrichment fails.
    """
    for row in rows:
        for field in FEDERAL_ENRICHMENT_FIELDS:
            row.setdefault(field, "")
        row.setdefault(FRESH_FIELDS_KEY, set())

    print(f"Enriching {len(rows)} federal row(s) from OpenParliament and the House of Commons...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        steps = [
            ("OpenParliament", pool.submit(_enrich_from_openparliament, rows, include_former)),
            ("House of Commons", pool.submit(_enrich_committees, rows)),
        ]
        for label, future in steps:
            try:
                future.result()
            except Exception as e:  # one source failing shouldn't sink the other
                print(f"  ! {label} enrichment failed ({e}) -- continuing without it.")
    return rows


def debug_dump_one(name):
    """
    Handy for checking OpenParliament's actual response shape if
    enrichment fields come back empty -- prints the raw JSON for one
    politician detail record.
    Usage: python3 -c "from openparliament_enrich import debug_dump_one; debug_dump_one('Marc Garneau')"
    """
    pol = fetch_all_politicians(include_former=True).get(normalize_name(name))
    if not pol:
        print(f"No OpenParliament match for {name!r}")
        return
    detail = http_get_json(f"{OP_BASE_URL}{pol['url']}?format=json", headers=OP_HEADERS)
    print(json.dumps(detail, indent=2, ensure_ascii=False))
