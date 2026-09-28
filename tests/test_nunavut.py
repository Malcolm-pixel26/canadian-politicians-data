"""Nunavut's MLAs: reading assembly.nu.ca, and the Legislative Assembly of
Nunavut alongside Represent's legislatures in scrape_provincial_territorial.py."""
import copy
import unittest

from support import (
    NUNAVUT_PAGES,
    REPRESENT,
    FakeNunavut,
    FakeRepresent,
    FakeWeb,
    ScraperTestCase,
    http_error,
    json_response,
    load_fixture,
    nunavut_member_page,
    sample_represent,
)

import lookup_politician
import nunavut
import represent_common as rc
import scrape_provincial_territorial

SAMPLE = load_fixture("represent_sample.json")
LISTING = load_fixture("nunavut_members.html")


def node(number):
    return f"https://www.assembly.nu.ca/node/{number}"


def parse(html, number=6490):
    return nunavut.parse_member_page(nunavut.parse_page(html), node(number))


class PageTests(unittest.TestCase):
    def test_a_member_page(self):
        row = parse(load_fixture("nunavut_member.html"), 6485)
        self.assertEqual((row["name"], row["first_name"], row["last_name"]), ("John Main", "John", "Main"))
        self.assertEqual((row["representative_set"], row["elected_office"]), ("Legislative Assembly of Nunavut", "MLA"))
        self.assertEqual((row["district_name"], row["province_territory"], row["party_name"]),
                         ("Arviat North-Whale Cove", "NU", ""))
        self.assertEqual(row["roles"], "Premier of Nunavut; Minister of Executive and Intergovernmental Affairs; "
                                       "Minister of Finance")
        self.assertEqual((row["phone"], row["email"]), ("1 867 975-5050", "jmain7@gov.nu.ca"))
        self.assertEqual((row["constituency_address"], row["constituency_phone"]),
                         ("P.O. Box 242, Arviat, NU X0C 0E0", "1 867 857-4201"))
        self.assertEqual(row["extra_info"], "constituency_email: johnmain@arviatnorthmla.ca; "
                                            "constituency_fax: 1 867 857-4205; constituency_office_2: "
                                            "General Delivery, Whale Cove, NU X0C 0J0, 1 867 896-9612, "
                                            "johnmain@whalecovemla.ca")
        self.assertEqual(row["photo_url"], "https://www.assembly.nu.ca/sites/default/files/styles/member_image/"
                                           "public/John-Main.jpg?itok=a1b2")
        self.assertEqual((row["url"], row["source_url"]), (node(6485), "https://www.assembly.nu.ca/members/mla"))
        self.assertEqual(set(row), set(rc.CSV_FIELDS))

    def test_a_page_with_each_section_on_one_line(self):
        row = parse(NUNAVUT_PAGES["6490"])
        self.assertEqual(row["district_name"], "Pangnirtung")
        self.assertEqual((row["phone"], row["email"]), ("1 867 975-5135", "jmike@assembly.nu.ca"))
        self.assertEqual((row["constituency_address"], row["constituency_phone"]),
                         ("P.O. Box 36 Pangnirtung, NU X0A 0R0", "1 867 473-8220"))
        # The biography after the office isn't read as another office.
        self.assertEqual(row["extra_info"], "constituency_email: JohnnyMike@pangnirtungmla.ca; "
                                            "constituency_fax: 1 867 473-8227")
        self.assertEqual(row["roles"], "")

    def test_a_page_without_a_legislative_office(self):
        row = parse(NUNAVUT_PAGES["2669"], 2669)
        self.assertEqual(row["name"], "David Joanasie")  # "Honourable" dropped
        self.assertEqual(row["roles"], "Speaker of the Legislative Assembly")
        self.assertEqual(row["phone"], "")
        self.assertEqual(row["constituency_phone"], "1 867 897-8753")
        # Only in a mailto: link reading "Email"; with no other address, it's the main one.
        self.assertEqual(row["email"], "davidjoanasie@southbaffinmla.ca")
        self.assertEqual(row["extra_info"], "")

    def test_pages_that_arent_an_mlas(self):
        pages = {
            "another page": NUNAVUT_PAGES["41"],
            "a vacant seat": nunavut_member_page("Vacant", "Hudson Bay", legislative="Phone: (867) 975-5000"),
            "no office or duties": nunavut_member_page("Jane Doe", "Hudson Bay"),
            "not a constituency": nunavut_member_page("Jane Doe", "Map", legislative="Phone: (867) 975-5000"),
        }
        for label, html in pages.items():
            with self.subTest(label):
                self.assertIsNone(parse(html))

    def test_office_details(self):
        self.assertEqual(nunavut.split_labels("Arviat, NU X0C 0E0 Phone: (867) 857-4201 E-mail: a@b.ca"),
                         ("Arviat, NU X0C 0E0", [("phone", "(867) 857-4201"), ("email", "a@b.ca")]))
        offices = nunavut.parse_offices([(1, "P.O. Box 1"), (1, "Iqaluit, NU X0A 0H0"), (1, "Phone: 867-975-5000"),
                                         (1, "Box 2"), (1, "Fax: 867-975-5001"), (2, "Room: 208")])
        self.assertEqual([(o["address"], o["phone"], o["fax"]) for o in offices],
                         [("P.O. Box 1, Iqaluit, NU X0A 0H0", "1 867 975-5000", ""),
                          ("Box 2", "", "1 867 975-5001"), ("Room: 208", "", "")])


