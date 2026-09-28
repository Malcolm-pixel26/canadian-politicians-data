"""
scrape_votes.py against a fake OpenParliament. Response shapes follow the
live API (/votes/, /votes/ballots/, /politicians/memberships/, /bills/,
/speeches/); the MPs, bills and ballots are fictional, so no real
person's voting record is invented here.
"""
import csv
import os
import urllib.parse

from support import OPENPARLIAMENT, FakeWeb, ScraperTestCase, http_error, json_response

import scrape_votes

SESSION = "45-1"


def membership(n, slug, party, riding, province):
    return {"url": f"/politicians/memberships/{n}/", "politician_url": f"/politicians/{slug}/",
            "party": {"name": {"en": party}, "short_name": {"en": party}} if party else None,
            "label": {"en": f"{party} MP for {riding}"}, "riding": {"name": {"en": riding}, "province": province}}


MEMBERSHIPS = [
    membership(1, "alex-martin", "Liberal", "Riding A", "ON"),
    membership(2, "jordan-lee", "Liberal", "Riding B", "BC"),
    membership(3, "casey-roy", "Conservative", "Riding C", "AB"),
    membership(4, "morgan-singh", "Conservative", "Riding D", "ON"),  # crossed the floor after vote 1...
    membership(5, "morgan-singh", "Liberal", "Riding D", "ON"),       # ...and sits as a Liberal from vote 2
    membership(6, "riley-gagnon", "Bloc", "Riding E", "QC"),
    membership(7, "taylor-brown", "Independent", "Riding F", "NS"),
    membership(8, "drew-former", "Conservative", "Riding G", "SK"),   # resigned after vote 1
]

CURRENT_MPS = [
    {"name": "Alex Martin", "url": "/politicians/alex-martin/", "current_party": {"short_name": {"en": "Liberal"}},
     "current_riding": {"province": "ON", "name": {"en": "Riding A"}}},
    {"name": "Jordan Lee", "url": "/politicians/jordan-lee/", "current_party": {"short_name": {"en": "Liberal"}},
     "current_riding": {"province": "BC", "name": {"en": "Riding B"}}},
    {"name": "Casey Roy", "url": "/politicians/casey-roy/", "current_party": {"short_name": {"en": "Conservative"}},
     "current_riding": {"province": "AB", "name": {"en": "Riding C"}}},
    {"name": "Morgan Singh", "url": "/politicians/morgan-singh/", "current_party": {"short_name": {"en": "Liberal"}},
     "current_riding": {"province": "ON", "name": {"en": "Riding D"}}},
    {"name": "Riley Gagnon", "url": "/politicians/riley-gagnon/", "current_party": {"short_name": {"en": "Bloc"}},
     "current_riding": {"province": "QC", "name": {"en": "Riding E"}}},
    {"name": "Taylor Brown", "url": "/politicians/taylor-brown/", "current_party": None,
     "current_riding": {"province": "NS", "name": {"en": "Riding F"}}},
]


def vote(number, date, bill, yea, nay, paired):
    return {"bill_url": f"/bills/{SESSION}/{bill}/" if bill else None, "session": SESSION, "number": number,
            "date": date, "description": {"en": f"Test vote {number}"}, "result": "Passed" if yea > nay else "Failed",
            "yea_total": yea, "nay_total": nay, "paired_total": paired, "url": f"/votes/{SESSION}/{number}/"}


VOTES = [vote(1, "2026-06-17", None, 3, 4, 0), vote(2, "2026-06-18", "C-98", 2, 2, 1), vote(3, "2026-09-23", "C-99", 5, 1, 0)]

# vote number -> [(slug, membership number, ballot)]
BALLOTS = {
    1: [("alex-martin", 1, "Yes"), ("jordan-lee", 2, "Yes"), ("casey-roy", 3, "No"), ("morgan-singh", 4, "No"),
        ("riley-gagnon", 6, "No"), ("taylor-brown", 7, "Yes"), ("drew-former", 8, "No")],
    2: [("alex-martin", 1, "Yes"), ("jordan-lee", 2, "No"), ("casey-roy", 3, "No"), ("morgan-singh", 5, "Yes"),
        ("riley-gagnon", 6, "Paired"), ("taylor-brown", 7, "Didn't vote")],
    3: [("alex-martin", 1, "Yes"), ("jordan-lee", 2, "Yes"), ("casey-roy", 3, "Yes"), ("morgan-singh", 5, "Yes"),
        ("riley-gagnon", 6, "No"), ("taylor-brown", 7, "Yes")],
}


