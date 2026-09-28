#!/usr/bin/env python3
"""
Federal voting records and parliamentary activity, from the OpenParliament
API (https://openparliament.ca/api/): how every MP voted on every
recorded vote ("division") in a session of the House of Commons, plus
per-MP totals. Writes:

    ~/Desktop/output/federal/votes_<session>.xlsx
        MP Summary  -- per MP: votes cast (yes / no / paired), missed,
                       votes against their party, and more
        Votes       -- each vote: number, date, bill, what it was on,
                       result, totals and how each party voted
        Ballots     -- one row per MP, one column per vote
    ~/Desktop/output/federal/votes_<session>_summary.csv
    ~/Desktop/output/federal/votes_<session>_ballots.csv   (one row per MP per vote)

(Runs limited with --bill add the bill numbers to the file names, so
they never overwrite the full-session files.)

Usage:
    python3 scrape_votes.py                            # every vote in the current session
    python3 scrape_votes.py --bill C-30 --bill C-266    # only votes on these bills
    python3 scrape_votes.py --session 44-1              # an earlier session
    python3 scrape_votes.py --activity                  # also count bills sponsored and speeches

"Against party" compares each Yes/No ballot with how most of the MP's
party voted -- the party they belonged to at the time of that vote, so
MPs who crossed the floor are compared with the right caucus.
Independents have no party line. (OpenParliament's own "dissent" flag
is marked "not reliable for research", so it isn't used.)

Speed: one request per vote -- a few minutes for a full session at
OpenParliament's polite rate of about one request a second. Recorded
votes don't change, so each vote's ballots are cached under
~/Desktop/output/_cache/openparliament/ and later runs only fetch new
votes. --activity adds a few requests per MP (roughly 10-20 more
minutes for the whole House).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta

from openparliament_enrich import OP_BASE_URL, OP_HEADERS, fetch_politician_list, op_get_all
from represent_common import (
    MAX_CONSECUTIVE_FAILURES,
    FetchError,
    add_common_args,
    apply_common_args,
    explain_fetch_error,
    get_output_dir,
    http_get_json,
    write_csv,
    write_text_file,
    write_workbook,
)

BALLOT_CHOICES = ("Yes", "No", "Paired", "Didn't vote")
OP_SITE = "https://openparliament.ca"

SUMMARY_COLUMNS = [
    ("name", "Name"),
    ("party", "Party"),
    ("riding", "Riding"),
    ("province", "Province"),
    ("current_mp", "Current MP"),
    ("votes", "Votes held"),
    ("yes", "Yes"),
    ("no", "No"),
    ("paired", "Paired"),
    ("didnt_vote", "Didn't vote"),
    ("participation_pct", "Participation %"),
    ("against_party", "Votes against party"),
    ("with_party_pct", "With party %"),
    ("bills_sponsored", "Bills sponsored"),
    ("house_speeches", "House speeches"),
    ("committee_speeches", "Committee speeches"),
    ("openparliament_url", "OpenParliament"),
]
ACTIVITY_FIELDS = ("bills_sponsored", "house_speeches", "committee_speeches")

VOTE_COLUMNS = [
    ("number", "Vote #"),
    ("date", "Date"),
    ("bill", "Bill"),
    ("description", "Description"),
    ("result", "Result"),
    ("yea_total", "Yea"),
    ("nay_total", "Nay"),
    ("paired_total", "Paired"),
    ("party_positions", "How the parties voted"),
    ("url", "OpenParliament"),
]

BALLOT_CSV_FIELDS = ["session", "vote_number", "date", "bill", "name", "party_at_vote", "riding",
                     "ballot", "party_position", "against_party"]


# --- Fetching -------------------------------------------------------------------

def latest_session():
    """The session of the most recent recorded vote, e.g. "45-1"."""
    payload = http_get_json(f"{OP_BASE_URL}/votes/?format=json&limit=1", headers=OP_HEADERS)
    objects = payload.get("objects") or []
    if not objects:
        raise FetchError(f"{OP_BASE_URL}/votes/", "OpenParliament returned no votes")
    return objects[0]["session"]


def normalize_bill(value):
    """"c30", "C 30", "c-30" -> "C-30" (None if it isn't a bill number)."""
    match = re.fullmatch(r"\s*([CcSs])\s*-?\s*(\d+)\s*", value or "")
    return f"{match.group(1).upper()}-{int(match.group(2))}" if match else None


def bill_number(vote):
    """"C-30" from a vote's bill_url ("/bills/45-1/C-30/"), or ""."""
    parts = [p for p in (vote.get("bill_url") or "").split("/") if p]
    return parts[-1] if len(parts) == 3 else ""


def _cache_path(*name_parts):
    return os.path.join(get_output_dir("_cache", "openparliament"), "_".join(name_parts) + ".json")


def _ballots_complete(vote, ballots):
    """Do the ballots add up to the vote's official totals? (Only complete
    results are cached, in case OpenParliament is still loading a vote.)"""
    counts = Counter(b.get("ballot") for b in ballots)
    return (counts["Yes"], counts["No"], counts["Paired"]) == (
        vote.get("yea_total") or 0, vote.get("nay_total") or 0, vote.get("paired_total") or 0)


def fetch_ballots(vote):
    """Every MP's ballot on one vote, from the local cache when possible."""
    path = _cache_path("ballots", vote["session"], str(vote["number"]))
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass  # unreadable cache -> fetch again
    ballots = op_get_all("/votes/ballots/", vote=vote["url"])
    if _ballots_complete(vote, ballots):
        write_text_file(path, json.dumps(ballots))
    return ballots


def _party_name(party):
    name = (party or {}).get("short_name") or {}
    return name.get("en", "") if isinstance(name, dict) else str(name)


def fetch_membership_details(needed):
    """{membership url: {"party", "riding", "province"}} for every
    membership in `needed`: one paginated listing of all memberships,
    then individual requests for any it didn't include."""
    details = {}

    def remember(membership):
        riding = membership.get("riding") or {}
        details[membership.get("url")] = {
            "party": _party_name(membership.get("party")),
            "riding": (riding.get("name") or {}).get("en", ""),
            "province": riding.get("province", ""),
        }

    for membership in op_get_all("/politicians/memberships/"):
        remember(membership)
    failures = 0
    for url in sorted(set(needed) - set(details)):
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print("  ! OpenParliament seems to be unreachable -- skipping the remaining memberships.")
            break
        try:
            remember(http_get_json(f"{OP_BASE_URL}{url}?format=json", headers=OP_HEADERS))
            failures = 0
        except FetchError as e:
            failures += 1
            print(f"  ! couldn't look up {url} ({e})")
    return details


# --- Analysis -------------------------------------------------------------------

def _has_party_line(party):
    return bool(party) and party.lower() != "independent"


def party_positions(ballots, party_of):
    """{party: "Yes" / "No"} -- how most of each party's MPs voted on one
    vote (parties whose Yes and No votes were tied have no position)."""
    tallies = {}
    for ballot in ballots:
        party = party_of(ballot)
        if _has_party_line(party) and ballot.get("ballot") in ("Yes", "No"):
            tallies.setdefault(party, Counter())[ballot["ballot"]] += 1
    positions = {}
    for party, tally in tallies.items():
        if tally["Yes"] != tally["No"]:
            positions[party] = "Yes" if tally["Yes"] > tally["No"] else "No"
    return positions


def analyse(votes, ballots_by_vote, politicians, memberships):
    """Build the summary, vote and ballot rows (see the module docstring)."""
    current = {p.get("url"): p for p in politicians}

    def party_of(ballot):
        return (memberships.get(ballot.get("politician_membership_url")) or {}).get("party", "")

    people = {}
    vote_rows = []
    ballot_rows = []
    for vote in votes:
        ballots = ballots_by_vote.get(vote["url"], [])
        positions = party_positions(ballots, party_of)
        bill = bill_number(vote)
        vote_label = f"#{vote['number']} {bill}".strip()
        vote_rows.append({
            "number": vote["number"],
            "date": vote.get("date", ""),
            "bill": bill,
            "description": (vote.get("description") or {}).get("en", ""),
            "result": vote.get("result", ""),
            "yea_total": vote.get("yea_total", ""),
            "nay_total": vote.get("nay_total", ""),
            "paired_total": vote.get("paired_total", ""),
            "party_positions": "; ".join(f"{p}: {v}" for p, v in sorted(positions.items())),
            "url": f"{OP_SITE}{vote['url']}",
            "_label": vote_label,
        })
        for ballot in ballots:
            politician = ballot.get("politician_url", "")
            membership = memberships.get(ballot.get("politician_membership_url")) or {}
            party = membership.get("party", "")
            choice = ballot.get("ballot", "")
            position = positions.get(party, "") if _has_party_line(party) else ""
            against = choice in ("Yes", "No") and bool(position) and choice != position

            person = people.setdefault(politician, {
                "politician_url": politician, "counts": Counter(), "against_party": 0,
                "party_line_votes": 0, "ballots": {}, "membership": membership,
            })
            person["membership"] = membership or person["membership"]  # latest vote wins
            person["counts"][choice] += 1
            person["ballots"][vote_label] = choice
            if choice in ("Yes", "No") and position:
                person["party_line_votes"] += 1
                person["against_party"] += against
            ballot_rows.append({
                "session": vote["session"], "vote_number": vote["number"], "date": vote.get("date", ""),
                "bill": bill, "politician_url": politician, "party_at_vote": party,
                "riding": membership.get("riding", ""), "ballot": choice,
                "party_position": position, "against_party": against if position else "",
            })

    summary_rows = []
    for politician, person in people.items():
        info = current.get(politician) or {}
        membership = person["membership"]
        counts = person["counts"]
        held = sum(counts.values())
        cast = counts["Yes"] + counts["No"] + counts["Paired"]
        summary_rows.append({
            "politician_url": politician,
            "name": info.get("name") or _name_from_url(politician),
            "party": _party_name(info.get("current_party")) or membership.get("party", ""),
            "riding": ((info.get("current_riding") or {}).get("name") or {}).get("en") or membership.get("riding", ""),
            "province": (info.get("current_riding") or {}).get("province") or membership.get("province", ""),
            "current_mp": "yes" if info else "no",
            "votes": held,
            "yes": counts["Yes"],
            "no": counts["No"],
            "paired": counts["Paired"],
            "didnt_vote": counts["Didn't vote"],
            "participation_pct": round(100 * cast / held, 1) if held else "",
            "against_party": person["against_party"] if person["party_line_votes"] else "",
            "with_party_pct": round(100 * (1 - person["against_party"] / person["party_line_votes"]), 1)
            if person["party_line_votes"] else "",
            "openparliament_url": f"{OP_SITE}{politician}",
            "_ballots": person["ballots"],
        })
    summary_rows.sort(key=lambda r: r["name"])
    names = {r["politician_url"]: r["name"] for r in summary_rows}
    for row in ballot_rows:
        row["name"] = names.get(row["politician_url"], "")
    return summary_rows, vote_rows, ballot_rows


def _name_from_url(politician_url):
    """Fallback display name from a slug ("/politicians/jane-doe/" -> "Jane Doe")."""
    slug = [p for p in politician_url.split("/") if p][-1:] or [""]
    return " ".join(part.capitalize() for part in slug[0].split("-"))


def fetch_former_names(summary_rows):
    """Proper names for people no longer in OpenParliament's current-MP list."""
    for row in summary_rows:
        if row["current_mp"] == "no":
            try:
                detail = http_get_json(f"{OP_BASE_URL}{row['politician_url']}?format=json", headers=OP_HEADERS)
                row["name"] = detail.get("name") or row["name"]
            except FetchError:
                pass


def session_start(session, votes):
    """The session's first day: bill C-1 is introduced at the start of
    every session (falls back to the date of its first recorded vote)."""
    try:
        bills = op_get_all("/bills/", session=session, number="C-1")
        if bills and bills[0].get("introduced"):
            return bills[0]["introduced"]
    except FetchError:
        pass
    return min(v.get("date", "") for v in votes)


def add_activity(summary_rows, session, start, end=None):
    """Bills sponsored in the session and non-procedural speeches since
    its first day (House debates and committees counted separately), for
    current MPs. Stops early if OpenParliament stops responding."""
    targets = [r for r in summary_rows if r["current_mp"] == "yes"]
    print(f"Counting bills and speeches for {len(targets)} MP(s) -- this takes a while...")
    time_filter = {"time__gte": start}
    if end:
        time_filter["time__lt"] = end
    failures = 0
    for n, row in enumerate(targets, start=1):
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print("  ! OpenParliament seems to be unreachable -- skipping the remaining MPs.")
            break
        try:
            bills = op_get_all("/bills/", session=session, sponsor_politician=row["politician_url"])
            speeches = op_get_all("/speeches/", politician=row["politician_url"], procedural="False", **time_filter)
        except FetchError as e:
            failures += 1
            print(f"  ! skipping {row['name']} ({e})")
            continue
        failures = 0
        row["bills_sponsored"] = len(bills)
        row["house_speeches"] = sum(1 for s in speeches if (s.get("document_url") or "").startswith("/debates/"))
        row["committee_speeches"] = sum(1 for s in speeches if (s.get("document_url") or "").startswith("/committees/"))
        if n % 25 == 0:
            print(f"  ... {n}/{len(targets)}")


# --- Command line ---------------------------------------------------------------

def _bill_arg(value):
    bill = normalize_bill(value)
    if not bill:
        raise argparse.ArgumentTypeError("use a bill number like C-30 or S-201")
    return bill


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="How every MP voted, and per-MP voting statistics, from OpenParliament."
    )
    parser.add_argument("--session", metavar="SESSION", help="e.g. 44-1 (default: the current session)")
    parser.add_argument("--bill", metavar="BILL", type=_bill_arg, action="append",
                        help="only votes on this bill, e.g. C-30 (repeat for several)")
    parser.add_argument("--activity", action="store_true",
                        help="also count bills sponsored and speeches per MP (much slower)")
    add_common_args(parser)
    args = parser.parse_args(argv)
    if args.session and not re.fullmatch(r"\d{1,2}-\d", args.session):
        parser.error("--session looks like 45-1 (parliament-session)")
    return args


