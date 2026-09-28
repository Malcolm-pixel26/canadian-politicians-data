import io
import os
import zipfile
from unittest import mock

from support import FakeResponse, FakeWeb, ScraperTestCase, http_error, load_fixture
from test_office_spending import FakeCommons

import budget_common
import represent_common as rc
import scrape_all_budgets
import scrape_budget_federal as sbf
import scrape_budget_provincial_territorial as sbp

SAMPLE = load_fixture("represent_sample.json")

# Elections Canada's contributions header. The recipient, party, district,
# event, date and amount columns (and their order) are as they appear in
# the published file; the contributor columns in between are abridged.
HEADER = (
    "Political Entity,Recipient ID,Recipient,Recipient last name,Recipient first name,"
    "Recipient middle initial,Political Party of Recipient,Electoral District,Electoral event,"
    "Fiscal/Election date,Form ID,Financial Report,Part Number of Return,Financial Report part,"
    "Contributor type,Contributor name,Contributor City,Contributor Province,Contributor Postal code,"
    "Contribution Received date,Monetary amount,Non-Monetary amount,Contribution given through"
)
ROWS = [
    # The first line matches a published sample of the real file.
    'Candidates,4350,"Whelan, Susan",Whelan,Susan,,Liberal Party of Canada,Essex,38th general election,'
    "2004-06-28,,,,,Individuals,A Donor,Windsor,ON,N9A 1A1,2004-06-01,800.0,0.0,",
    'Candidates,4350,"Whelan, Susan",Whelan,Susan,,Liberal Party of Canada,Essex,38th general election,'
    "2004-06-28,,,,,Individuals,B Donor,Windsor,ON,N9A 1A1,2004-06-02,1280.0,50.0,",
    'Candidates,77001,"Hallan, Jasraj Singh",Hallan,Jasraj Singh,,Conservative Party of Canada,'
    "Calgary Forest Lawn,43rd general election,2019-10-21,,,,,Individuals,C Donor,Calgary,AB,T2A 1A1,"
    "2019-09-01,500.0,0.0,",
    'Leadership contestants,88001,"Hallan, Jasraj Singh",Hallan,Jasraj Singh,,Conservative Party of Canada,'
    ",Leadership contest,2022-09-10,,,,,Individuals,D Donor,Calgary,AB,T2A 1A1,2022-05-01,250.0,0.0,",
    "Registered parties,1,Liberal Party of Canada,,,,Liberal Party of Canada,,Annual,2019-12-31,,,,,"
    "Individuals,E Donor,Ottawa,ON,K1A 0A1,2019-03-01,100.0,0.0,",
    'Registered associations,2,"Essex, Liberal Association",,,,Liberal Party of Canada,Essex,Annual,2019-12-31,'
    ",,,,Individuals,F Donor,Windsor,ON,N9A 1A1,2019-03-01,75.0,0.0,",
]


def contributions_csv(rows=ROWS, header=HEADER):
    return io.StringIO(header + "\n" + "\n".join(rows) + "\n")


def master_row(name, district, first, last):
    return {"name": name, "first_name": first, "last_name": last, "district_name": district,
            "category": "federal", "party_name": "", "representative_set": "House of Commons",
            "province_territory": ""}