def ballots_for(number):
    return [{"vote_url": f"/votes/{SESSION}/{number}/", "politician_url": f"/politicians/{slug}/",
             "politician_membership_url": f"/politicians/memberships/{m}/", "ballot": choice}
            for slug, m, choice in BALLOTS[number]]


def speech(slug, when, document):
    return {"time": when, "politician_url": f"/politicians/{slug}/", "procedural": False, "document_url": document}


SPEECHES = {
    "alex-martin": [speech("alex-martin", "2026-02-02 14:00:00", "/debates/2026/2/2/"),
                    speech("alex-martin", "2026-02-03 14:00:00", "/debates/2026/2/3/"),
                    speech("alex-martin", "2026-03-01 11:00:00", "/committees/finance/45-1/12/")],
}


class FakeOpenParliamentVotes:
    """Serves the scenario above with OpenParliament-style pagination
    (pages capped at `max_page` objects)."""

    def __init__(self, max_page=500):
        self.max_page = max_page
        self.queries = []

    def page(self, objects, url, query):
        limit = min(int(query.get("limit", 20)), self.max_page)
        offset = int(query.get("offset", 0))
        more = offset + limit < len(objects)
        next_query = dict(query, limit=limit, offset=offset + limit)
        next_url = f"{urllib.parse.urlsplit(url).path}?{urllib.parse.urlencode(next_query)}" if more else None
        return json_response({"objects": objects[offset:offset + limit],
                              "pagination": {"offset": offset, "limit": limit, "next_url": next_url}})

    def handle(self, url):
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        self.queries.append((path, query))
        if path == "/votes/":
            newest_first = sorted(VOTES, key=lambda v: -v["number"])
            return self.page(newest_first if "session" not in query or query["session"] == SESSION else [], url, query)
        if path == "/votes/ballots/":
            number = int(query["vote"].strip("/").split("/")[-1])
            return self.page(ballots_for(number), url, query)
        if path == "/politicians/":
            return self.page(CURRENT_MPS, url, query)
        if path == "/politicians/memberships/":
            return self.page(MEMBERSHIPS, url, query)
        if path == "/politicians/drew-former/":
            return json_response({"name": "Drew Former", "url": path})
        if path == "/bills/":
            if query.get("number") == "C-1":
                return self.page([{"session": SESSION, "number": "C-1", "introduced": "2025-05-27"}], url, query)
            sponsored = [{"number": "C-99"}] if query.get("sponsor_politician") == "/politicians/alex-martin/" else []
            return self.page(sponsored, url, query)
        if path == "/speeches/":
            slug = query["politician"].strip("/").split("/")[-1]
            found = [s for s in SPEECHES.get(slug, []) if s["time"] >= query["time__gte"]]
            return self.page(found, url, query)
        return http_error(url, 404)

    def install(self, web):
        web.route(OPENPARLIAMENT, self.handle)
        return self


