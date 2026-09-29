"""The Senate: reading sencanada.ca's List and Contact Information tables,
and senators alongside the MPs in scrape_federal.py, master.xlsx, the
budget tabs and the lookups."""
import csv
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from support import (
    REPRESENT,
    FakeSenate,
    FakeWeb,
    ScraperTestCase,
    http_error,
    json_response,
    load_fixture,
    sample_represent,
    senate_list_without,
)

import budget_common
import lookup_politician
from changes_digest import TIMESTAMP_FORMAT
import represent_common as rc
import scrape_federal
import senate

LIST = load_fixture("senate_list.html")
CONTACTS = load_fixture("senate_contacts.html")
POSTCODE = load_fixture("represent_postcode.json")


def senators(contacts=CONTACTS):
    found = senate.parse_contacts(contacts) if contacts is not None else None
    return {row["name"]: row for row in (senate.senator_row(e, found) for e in senate.parse_list(LIST))}


class ParsingTests(unittest.TestCase):
    def test_the_list(self):
        rows = senators()
        self.assertEqual(len(rows), 8)
        adler = rows["Charles S. Adler"]
        self.assertEqual((adler["first_name"], adler["last_name"]), ("Charles S.", "Adler"))
        self.assertEqual((adler["representative_set"], adler["elected_office"]), ("Senate of Canada", "Senator"))
        self.assertEqual(adler["party_name"], "Canadian Senators Group")
        self.assertEqual((adler["district_name"], adler["province_territory"]), ("Manitoba", "MB"))
        self.assertEqual(adler["url"], "https://sencanada.ca/en/senators/adler-charles/")
        self.assertEqual(adler["postal_address"], "Senate of Canada, Ottawa ON K1A 0A4")
        self.assertEqual(adler["extra_info"], "appointed: 2024-08-16; retirement: 2029-08-25; "
                                              "appointed_on_advice_of: Trudeau, Justin (Lib.)")
        # ", P.C." isn't part of the name, and a footnote mark isn't part of the date.
        self.assertEqual(rows["Claude Carignan"]["first_name"], "Claude")
        self.assertIn("appointed: 2011-05-25;", rows["Fabian Manning"]["extra_info"])
        self.assertEqual(rows["Fabian Manning"]["url"], "https://sencanada.ca/en/senators/manning-fabian/")
        self.assertEqual(rows["Patrick Brazeau"]["party_name"], "Non-affiliated")
        self.assertEqual(rows["Nancy Karetak-Lindell"]["province_territory"], "NU")
        self.assertIn("Flordeliz (Gigi) Osler", rows)

    def test_contact_details_are_matched_by_link_or_by_name(self):
        rows = senators()
        adler = rows["Charles S. Adler"]
        self.assertEqual((adler["phone"], adler["email"]), ("1 613 943-4619", "charles.adler@sen.parl.gc.ca"))
        # No link, no "P.C.", and the address only in a link reading "Email".
        self.assertEqual(rows["Claude Carignan"]["email"], "claude.carignan@sen.parl.gc.ca")
        # "Surette, Allister W." in the contact table.
        self.assertEqual(rows["Allister Surette"]["phone"], "1 343 991-4120")
        # Appointed after the contact table was last updated.
        martel = rows["Richard Martel"]
        self.assertEqual((martel["phone"], martel["email"]), ("", ""))
        # Retired: still in the contact table, but not a senator any more.
        self.assertNotIn("Stan Kutcher", rows)

    def test_without_the_contact_table_the_contact_fields_are_left_out(self):
        adler = senators(contacts=None)["Charles S. Adler"]
        self.assertNotIn("email", adler)
        self.assertNotIn("phone", adler)
        self.assertEqual(adler["party_name"], "Canadian Senators Group")

    def test_a_missing_table_is_an_error_not_an_empty_senate(self):
        for page in ("<p>Down for maintenance</p>", "<table><tr><th>Name</th><th>Affiliation</th></tr></table>"):
            with self.subTest(page=page), self.assertRaises(ValueError):
                senate.parse_list(page)
        with self.assertRaises(ValueError):
            senate.parse_contacts("<table><tr><th>Hon. Senator</th><th>Phone</th></tr></table>")

    def test_senators_are_not_mps(self):
        self.assertFalse(any(rc.in_house_of_commons(r) for r in senators().values()))
        self.assertTrue(rc.in_house_of_commons({"representative_set": "House of Commons"}))