def main(argv=None):
    args = parse_args(argv)
    apply_common_args(args)

    print("=== Federal voting records (OpenParliament) ===")
    try:
        current_session = latest_session()
        session = args.session or current_session
        votes = sorted(op_get_all("/votes/", session=session), key=lambda v: v["number"])
        if args.bill:
            wanted = set(args.bill)
            votes = [v for v in votes if bill_number(v) in wanted]
            missing = sorted(wanted - {bill_number(v) for v in votes})
            if missing:
                print(f"No recorded votes in session {session} on: {', '.join(missing)}")
        if not votes:
            print(f"No votes to report for session {session}.")
            return 1
        print(f"Session {session}: {len(votes)} vote(s) to analyse.")

        politicians = fetch_politician_list()
        ballots_by_vote = {}
        for n, vote in enumerate(votes, start=1):
            ballots_by_vote[vote["url"]] = fetch_ballots(vote)
            if n % 25 == 0:
                print(f"  ... ballots for {n}/{len(votes)} vote(s)")
        needed = {b.get("politician_membership_url") for ballots in ballots_by_vote.values() for b in ballots}
        memberships = fetch_membership_details(needed - {None, ""})
    except FetchError as e:
        explain_fetch_error(e)
        return 1

    summary_rows, vote_rows, ballot_rows = analyse(votes, ballots_by_vote, politicians, memberships)
    fetch_former_names(summary_rows)
    names = {r["politician_url"]: r["name"] for r in summary_rows}
    for row in ballot_rows:
        row["name"] = names.get(row["politician_url"], row["name"])

    summary_columns = SUMMARY_COLUMNS
    if args.activity:
        # A past session's speeches end with its last vote (plus that day);
        # the current session is still open.
        end = None
        if session != current_session:
            last_vote_day = date.fromisoformat(max(v.get("date", "") for v in votes))
            end = (last_vote_day + timedelta(days=1)).isoformat()
        add_activity(summary_rows, session, session_start(session, votes), end)
    else:
        summary_columns = [c for c in SUMMARY_COLUMNS if c[0] not in ACTIVITY_FIELDS]

    suffix = session + ("_" + "_".join(sorted(args.bill)) if args.bill else "")
    out_dir = get_output_dir("federal")
    labels = [v["_label"] for v in vote_rows]
    for row in summary_rows:
        row.update(row["_ballots"])
    write_workbook(os.path.join(out_dir, f"votes_{suffix}.xlsx"), [
        {"title": "MP Summary", "fields": [c[0] for c in summary_columns],
         "headers": [c[1] for c in summary_columns], "rows": summary_rows},
        {"title": "Votes", "fields": [c[0] for c in VOTE_COLUMNS],
         "headers": [c[1] for c in VOTE_COLUMNS], "rows": vote_rows},
        {"title": "Ballots", "fields": ["name", "party", "riding"] + labels,
         "headers": ["Name", "Party", "Riding"] + labels, "rows": summary_rows, "freeze": "B2"},
    ])
    write_csv(os.path.join(out_dir, f"votes_{suffix}_summary.csv"), summary_rows, [c[0] for c in summary_columns])
    write_csv(os.path.join(out_dir, f"votes_{suffix}_ballots.csv"), ballot_rows, BALLOT_CSV_FIELDS)

    print(f"Done. {len(votes)} vote(s), {len(summary_rows)} MP(s) -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