class VotesTests(ScraperTestCase):
    def run_votes(self, *argv, fake=None):
        fake = fake or FakeOpenParliamentVotes()
        with FakeWeb() as web:
            fake.install(web)
            exit_code = scrape_votes.main(list(argv))
        return exit_code, web, fake

    def summary(self, name="votes_45-1_summary.csv"):
        with open(os.path.join(self.tmp, "federal", name), encoding="utf-8-sig", newline="") as f:
            return {r["name"]: r for r in csv.DictReader(f)}

    def test_whole_session(self):
        exit_code, _, _ = self.run_votes()
        self.assertEqual(exit_code, 0)
        rows = self.summary()
        self.assertEqual(set(rows), {"Alex Martin", "Jordan Lee", "Casey Roy", "Morgan Singh", "Riley Gagnon",
                                     "Taylor Brown", "Drew Former"})
        alex = rows["Alex Martin"]
        self.assertEqual((alex["votes"], alex["yes"], alex["participation_pct"], alex["with_party_pct"]),
                         ("3", "3", "100.0", "100.0"))
        # Voted No while most Liberals voted Yes on vote 2.
        self.assertEqual((rows["Jordan Lee"]["against_party"], rows["Jordan Lee"]["with_party_pct"]), ("1", "66.7"))
        # Crossed the floor: vote 1 counts against the Conservative line, votes 2-3 the Liberal one.
        morgan = rows["Morgan Singh"]
        self.assertEqual((morgan["party"], morgan["against_party"], morgan["with_party_pct"]), ("Liberal", "0", "100.0"))
        # Paired counts as taking part; an independent has no party line.
        self.assertEqual((rows["Riley Gagnon"]["paired"], rows["Riley Gagnon"]["participation_pct"]), ("1", "100.0"))
        taylor = rows["Taylor Brown"]
        self.assertEqual((taylor["party"], taylor["didnt_vote"], taylor["participation_pct"], taylor["against_party"]),
                         ("Independent", "1", "66.7", ""))
        drew = rows["Drew Former"]
        self.assertEqual((drew["current_mp"], drew["votes"], drew["party"], drew["riding"]),
                         ("no", "1", "Conservative", "Riding G"))
        self.assertNotIn("bills_sponsored", alex)  # only with --activity

    def test_workbook_and_ballot_csv(self):
        self.run_votes()
        path = os.path.join(self.tmp, "federal", "votes_45-1.xlsx")
        self.assertEqual(self.sheet_names(path), ["MP Summary", "Votes", "Ballots"])
        grid = {r["Name"]: r for r in self.read_sheet(path, "Ballots")}
        self.assertEqual((grid["Jordan Lee"]["#1"], grid["Jordan Lee"]["#2 C-98"], grid["Jordan Lee"]["#3 C-99"]),
                         ("Yes", "No", "Yes"))
        self.assertIsNone(grid["Drew Former"]["#3 C-99"])  # not an MP any more
        votes = {r["Vote #"]: r for r in self.read_sheet(path, "Votes")}
        self.assertEqual(votes[2]["How the parties voted"], "Conservative: No; Liberal: Yes")
        self.assertEqual(votes[3]["Bill"], "C-99")
        with open(os.path.join(self.tmp, "federal", "votes_45-1_ballots.csv"), encoding="utf-8-sig", newline="") as f:
            ballots = list(csv.DictReader(f))
        self.assertEqual(len(ballots), sum(len(b) for b in BALLOTS.values()))
        morgan_vote1 = next(b for b in ballots if b["name"] == "Morgan Singh" and b["vote_number"] == "1")
        self.assertEqual((morgan_vote1["party_at_vote"], morgan_vote1["against_party"]), ("Conservative", "False"))

    def test_bill_filter(self):
        exit_code, _, _ = self.run_votes("--bill", "c99")
        self.assertEqual(exit_code, 0)
        rows = self.summary("votes_45-1_C-99_summary.csv")
        self.assertEqual(rows["Alex Martin"]["votes"], "1")
        self.assertNotIn("Drew Former", rows)

    def test_unknown_bill(self):
        exit_code, _, _ = self.run_votes("--bill", "C-1234")
        self.assertEqual(exit_code, 1)
        self.assertIn("No recorded votes in session 45-1 on: C-1234", self.output.getvalue())

    def test_ballots_are_cached(self):
        self.run_votes()
        _, _, fake = self.run_votes()
        self.assertEqual([q for q in fake.queries if q[0] == "/votes/ballots/"], [])

    def test_incomplete_ballots_are_not_cached(self):
        original = VOTES[0]["yea_total"]
        VOTES[0]["yea_total"] = original + 1  # OpenParliament still loading this vote
        try:
            self.run_votes()
            _, _, fake = self.run_votes()
        finally:
            VOTES[0]["yea_total"] = original
        fetched = [q[1]["vote"] for q in fake.queries if q[0] == "/votes/ballots/"]
        self.assertEqual(fetched, ["/votes/45-1/1/"])

    def test_pagination(self):
        exit_code, _, fake = self.run_votes(fake=FakeOpenParliamentVotes(max_page=2))
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(self.summary()), 7)
        self.assertGreater(len([q for q in fake.queries if q[0] == "/votes/ballots/"]), 3)

    def test_activity(self):
        exit_code, _, fake = self.run_votes("--activity")
        self.assertEqual(exit_code, 0)
        alex = self.summary()["Alex Martin"]
        self.assertEqual((alex["bills_sponsored"], alex["house_speeches"], alex["committee_speeches"]), ("1", "2", "1"))
        self.assertEqual(self.summary()["Drew Former"]["bills_sponsored"], "")  # former MPs are skipped
        speech_queries = [q[1] for q in fake.queries if q[0] == "/speeches/"]
        self.assertEqual({q["time__gte"] for q in speech_queries}, {"2025-05-27"})  # the session's first day
        self.assertEqual({q["procedural"] for q in speech_queries}, {"False"})

    def test_unreachable(self):
        with FakeWeb() as web:
            web.route(OPENPARLIAMENT, lambda url: OSError(101, "Network is unreachable"))
            self.assertEqual(scrape_votes.main([]), 1)
        self.assertIn("Couldn't reach api.openparliament.ca", self.output.getvalue())

    def test_bill_numbers(self):
        self.assertEqual([scrape_votes.normalize_bill(v) for v in ("c30", "C-30", "c 030", "S-201", "X-1", "C-")],
                         ["C-30", "C-30", "C-30", "S-201", None, None])