class MembersPageTests(unittest.TestCase):
    def test_links_to_members_pages(self):
        listing = nunavut.parse_page(LISTING)
        # Only the listing's links -- not the menu, calendar or footer -- each once.
        self.assertEqual(nunavut.member_links(listing), [node(2669), node(6485), node(6490), node(9695)])
        self.assertEqual(nunavut.vacant_seats(listing), 1)

    def test_without_a_listing_every_link_to_a_page_is_tried(self):
        listing = nunavut.parse_page(LISTING.replace("views-", "x-"))
        self.assertEqual(nunavut.member_links(listing),
                         [node(n) for n in (41, 2669, 6485, 6490, 9695, 12345, 99)])


class ProvincialScrapeTests(ScraperTestCase):
    def run_provincial(self, site=None, represent=None):
        with FakeWeb() as web:
            (represent or sample_represent()).install(web)
            (site or FakeNunavut()).install(web)
            exit_code = scrape_provincial_territorial.main([])
        return exit_code, web

    def master(self):
        return {r["Name"]: r for r in self.read_sheet(self.master_path, "Provincial_Territorial")}

    def changelog(self):
        return self.read_sheet(self.master_path, "Changelog")

    def test_nunavut_is_saved_with_the_other_legislatures(self):
        exit_code, web = self.run_provincial()
        self.assertEqual(exit_code, 0)
        self.assertEqual(web.count("assembly.nu.ca"), 1 + 4)  # the members page + one per MLA
        main = self.master()["John Main"]
        self.assertEqual((main["Representative Set"], main["Province Territory"]),
                         ("Legislative Assembly of Nunavut", "NU"))
        self.assertEqual(main["Email"], "jmain7@gov.nu.ca")
        self.assertIn("Legislative Assembly of Nunavut: 4 MLA(s), 1 seat(s) vacant", self.output.getvalue())

    def test_other_pages_linked_from_the_members_page_are_skipped(self):
        others = {"12345": "<html><head><title>Sitting | Nunavut Legislative Assembly</title></head>"
                           "<body><h1>Sitting</h1><p>The House sits at 1:30 p.m.</p></body></html>",
                  "99": "<html><head><title>Contact Us</title></head><body><p>Phone: (867) 975-5000</p></body></html>"}
        exit_code, _ = self.run_provincial(FakeNunavut(pages=others, listing=LISTING.replace("views-", "x-")))
        self.assertEqual(exit_code, 0)
        self.assertEqual({r["Name"] for r in self.master().values() if r["Province Territory"] == "NU"},
                         {"David Joanasie", "John Main", "Johnny Mike", "Fred Pedersen"})

    def test_a_page_that_fails_to_download_marks_nobody_removed(self):
        self.run_provincial()
        exit_code, _ = self.run_provincial(FakeNunavut(pages={"6490": http_error(node(6490), 503)}))
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.master()["Johnny Mike"]["Status"], "active")
        self.assertIn("Couldn't download: Legislative Assembly of Nunavut", self.output.getvalue())

    def test_a_members_page_outage_keeps_the_rest(self):
        exit_code, _ = self.run_provincial(FakeNunavut(listing=http_error(nunavut.MEMBERS_URL, 404)))
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(self.master()), 2)  # Ontario, from Represent
        self.assertIn("Couldn't read Nunavut's MLAs", self.output.getvalue())

    def test_a_new_mla_for_the_same_seat_is_a_new_person(self):
        # Before the 2025 election, the Cambridge Bay page (node/9695) was Pamela Gross's.
        gross = nunavut_member_page(
            "Pamela Hakongak Gross", "Cambridge Bay", legislative="Phone: (867) 975-5127",
            offices=["P.O. Box 2450 Cambridge Bay, NU X0B 0C0 Phone: (867) 983-3777"])
        self.run_provincial(FakeNunavut(pages={"9695": gross}))
        self.run_provincial()
        master = self.master()
        self.assertEqual(master["Pamela Hakongak Gross"]["Status"], "removed")
        self.assertEqual(master["Fred Pedersen"]["Status"], "active")
        latest = [(c["Name"], c["Change Type"]) for c in self.changelog()
                  if c["Name"] in ("Pamela Hakongak Gross", "Fred Pedersen")]
        self.assertIn(("Pamela Hakongak Gross", "removed"), latest)
        self.assertIn(("Fred Pedersen", "added"), latest)
        self.assertNotIn("updated", {change for _, change in latest})

    def test_represents_own_nunavut_set_is_used_if_it_ever_has_one(self):
        nunavut_set = {"name": "Legislative Assembly of Nunavut", "url": "/representative-sets/nunavut-legislature/",
                       "data_url": "https://scrapers.herokuapp.com/represent/ca_nu/",
                       "related": {"representatives_url": "/representatives/nunavut-legislature/"}}
        mla = copy.deepcopy(next(r for r in SAMPLE["representatives"]
                                 if r["representative_set_name"] == "Legislative Assembly of Ontario"))
        mla.update(name="John Main", first_name="John", last_name="Main", district_name="Arviat North-Whale Cove",
                   elected_office="MLA", representative_set_name=nunavut_set["name"],
                   related={"representative_set_url": nunavut_set["url"]})
        represent = FakeRepresent(SAMPLE["sets"] + [nunavut_set], SAMPLE["representatives"] + [mla])
        exit_code, web = self.run_provincial(represent=represent)
        self.assertEqual(exit_code, 0)
        self.assertEqual(web.count("assembly.nu.ca"), 0)
        self.assertEqual(self.master()["John Main"]["Province Territory"], "NU")


class LookupTests(ScraperTestCase):
    def test_a_postal_code_in_nunavut_explains_the_missing_mla(self):
        payload = dict(load_fixture("represent_postcode.json"), province="NU", city="IQALUIT")
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/postcodes/", lambda url: json_response(payload))
            self.assertEqual(lookup_politician.main(["--postal-code", "X0A 0H0", "--no-enrich"]), 0)
        out = self.output.getvalue()
        self.assertIn("can't tell which MLA is yours", out)
        self.assertIn("nunavut-legislature.xlsx", out)

    def test_the_hint_is_left_out_when_it_doesnt_apply(self):
        payload = dict(load_fixture("represent_postcode.json"), province="NU")
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/postcodes/", lambda url: json_response(payload))
            lookup_politician.main(["--postal-code", "X0A 0H0", "--no-enrich", "--category", "federal"])
        self.assertNotIn("which MLA", self.output.getvalue())
