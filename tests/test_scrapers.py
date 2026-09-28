"""End-to-end runs of the scraper scripts against the fake APIs."""
import csv
import json
import os

from support import (
    FakeFederalSources,
    FakeWeb,
    ScraperTestCase,
    http_error,
    load_fixture,
    sample_represent,
)

import represent_common as rc
import scrape_all
import scrape_federal
import scrape_local
import scrape_provincial_territorial

SAMPLE = load_fixture("represent_sample.json")


class FederalScraperTests(ScraperTestCase):
    def run_federal(self, *argv, sources=None):
        with FakeWeb() as web:
            sample_represent().install(web)
            (sources or FakeFederalSources()).install(web)
            exit_code = scrape_federal.main(list(argv))
        return exit_code, web

    def federal_csv(self):
        with open(os.path.join(self.tmp, "federal", "federal_mps.csv"), encoding="utf-8-sig", newline="") as f:
            return {r["name"]: r for r in csv.DictReader(f)}

    def test_default_run_writes_every_output_and_the_master(self):
        # In v4 this run crashed in write_csv and never reached master.xlsx.
        exit_code, _ = self.run_federal()
        self.assertEqual(exit_code, 0)
        rows = self.federal_csv()
        self.assertEqual(len(rows), 6)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "federal", "federal_mps.xlsx")))
        with open(os.path.join(self.tmp, "federal", "federal_mps.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 6)
        master = {r["Name"]: r for r in self.read_sheet(self.master_path, "Federal")}
        self.assertEqual(len(master), 6)
        self.assertEqual(master["Parm Bains"]["Province Territory"], "BC")

    def test_enrichment(self):
        self.run_federal()
        rows = self.federal_csv()
        # Committees come from the House of Commons XML (v4's were always blank);
        # ended roles are left out and non-member roles are labelled.
        self.assertEqual(rows["Rachel Bendayan"]["committees"], "Procedure and House Affairs (Vice-Chair)")
        self.assertEqual(rows["Parm Bains"]["committees"], "Finance")
        self.assertEqual(rows["Marilène Gill"]["committees"], "")
        # "Jasraj Hallan" is "Jasraj Singh Hallan" on OpenParliament: matched by riding.
        hallan = rows["Jasraj Hallan"]
        self.assertEqual(hallan["openparliament_url"], "https://openparliament.ca/politicians/jasraj-singh-hallan/")
        self.assertEqual(hallan["first_elected"], "2019-10-21")
        self.assertEqual(hallan["committees"], "Public Accounts (Chair)")
        self.assertEqual(rows["Rachel Bendayan"]["first_elected"], "2019-02-25")

    def test_id_check_rejects_a_wrong_riding_match(self):
        wrong = dict(load_fixture("openparliament_sample.json")["details"]["/politicians/jasraj-singh-hallan/"],
                     other_info={"parl_mp_id": ["99999"]})
        self.run_federal(sources=FakeFederalSources(details_override={"/politicians/jasraj-singh-hallan/": wrong}))
        self.assertEqual(self.federal_csv()["Jasraj Hallan"]["openparliament_url"], "")
        self.assertIn("IDs differ", self.output.getvalue())

    def test_no_enrich(self):
        exit_code, web = self.run_federal("--no-enrich")
        self.assertEqual(exit_code, 0)
        self.assertEqual(web.count("openparliament"), 0)
        self.assertEqual(web.count("ourcommons"), 0)

    def test_enrichment_outage_does_not_stop_the_run(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            web.route("https://api.openparliament.ca", lambda url: http_error(url, 503))
            web.route("https://www.ourcommons.ca", lambda url: http_error(url, 503))
            exit_code = scrape_federal.main([])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(self.read_sheet(self.master_path, "Federal")), 6)

    def test_unreachable_committee_site_is_given_up_on(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeFederalSources().install(web)
            web.route("https://www.ourcommons.ca", lambda url: TimeoutError("timed out"))
            self.assertEqual(scrape_federal.main([]), 0)
            self.assertEqual(web.count("ourcommons.ca"), rc.MAX_CONSECUTIVE_FAILURES * rc.NETWORK_MAX_ATTEMPTS)
        self.assertEqual(self.federal_csv()["Jasraj Hallan"]["first_elected"], "2019-10-21")

    def test_enrichment_data_is_kept_when_a_later_run_skips_it(self):
        self.run_federal()
        self.run_federal("--no-enrich")
        master = {r["Name"]: r for r in self.read_sheet(self.master_path, "Federal")}
        self.assertEqual(master["Jasraj Hallan"]["Committees"], "Public Accounts (Chair)")

    def test_second_run_records_no_changes(self):
        self.run_federal()
        self.run_federal()
        self.assertIn("Master workbook: 0 change(s) recorded this run.", self.output.getvalue())

    def test_represent_outage_changes_nothing(self):
        with FakeWeb() as web:
            fake = sample_represent().install(web)
            web.route(lambda url: fake.sets[0]["related"]["representatives_url"] in url,
                      lambda url: http_error(url, 503))
            exit_code = scrape_federal.main(["--no-enrich"])
        self.assertEqual(exit_code, 1)
        self.assertFalse(os.path.exists(self.master_path))


class ProvincialAndLocalTests(ScraperTestCase):
    def test_provincial_run(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            self.assertEqual(scrape_provincial_territorial.main([]), 0)
        folder = os.path.join(self.tmp, "provincial_territorial")
        self.assertTrue(os.path.exists(os.path.join(folder, "ontario-legislature.csv")))
        self.assertTrue(os.path.exists(os.path.join(folder, "all_provincial_territorial.json")))
        self.assertIn("Not available from Represent:", self.output.getvalue())
        self.assertIn("Nunavut", self.output.getvalue())
        self.assertEqual(len(self.read_sheet(self.master_path, "Provincial_Territorial")), 2)

    def test_local_province_filter_then_full_run(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            self.assertEqual(scrape_local.main(["--province", "qc"]), 0)
        names = {r["Name"] for r in self.read_sheet(self.master_path, "Local")}
        self.assertEqual(names, {"Julie Brisebois", "Pierre Matuszewski"})

        with FakeWeb() as web:
            sample_represent().install(web)
            self.assertEqual(scrape_local.main([]), 0)
        local = self.read_sheet(self.master_path, "Local")
        local_sets = {s["url"] for s in SAMPLE["sets"] if rc.classify_set(s) == "local"}
        expected = [r for r in SAMPLE["representatives"] if r["related"]["representative_set_url"] in local_sets]
        self.assertEqual(len(local), len(expected))
        self.assertEqual({r["Status"] for r in local}, {"active"})

    def test_no_internet_gives_a_clear_message(self):
        with FakeWeb() as web:
            web.route("https://", lambda url: OSError(101, "Network is unreachable"))
            self.assertEqual(scrape_local.main([]), 1)
            self.assertLess(web.now - 1_000, 30)
        self.assertIn("Couldn't reach represent.opennorth.ca", self.output.getvalue())
        self.assertFalse(os.path.exists(self.master_path))

    def test_local_run_is_a_handful_of_requests(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            scrape_local.main([])
            self.assertEqual(len(web.requests), 2)  # set list + one bulk page

    def test_scrape_all_shares_downloads_and_keeps_going(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeFederalSources().install(web)
            web.route("https://www.ourcommons.ca", lambda url: RuntimeError("boom"))
            exit_code = scrape_all.main([])
            self.assertEqual(web.count("/representative-sets/"), 1)
            self.assertEqual(web.count("/representatives/?"), 1)
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.sheet_names(self.master_path), ["Federal", "Provincial_Territorial", "Local", "Changelog"])
        self.assertEqual(len(rc.read_all_master_rows(self.master_path)), len(SAMPLE["representatives"]))