class AggregateTests(ScraperTestCase):
    def test_last_first_recipient_is_keyed_first_last(self):
        # v4 keyed this "whelan susan", so it never matched "Susan Whelan".
        totals = sbf.aggregate_contributions(contributions_csv())
        self.assertIn("susan whelan", totals)
        self.assertNotIn("whelan susan", totals)

    def test_totals_types_dates_and_districts(self):
        whelan = sbf.aggregate_contributions(contributions_csv())["susan whelan"]
        self.assertEqual(whelan["recipient_type"], {"Candidates"})  # v4: always ""
        self.assertEqual(whelan["total_contributions"], 2080.0)
        self.assertEqual(whelan["non_monetary_contributions"], 50.0)
        self.assertEqual(whelan["contribution_count"], 2)
        self.assertEqual((whelan["min_date"], whelan["max_date"]), ("2004-06-28", "2004-06-28"))
        self.assertEqual(whelan["electoral_districts"], {"Essex"})

    def test_a_persons_roles_are_combined(self):
        hallan = sbf.aggregate_contributions(contributions_csv())["jasraj singh hallan"]
        self.assertEqual(hallan["recipient_type"], {"Candidates", "Leadership contestants"})
        self.assertEqual(hallan["total_contributions"], 750.0)
        self.assertEqual((hallan["first_name"], hallan["last_name"]), ("Jasraj Singh", "Hallan"))

    def test_parties_and_associations_keep_their_names(self):
        totals = sbf.aggregate_contributions(contributions_csv())
        self.assertIn("liberal party of canada", totals)
        self.assertIn("essex liberal association", totals)  # not flipped like a person's name

    def test_since_filters_on_the_election_or_fiscal_date(self):
        totals = sbf.aggregate_contributions(contributions_csv(), since_year=2019)
        self.assertNotIn("susan whelan", totals)
        self.assertIn("jasraj singh hallan", totals)

    def test_missing_columns_stop_the_run_with_the_real_header(self):
        with self.assertRaises(RuntimeError) as ctx:
            sbf.aggregate_contributions(contributions_csv(rows=[], header="Recipient,Non-Monetary amount"))
        self.assertIn("Non-Monetary amount", str(ctx.exception))  # v4 would have summed that column

    def test_every_csv_in_the_zip_is_read_and_cp1252_is_tolerated(self):
        path = os.path.join(self.tmp, "od.zip")
        extra = ('Candidates,5,"Tremblay, Jos\xe9e",Tremblay,Jos\xe9e,,Bloc,Qu\xe9bec,45th general election,'
                 "2025-04-28,,,,,Individuals,G Donor,Québec,QC,G1A 1A1,2025-04-01,10.0,0.0,")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("part1.csv", ("﻿" + HEADER + "\n" + ROWS[0] + "\n").encode("utf-8"))
            zf.writestr("part2.csv", (HEADER + "\n" + extra + "\n").encode("cp1252"))
        totals = sbf.read_contributions_from_zip(path)
        self.assertEqual(set(totals), {"susan whelan", "josee tremblay"})
        self.assertEqual(totals["josee tremblay"]["electoral_districts"], {"Québec"})


class MatchingTests(ScraperTestCase):
    def test_matching_rules(self):
        totals = sbf.aggregate_contributions(contributions_csv())
        master = [
            master_row("Susan Whelan", "Essex", "Susan", "Whelan"),
            master_row("Jasraj Hallan", "Calgary East", "Jasraj", "Hallan"),
            master_row("Jane Nobody", "Ottawa Centre", "Jane", "Nobody"),
        ]
        rows = {r["name"]: r for r in budget_common.build_budget_rows(master, totals, "no funding data found")}
        self.assertEqual(rows["Susan Whelan"]["match_status"], "matched")
        self.assertEqual(rows["Susan Whelan"]["total_contributions"], 2080.0)
        self.assertEqual(rows["Susan Whelan"]["avg_contribution"], 1040.0)
        self.assertEqual(rows["Susan Whelan"]["date_range"], "2004-06-28")
        self.assertEqual(rows["Jasraj Hallan"]["match_status"],
                         "matched (first + last name only -- check); riding differs -- check")
        self.assertEqual(rows["Jasraj Hallan"]["date_range"], "2019-10-21 to 2022-09-10")
        self.assertEqual(rows["Jane Nobody"]["match_status"], "no funding data found")
        self.assertEqual(rows["Liberal Party of Canada"]["match_status"], budget_common.UNMATCHED_RECIPIENT_STATUS)
        self.assertNotIn("Jasraj Singh Hallan", rows)  # matched, so not also listed as unmatched

    def test_fetcher_extension_point(self):
        rc.upsert_master(self.master_path, [
            rc.flatten_representative(r, "provincial_territorial", province="ON")
            for r in SAMPLE["representatives"] if r["representative_set_name"] == "Legislative Assembly of Ontario"
        ], "provincial_territorial")
        fetched = {"david smith": {"raw_recipient_name": "David Smith", "recipient_type": "Candidate",
                                   "total_contributions": 1000.0, "contribution_count": 4}}
        with mock.patch.dict(sbp.PROVINCE_FETCHERS, {"ontario": lambda: fetched}):
            rows = {r["name"]: r for r in sbp.build_budget_rows(only_province="ontario")}
        self.assertEqual(rows["David Smith"]["avg_contribution"], 250.0)
        self.assertEqual(rows["Bobbi Ann Brady"]["match_status"], sbp.NOT_COVERED_STATUS)
        with self.assertRaises(SystemExit):
            sbp.build_budget_rows(only_province="yukon")


