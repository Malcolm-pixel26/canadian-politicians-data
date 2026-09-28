import copy
import csv
import html
import os
from datetime import date

from support import FakeFederalSources, FakeRepresent, FakeWeb, ScraperTestCase, http_error, load_fixture

import lookup_politician
import scrape_careers as sc

SAMPLE = load_fixture("represent_sample.json")
POILIEVRE_PAGE = load_fixture("ourcommons_roles.html")
POILIEVRE_URL = "https://www.ourcommons.ca/Members/en/pierre-poilievre(25524)"


def roles_page(terms=(), parties=(), offices=(), committees=(), associations=(), elections=()):
    """A roles page shaped like fixtures/ourcommons_roles.html; sections
    with no rows are left out, as for an MP who never held an office."""
    sections = [
        ("Member of Parliament", ["Constituency", "Province / Territory", "Start Date", "End Date"], terms),
        ("Political Affiliation", ["Parliament", "Political Affiliation", "Start Date", "End Date"], parties),
        ("Offices and Roles as a Parliamentarian", ["Parliament", "Title", "Role", "Start Date", "End Date"], offices),
        ("Committees", ["Parliament ‑ Session", "Role", "Committee", "Start Date", "End Date"], committees),
        ("Parliamentary Associations and Interparliamentary Groups",
         ["Parliament", "Role", "Association or Group", "Start Date", "End Date"], associations),
        ("Election Candidate", ["Date", "Election Type", "Constituency", "Province / Territory", "Result"], elections),
    ]
    parts = []
    for title, headings, rows in sections:
        if not rows:
            continue
        head = "".join(f'<th scope="col">{h}</th>' for h in headings)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
        parts.append(f'<h2>{title}</h2><table class="table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
    return "<!DOCTYPE html><html><body><main>" + "".join(parts) + "</main></body></html>"


# A fictional MP who crossed the floor, so no real person's record is invented.
FLOOR_CROSSER_PAGE = roles_page(
    terms=[("Northern Riding", "Ontario", "Monday, April 28, 2025", "not applicable"),
           ("Northern Riding", "Ontario", "Monday, September 20, 2021", "Sunday, April 27, 2025")],
    parties=[("45th", "Liberal", "Wednesday, November 5, 2025", "not applicable"),
             ("45th", "Conservative", "Monday, April 28, 2025", "Tuesday, November 4, 2025"),
             ("44th", "Conservative", "Monday, September 20, 2021", "Sunday, April 27, 2025")],
    committees=[("45-1", "Member", "Finance", "Thursday, June 12, 2025", "not applicable"),
                ("45-1", "Chair", "Health", "Thursday, June 12, 2025", "not applicable"),
                ("44-1", "Member", "Finance", "Thursday, December 2, 2021", "Sunday, April 27, 2025")],
    elections=[("Monday, April 28, 2025", "General Election", "Northern Riding", "Ontario", "Re-Elected"),
               ("Monday, September 20, 2021", "General Election", "Northern Riding", "Ontario", "Elected")],
)


def mp(name, first, last, riding, party, url, district_code):
    """A House of Commons representative shaped like the Represent fixture."""
    rep = copy.deepcopy(next(r for r in SAMPLE["representatives"] if r["name"] == "Parm Bains"))
    rep.update(name=name, first_name=first, last_name=last, district_name=riding, party_name=party, url=url,
               email="", personal_url="", photo_url="", offices=[])
    rep["related"]["boundary_url"] = f"/boundaries/federal-electoral-districts-2023-representation-order/{district_code}/"
    return rep


MPS = [
    mp("Pierre Poilievre", "Pierre", "Poilievre", "Battle River—Crowfoot", "Conservative", POILIEVRE_URL, "48002"),
    mp("Alex Tremblay", "Alex", "Tremblay", "Northern Riding", "Liberal",
       "https://www.ourcommons.ca/Members/en/alex-tremblay(900001)", "35001"),
    mp("Sam Rivers", "Sam", "Rivers", "Western Riding", "NDP",
       "https://www.ourcommons.ca/Members/en/sam-rivers(900002)", "59001"),  # no roles page
]
HOUSE_OF_COMMONS = [s for s in SAMPLE["sets"] if s["name"] == "House of Commons"]


class ParsingTests(ScraperTestCase):
    def test_roles_page(self):
        career = sc.parse_roles_page(POILIEVRE_PAGE)
        self.assertEqual({section: len(entries) for section, entries in career.items()},
                         {"terms": 8, "parties": 8, "offices": 9, "committees": 12, "associations": 9, "elections": 9})
        self.assertEqual(career["terms"][0], {"constituency": "Battle River—Crowfoot", "province": "Alberta",
                                              "start": "2025-08-18", "end": ""})
        self.assertEqual(career["parties"][-1], {"parliament": 38, "party": "Conservative",
                                                 "start": "2004-06-28", "end": "2006-01-22"})
        self.assertEqual(career["committees"][1], {"session": "43-2", "role": "Vice-Chair",
                                                   "committee": "Industry, Science and Technology",
                                                   "start": "2021-02-18", "end": "2021-08-15"})
        self.assertEqual(career["elections"][1], {"date": "2025-04-28", "election_type": "General Election",
                                                  "constituency": "Carleton", "province": "Ontario",
                                                  "result": "Defeated"})

    def test_summary_of_a_long_career(self):
        summary = sc.summarize(sc.parse_roles_page(POILIEVRE_PAGE), today=date(2026, 9, 28))
        # Terms follow each other without gaps, except while he had no seat in 2025.
        days = ((date(2025, 4, 27) - date(2004, 6, 28)).days + 1) + ((date(2026, 9, 28) - date(2025, 8, 18)).days + 1)
        self.assertEqual(summary["years_as_mp"], round(days / 365.25, 1))
        self.assertEqual(summary, {
            "first_elected": "2004-06-28",
            "years_as_mp": 21.9,
            "parliaments": 8,
            "ridings": "Nepean—Carleton; Carleton; Battle River—Crowfoot",
            "elections_won": 8,
            "elections_lost": 1,
            "party_history": "Conservative",
            "party_changes": 0,
            "current_offices": "Leader of the Conservative Party of Canada; Leader of the Opposition",
            # Oldest first; an office he holds again isn't repeated here.
            "past_offices": "Parliamentary Secretary to the President of the Treasury Board; "
                            "Parliamentary Secretary to the Prime Minister and to the Minister of Intergovernmental "
                            "Affairs; Parliamentary Secretary to the Minister of Transport, Infrastructure and "
                            "Communities and for the Federal Economic Development Agency for Southern Ontario; "
                            "Minister of State (Democratic Reform); Minister of Employment and Social Development; "
                            "Minister for Democratic Reform",
            "minister_posts": 3,
            "parliamentary_secretary_posts": 3,
            "current_committees": "",
            "committees_served": 9,
            "associations": 7,
        })

    def test_floor_crossing_and_current_committees(self):
        summary = sc.summarize(sc.parse_roles_page(FLOOR_CROSSER_PAGE), today=date(2026, 9, 28))
        self.assertEqual((summary["party_history"], summary["party_changes"]), ("Conservative → Liberal", 1))
        self.assertEqual(summary["current_committees"], "Finance; Health (Chair)")
        self.assertEqual((summary["committees_served"], summary["associations"]), (2, 0))
        self.assertEqual((summary["current_offices"], summary["past_offices"], summary["minister_posts"]), ("", "", 0))
        self.assertEqual((summary["first_elected"], summary["elections_won"], summary["elections_lost"]),
                         ("2021-09-20", 2, 0))

    def test_dates(self):
        cases = {"Monday, August 18, 2025": "2025-08-18", "Tuesday, February 7, 2006": "2006-02-07",
                 "not applicable": "", "Not Applicable": "", "": "", "sometime in 2019": "sometime in 2019",
                 "Friday, February 30, 2026": "Friday, February 30, 2026"}
        for text, expected in cases.items():
            self.assertEqual(sc.parse_long_date(text), expected, text)

    def test_a_page_without_the_mp_table_is_rejected(self):
        for page in ("<html><body><p>Page not found</p></body></html>",
                     roles_page(elections=[("Monday, April 28, 2025", "General Election", "X", "Ontario", "Defeated")])):
            with self.assertRaises(ValueError):
                sc.parse_roles_page(page)

    def test_career_lines(self):
        lines = sc.career_lines(sc.summarize(sc.parse_roles_page(POILIEVRE_PAGE), today=date(2026, 9, 28)))
        self.assertEqual(lines[0], "MP since 2004-06-28 (21.9 years over 8 Parliament(s)): "
                                   "Nepean—Carleton; Carleton; Battle River—Crowfoot")
        self.assertIn("Elections: 8 won, 1 lost", lines)
        self.assertIn("Now: Leader of the Conservative Party of Canada; Leader of the Opposition", lines)
        offices = lines[lines.index("Past offices: Parliamentary Secretary to the President of the Treasury Board"):-1]
        self.assertEqual(len(offices), 6)  # one per line, aligned
        self.assertEqual(offices[-1], " " * len("Past offices: ") + "Minister for Democratic Reform")
        self.assertEqual(lines[-1], "Committees: 9 over their career")

    def test_a_long_list_of_offices_is_cut_to_the_latest(self):
        summary = dict(sc.summarize(sc.parse_roles_page(POILIEVRE_PAGE), today=date(2026, 9, 28)),
                       past_offices="; ".join(f"Office {n}" for n in range(1, 13)))
        lines = sc.career_lines(summary)
        label = "Past offices (8 most recent of 12): "
        self.assertIn(label + "Office 5", lines)
        self.assertIn(" " * len(label) + "Office 12", lines)
        self.assertNotIn(" " * len(label) + "Office 4", lines)


class RunTests(ScraperTestCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb()
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)
        FakeRepresent(HOUSE_OF_COMMONS, MPS).install(self.web)
        FakeFederalSources(roles={"25524": POILIEVRE_PAGE, "900001": FLOOR_CROSSER_PAGE}).install(self.web)

    def federal_file(self, name):
        return os.path.join(self.tmp, "federal", name)

    def test_every_mp(self):
        self.assertEqual(sc.main([]), 1)  # one MP's page is missing
        path = self.federal_file("mp_careers.xlsx")
        self.assertEqual(self.sheet_names(path),
                         ["Summary", "Terms", "Parties", "Offices", "Committees", "Associations", "Elections"])
        summary = {r["Name"]: r for r in self.read_sheet(path, "Summary")}
        self.assertEqual(set(summary), {"Pierre Poilievre", "Alex Tremblay"})
        self.assertEqual(summary["Alex Tremblay"]["Party history"], "Conservative → Liberal")
        self.assertEqual(summary["Pierre Poilievre"]["Province"], "AB")
        self.assertEqual(summary["Pierre Poilievre"]["Minister posts"], 3)
        self.assertEqual(summary["Pierre Poilievre"]["Source"], f"{POILIEVRE_URL}/roles")
        offices = self.read_sheet(path, "Offices")
        self.assertEqual(len(offices), 9)
        self.assertEqual(offices[0], {"Name": "Pierre Poilievre", "Parliament": 45, "Title": "Leader of the Opposition",
                                      "Role": "Leader of the Official Opposition", "Start": "2025-08-18", "End": None})
        self.assertEqual(len(self.read_sheet(path, "Committees")), 12 + 3)
        with open(self.federal_file("mp_careers_summary.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["name"] for r in rows], ["Alex Tremblay", "Pierre Poilievre"])  # sorted by name
        self.assertIn("Couldn't read 1 MP(s): Sam Rivers", self.output.getvalue())

    def test_one_mp(self):
        self.assertEqual(sc.main(["--mp", "poilievre"]), 0)
        self.assertEqual(self.web.count("/roles"), 1)
        self.assertTrue(os.path.exists(self.federal_file("mp_careers_poilievre.xlsx")))
        self.assertFalse(os.path.exists(self.federal_file("mp_careers.xlsx")))  # whole-House file untouched
        self.assertIn("MP since 2004-06-28", self.output.getvalue())

    def test_no_matching_mp(self):
        self.assertEqual(sc.main(["--mp", "nobody"]), 1)
        self.assertIn("No current MP's name matches nobody.", self.output.getvalue())

    def test_unreachable_site(self):
        more = [mp(f"Test Member {n}", "Test", f"Member {n}", f"Riding {n}", "Liberal",
                   f"https://www.ourcommons.ca/Members/en/test-member-{n}(90001{n})", "35002") for n in range(4)]
        FakeRepresent(HOUSE_OF_COMMONS, MPS + more).install(self.web)
        self.web.route(lambda url: url.endswith("/roles"), lambda url: http_error(url, 500))
        self.assertEqual(sc.main([]), 1)
        # Three pages in a row fail, so the other four aren't tried.
        self.assertEqual(len({url for _, url in self.web.requests if url.endswith("/roles")}), 3)
        self.assertIn("ourcommons.ca seems to be unreachable -- skipping the remaining 4 MP(s).", self.output.getvalue())
        self.assertIn("No career history could be read, so nothing was written.", self.output.getvalue())
        self.assertFalse(os.path.exists(self.federal_file("mp_careers.xlsx")))

    def test_lookup_shows_the_career(self):
        self.assertEqual(lookup_politician.main(["poilievre", "--live"]), 0)
        out = self.output.getvalue()
        self.assertIn("Career: MP since 2004-06-28", out)
        self.assertIn("Past offices: Parliamentary Secretary to the President of the Treasury Board", out)

    def test_lookup_without_a_roles_page(self):
        self.assertEqual(lookup_politician.main(["sam rivers", "--live"]), 0)
        self.assertIn("Career: unavailable (Failed to fetch", self.output.getvalue())