class FederalScrapeTests(ScraperTestCase):
    def run_federal(self, senate_site=None):
        with FakeWeb() as web:
            sample_represent().install(web)
            (senate_site or FakeSenate()).install(web)
            exit_code = scrape_federal.main(["--no-enrich"])
        return exit_code, web

    def master(self):
        return {r["Name"]: r for r in self.read_sheet(self.master_path, "Federal")}

    def changelog(self):
        return self.read_sheet(self.master_path, "Changelog")

    def test_senators_are_saved_next_to_the_mps(self):
        exit_code, web = self.run_federal()
        self.assertEqual(exit_code, 0)
        self.assertEqual(web.count("sencanada.ca"), 2)
        with open(os.path.join(self.tmp, "federal", "senators.csv"), encoding="utf-8-sig", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 8)
        adler = self.master()["Charles S. Adler"]
        self.assertEqual(adler["Representative Set"], "Senate of Canada")
        self.assertEqual(adler["Email"], "charles.adler@sen.parl.gc.ca")
        self.assertEqual(adler["Status"], "active")
        self.assertIn("6 MP(s) and 8 senator(s)", self.output.getvalue())

    def test_a_senate_outage_keeps_the_mps_and_the_senators(self):
        self.run_federal()
        exit_code, _ = self.run_federal(FakeSenate(senators=http_error(senate.LIST_URL, 503)))
        self.assertEqual(exit_code, 1)
        master = self.master()
        self.assertEqual(len(master), 6 + 8)
        self.assertEqual({r["Status"] for r in master.values()}, {"active"})
        self.assertIn("Couldn't download the Senate", self.output.getvalue())

    def test_a_contact_table_outage_keeps_the_saved_contact_details(self):
        self.run_federal()
        exit_code, _ = self.run_federal(FakeSenate(contacts=http_error(senate.CONTACTS_URL, 404)))
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.master()["Charles S. Adler"]["Email"], "charles.adler@sen.parl.gc.ca")
        self.assertEqual([c for c in self.changelog() if c["Change Type"] != "added"], [])
        self.assertIn("Couldn't read the senators' phone numbers and emails", self.output.getvalue())

    def test_a_senator_who_leaves_is_marked_removed(self):
        self.run_federal()
        self.assertEqual(self.run_federal(FakeSenate(senators=senate_list_without("Brazeau, Patrick")))[0], 0)
        self.assertEqual(self.master()["Patrick Brazeau"]["Status"], "removed")
        removed = [c["Name"] for c in self.changelog() if c["Change Type"] == "removed"]
        self.assertEqual(removed, ["Patrick Brazeau"])

    def test_budgets_leave_senators_out(self):
        self.run_federal()
        names = {row["name"] for row in budget_common.read_master_mps()}
        self.assertEqual(len(names), 6)
        self.assertNotIn("Charles S. Adler", names)


class LookupTests(ScraperTestCase):
    def scrape(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeSenate().install(web)
            scrape_federal.main(["--no-enrich"])
        self.output.truncate(0)
        self.output.seek(0)

    def test_cached_lookup_finds_a_senator_without_house_of_commons_requests(self):
        self.scrape()
        with FakeWeb() as web:
            self.assertEqual(lookup_politician.main(["adler"]), 0)
            self.assertEqual(web.requests, [])
        self.assertIn("Senate of Canada", self.output.getvalue())

    def test_live_lookup_finds_senators(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeSenate().install(web)
            self.assertEqual(lookup_politician.main(["osler", "--live", "--place", "manitoba"]), 0)
            self.assertEqual(web.count("openparliament"), 0)
        self.assertIn("Flordeliz (Gigi) Osler", self.output.getvalue())

    def test_live_lookup_skips_the_senate_when_the_place_rules_it_out(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeSenate().install(web)
            lookup_politician.main(["mantas", "--live", "--place", "toronto"])
            self.assertEqual(web.count("sencanada.ca"), 0)

    def postal_code(self, *argv, province):
        self.output.truncate(0)
        self.output.seek(0)
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/postcodes/", lambda url: json_response(dict(POSTCODE, province=province)))
            exit_code = lookup_politician.main(["--postal-code", "L5G4L3", "--no-enrich", *argv])
            self.assertEqual(len(web.requests), 1)  # the senators come from master.xlsx
        return exit_code, self.output.getvalue()

    def test_postal_code_lookup_lists_the_provinces_senators(self):
        self.scrape()
        exit_code, out = self.postal_code(province="QC")
        self.assertEqual(exit_code, 0)
        self.assertIn("Senate of Canada -- 3 senator(s) for Quebec (from master.xlsx)", out)
        self.assertIn("Senator Claude Carignan (Conservative Party of Canada) -- "
                      "claude.carignan@sen.parl.gc.ca, 1 613 992-0240", out)
        self.assertLess(out.index("House of Commons"), out.index("Senate of Canada"))
        _, out = self.postal_code("--category", "local", province="QC")
        self.assertNotIn("Senate of Canada", out)


class ChangeReportTests(ScraperTestCase):
    def test_the_senates_first_appearance_is_a_first_import_not_new_faces(self):
        # MPs first, while the Senate's site is down; the senators a day later.
        runs = ((2, FakeSenate(senators=http_error(senate.LIST_URL, 404))), (1, FakeSenate()))
        for days_ago, site in runs:
            self.output.truncate(0)
            self.output.seek(0)
            moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
            with FakeWeb() as web, mock.patch.object(rc, "_now_iso", return_value=moment.strftime(TIMESTAMP_FORMAT)):
                sample_represent().install(web)
                site.install(web)
                scrape_federal.main(["--no-enrich"])
        out = self.output.getvalue()
        self.assertIn("Federal (House of Commons and Senate): first import of 8 people", out)
        self.assertNotIn("Charles S. Adler", out)