class BudgetScriptTests(ScraperTestCase):
    def make_zip(self):
        path = os.path.join(self.tmp, "contributions.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("od_cntrbtn_de_e.csv", HEADER + "\n" + "\n".join(ROWS) + "\n")
        return path

    def test_all_budget_tabs(self):
        rc.upsert_master(self.master_path, [
            rc.flatten_representative(r, "federal") for r in SAMPLE["representatives"]
            if r["representative_set_name"] == "House of Commons"
        ], "federal")
        with FakeWeb() as web, mock.patch.object(sbf, "download_contributions_zip", return_value=self.make_zip()):
            FakeCommons().install(web)
            self.assertEqual(scrape_all_budgets.main(["--since", "2004"]), 0)

        path = budget_common.budget_workbook_path()
        self.assertEqual(self.sheet_names(path),
                         ["Federal", "Federal_Office_Spending", "Provincial_Territorial", "Local", "Notes"])
        spending = {r["Name"]: r for r in self.read_sheet(path, "Federal_Office_Spending")}
        self.assertEqual(spending["Jasraj Hallan"]["Match Status"], "matched")
        federal = {r["Name"]: r for r in self.read_sheet(path, "Federal")}
        self.assertEqual(federal["Jasraj Hallan"]["Total Contributions"], 750)
        self.assertEqual(federal["Jasraj Hallan"]["Province Territory"], "AB")
        self.assertEqual(federal["Susan Whelan"]["Match Status"], budget_common.UNMATCHED_RECIPIENT_STATUS)
        notes = {r["Category"]: r["Scope / Notes"] for r in self.read_sheet(path, "Notes")}
        self.assertIn("as submitted", notes["Federal"])
        self.assertIn("2004 or later", notes["Federal"])
        self.assertIn("Matched 1/6 federal politician(s)", self.output.getvalue())

    def test_download_is_validated_and_retried(self):
        with open(self.make_zip(), "rb") as f:
            good = f.read()
        responses = [FakeResponse(good[: len(good) // 2]), FakeResponse(good)]
        with FakeWeb() as web:
            web.route("https://www.elections.ca/fin/oda/", lambda url: responses.pop(0))
            path = sbf.download_contributions_zip(dataset="reviewed")
            self.assertEqual(web.count("od_cntrbtn_audt_e.zip"), 2)
        self.assertTrue(path.endswith("od_cntrbtn_audt_e.zip"))
        self.assertTrue(sbf._is_valid_zip(path))
        self.assertFalse(os.path.exists(path + ".part"))

    def test_missing_download_is_not_retried(self):
        with FakeWeb() as web:
            web.route("https://www.elections.ca/", lambda url: http_error(url, 404))
            with self.assertRaises(RuntimeError):
                sbf.download_contributions_zip()
            self.assertEqual(len(web.requests), 1)
