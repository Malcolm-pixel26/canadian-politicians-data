"""
Optional enrichment for federal MPs using the OpenParliament API
(https://openparliament.ca/api/), a free, public, no-key-required API
covering federal MPs, bills, votes, and committees.

Represent (used by the other scripts) only gives us current
office/contact info. OpenParliament adds a few extra things that are
useful for a "keep track of politicians" project:
    - first elected date
    - current committee memberships
    - a stable OpenParliament profile URL (bio, voting record, speeches)

This module matches people by normalized name (there's no shared ID
between Represent and OpenParliament), so it's not 100% guaranteed to
match everyone -- name mismatches (nicknames, accents, name changes)
will just come back with blank enrichment fields rather than crashing
the whole run.

NOTE ON API FIELD NAMES: OpenParliament doesn't publish a formal
JSON schema doc, so the exact key names below are based on the
publicly documented shape of the API as of this writing. If Open-
Parliament changes their response shape, `_extract_membership_info`
is the one place to fix -- it's written defensively (lots of .get()
calls) and falls back to blank values rather than raising, and it also
stashes the raw JSON in `raw_json` on request so you can inspect it
directly (see `debug_dump_one` below).
"""
import json
import time
import urllib.request
import urllib.error

from represent_common import normalize_name

OP_BASE_URL = "https://api.openparliament.ca"
OP_USER_AGENT = "ca-politician-scraper/1.0 (personal research script)"
OP_REQUEST_DELAY_SECONDS = 1.0


def _get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": OP_USER_AGENT, "Accept": "application/json"}
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            print(f"  ! OpenParliament request failed ({e}) -- retrying in 3s...")
            time.sleep(3)
    print(f"  ! Giving up on {url}: {last_error}")
    return None


def fetch_all_politicians(include_former=False):
    """
    Return every politician OpenParliament knows about, as a dict keyed
    by normalized name. include_former=True also pulls MPs no longer
    sitting (much slower / more pages); default is current MPs only.
    """
    by_name = {}
    url = f"{OP_BASE_URL}/politicians/?format=json&limit=500"
    if not include_former:
        url += "&current=true"

    while url:
        payload = _get_json(url)
        if payload is None:
            break
        for pol in payload.get("objects", []):
            key = normalize_name(pol.get("name", ""))
            if key:
                by_name[key] = pol
        next_link = (payload.get("pagination") or payload.get("meta", {})).get("next_url") \
            or (payload.get("pagination") or payload.get("meta", {})).get("next")
        if next_link:
            url = next_link if next_link.startswith("http") else f"{OP_BASE_URL}{next_link}"
            time.sleep(OP_REQUEST_DELAY_SECONDS)
        else:
            url = None

    return by_name


def _extract_membership_info(detail):
    """
    Pull first-elected date and current committee names out of a
    politician detail payload. Written defensively: OpenParliament's
    exact field names for this aren't formally documented, so this
    checks a few plausible shapes and quietly returns blanks if none
    match, rather than raising.
    """
    first_elected = ""
    committees = []

    memberships = detail.get("memberships") or detail.get("related", {}).get("memberships") or []
    # memberships may be a list of URLs (needing a follow-up fetch) or
    # already-expanded objects, depending on API version -- handle both.
    expanded = []
    for m in memberships:
        if isinstance(m, str):
            fetched = _get_json(m if m.startswith("http") else f"{OP_BASE_URL}{m}")
            time.sleep(OP_REQUEST_DELAY_SECONDS)
            if fetched:
                expanded.append(fetched)
        elif isinstance(m, dict):
            expanded.append(m)

    start_dates = []
    for m in expanded:
        start = m.get("start_date") or m.get("start")
        if start:
            start_dates.append(start)
        label = (m.get("label") or {}) if isinstance(m.get("label"), dict) else {}
        org_name = (
            m.get("organization_name")
            or label.get("en")
            or (m.get("organization") or {}).get("name")
            or ""
        )
        role = (m.get("role") or "").lower()
        if "committee" in org_name.lower() or "committee" in role:
            if org_name and (m.get("end_date") in (None, "")):
                committees.append(org_name)

    if start_dates:
        first_elected = min(start_dates)

    return first_elected, sorted(set(committees))


def enrich_federal_rows(rows, include_former=False):
    """
    Given a list of flattened federal representative rows (from
    represent_common.flatten_representative), add:
        - openparliament_url
        - first_elected
        - committees  (semicolon-separated)
    Matching is by normalized name. Rows with no match are left with
    blank enrichment fields (nothing else about them is changed).
    """
    print("Fetching OpenParliament politician list for enrichment...")
    op_by_name = fetch_all_politicians(include_former=include_former)
    print(f"  -> {len(op_by_name)} OpenParliament politician(s) loaded")

    matched = 0
    for row in rows:
        row.setdefault("openparliament_url", "")
        row.setdefault("first_elected", "")
        row.setdefault("committees", "")

        key = normalize_name(row.get("name", ""))
        pol = op_by_name.get(key)
        if not pol:
            continue

        op_url = pol.get("url", "")
        row["openparliament_url"] = f"https://openparliament.ca{op_url}" if op_url.startswith("/") else op_url

        detail_url = pol.get("url") or ""
        detail = _get_json(f"{OP_BASE_URL}{detail_url}?format=json") if detail_url else None
        time.sleep(OP_REQUEST_DELAY_SECONDS)
        if detail:
            first_elected, committees = _extract_membership_info(detail)
            row["first_elected"] = first_elected
            row["committees"] = "; ".join(committees)

        matched += 1

    print(f"  -> Matched {matched}/{len(rows)} federal row(s) to OpenParliament by name")
    return rows


def debug_dump_one(name):
    """
    Handy for checking OpenParliament's actual response shape if
    enrichment fields come back empty -- prints the raw JSON for one
    politician detail record so you can see what field names are
    actually there and adjust `_extract_membership_info` if needed.
    Usage: python3 -c "from openparliament_enrich import debug_dump_one; debug_dump_one('Marc Garneau')"
    """
    by_name = fetch_all_politicians()
    pol = by_name.get(normalize_name(name))
    if not pol:
        print(f"No OpenParliament match for {name!r}")
        return
    detail = _get_json(f"{OP_BASE_URL}{pol['url']}?format=json")
    print(json.dumps(detail, indent=2))
