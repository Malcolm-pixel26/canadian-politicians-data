import os
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from support import FakeWeb, ScraperTestCase, load_fixture, sample_represent

import changes_digest
import represent_common as rc
import scrape_federal

SAMPLE = load_fixture("represent_sample.json")
MPS = [r for r in SAMPLE["representatives"] if r["representative_set_name"] == "House of Commons"]


def stamp(days_ago):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime(changes_digest.TIMESTAMP_FORMAT)


def mp_rows(**changes_by_name):
    rows = []
    for rep in MPS:
        row = rc.flatten_representative(rep, "federal")
        row.update(changes_by_name.get(rep["name"], {}))
        rows.append(row)
    return rows


class DigestTests(ScraperTestCase):
    def run_at(self, days_ago, rows):
        with mock.patch.object(rc, "_now_iso", return_value=stamp(days_ago)):
            return rc.upsert_master(self.master_path, rows, "federal")

    def make_history(self):
        """First import 8 days ago, then a run with one of every kind of change 1 day ago."""
        self.run_at(8, mp_rows())
        rows = mp_rows(**{
            "Rachel Bendayan": {"party_name": "Independent"},
            "Jasraj Hallan": {"email": "jasraj.hallan@parl.gc.ca"},
            "Parm Bains": {"photo_url": "https://example.org/new-photo.jpg"},
            "Marilène Gill": {"roles": "Whip of the Bloc Québécois"},
        })
        rows = [r for r in rows if r["name"] != "Tamara Kronis"]
        newcomer = dict(rows[0], name="<script>alert(1)</script> Newcomer", first_name="Test",
                        last_name="Newcomer", district_name="Test Riding", url="https://example.org/new")
        return self.run_at(1, rows + [newcomer])

    def test_digest_groups_every_kind_of_change(self):
        changes = self.make_history()
        people = changes_digest._people(self.master_path)
        firsts = changes_digest._first_import_times(rc.read_master_changelog(self.master_path))
        section = changes_digest.build_digest(changes, people, firsts)["federal"]

        self.assertEqual(section["first_import"], 0)  # this run isn't the first import
        self.assertEqual([p["name"] for p in section["removed"]], ["Tamara Kronis"])
        kronis = section["removed"][0]
        self.assertEqual((kronis["office"], kronis["district"], kronis["party"]),
                         ("MP", "Nanaimo—Ladysmith", "Conservative"))
        self.assertEqual(len(section["added"]), 1)
        party = section["party"][0]
        self.assertEqual((party["name"], party["old"], party["new"]), ("Rachel Bendayan", "Liberal", "Independent"))
        self.assertEqual([c["field"] for c in section["contact"]], ["email"])
        self.assertEqual([c["field"] for c in section["roles"]], ["roles"])
        self.assertEqual(section["minor"], {"photo_url": 1})
        self.assertEqual(changes_digest.counts_line(section),
                         "1 left office, 1 new, 1 party change, 1 role/committee change, "
                         "1 contact update, 1 minor update")

    def test_first_import_is_summarized_not_listed(self):
        changes = self.run_at(0, mp_rows())
        firsts = changes_digest._first_import_times(rc.read_master_changelog(self.master_path))
        section = changes_digest.build_digest(changes, {}, firsts)["federal"]
        self.assertEqual((section["first_import"], section["added"]), (6, []))
        self.assertIn("first import of 6 people", changes_digest.render_text({"federal": section}))

    def test_text_summary(self):
        text = changes_digest.render_text(changes_digest.build_digest(self.make_history()))
        self.assertIn("Party changes:", text)
        self.assertIn("Rachel Bendayan: Party Liberal -> Independent", text)
        self.assertIn("Left office:", text)

    def test_html_report_escapes_scraped_text(self):
        self.make_history()
        changes_digest.main(["--days", "30"])
        path = self.output.getvalue().split("Full report: ")[1].strip()
        with open(path, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; Newcomer", page)
        self.assertNotIn("<script>", page)
        self.assertIn("First import: 6 people", page)
        self.assertIn("Party changes (1)", page)
        self.assertIn("Tamara Kronis", page)

    def test_period_filtering(self):
        self.make_history()
        self.assertEqual(changes_digest.main(["--days", "3"]), 0)
        recent = self.output.getvalue()
        self.assertIn("1 party change", recent)
        self.assertNotIn("first import", recent)

        self.output.truncate(0)
        self.output.seek(0)
        since = (date.today() - timedelta(days=10)).isoformat()
        self.assertEqual(changes_digest.main(["--since", since]), 0)
        self.assertIn("first import of 6 people", self.output.getvalue())
        self.assertTrue(os.path.exists(os.path.join(
            self.tmp, "changes", f"changes_{since}_to_{date.today().isoformat()}.html")))

    def test_no_master_yet(self):
        self.assertEqual(changes_digest.main([]), 1)
        self.assertIn("run a scraper first", self.output.getvalue())

    def test_bad_arguments(self):
        for argv in (["--days", "0"], ["--since", "last week"], ["--days", "3", "--since", "2026-01-01"]):
            with self.subTest(argv=argv), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    changes_digest.parse_args(argv)


class AfterRunTests(ScraperTestCase):
    def test_scraper_prints_a_summary_and_writes_todays_report(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            scrape_federal.main(["--no-enrich"])
        out = self.output.getvalue()
        self.assertIn("Federal (House of Commons and Senate): first import of 6 people", out)
        report = os.path.join(self.tmp, "changes", f"changes_{date.today().isoformat()}.html")
        self.assertIn(f"Today's change report: {report}", out)
        self.assertTrue(os.path.exists(report))

    def test_a_reporting_problem_never_fails_the_run(self):
        with mock.patch.object(changes_digest, "read_master_changelog", side_effect=OSError("disk full")):
            changes_digest.report_run([{"category": "federal"}], master_path=self.master_path)
        self.assertIn("Couldn't write the change report (disk full)", self.output.getvalue())
