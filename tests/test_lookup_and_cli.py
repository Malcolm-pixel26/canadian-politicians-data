import sys
from unittest import mock

from support import FakeSenate, FakeWeb, ScraperTestCase, sample_represent

import changes_digest
import cli_budgets
import cli_politicians
import lookup_politician
import make_maps
import scrape_all
import scrape_all_budgets
import scrape_budget_federal
import scrape_budget_local
import scrape_budget_office_spending
import scrape_budget_provincial_territorial
import scrape_careers
import scrape_federal
import scrape_local
import scrape_provincial_territorial
import scrape_votes


class LookupTests(ScraperTestCase):
    def test_name_matching(self):
        match = lookup_politician.name_matches
        self.assertTrue(match("hallan", "Jasraj Hallan"))
        self.assertTrue(match("Hallan Jas", "Jasraj Hallan"))
        self.assertTrue(match("marilene", "Marilène Gill"))
        self.assertTrue(match("GILL MARILÈNE", "Marilène Gill"))
        self.assertFalse(match("ann", "Joanne Smith"))
        self.assertFalse(match("", "Anyone"))

    def test_cached_lookup(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            scrape_federal.main(["--no-enrich"])
            scrape_provincial_territorial.main([])
        self.assertEqual(lookup_politician.main(["gill", "--no-enrich"]), 0)
        self.assertIn("Found 2 match(es)", self.output.getvalue())
        self.assertEqual(lookup_politician.main(["smith", "--category", "provincial"]), 0)
        self.assertIn("Scarborough Centre", self.output.getvalue())
        self.assertEqual(lookup_politician.main(["gill", "--category", "local"]), 1)

    def test_live_lookup_needs_no_category(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            FakeSenate().install(web)
            self.assertEqual(lookup_politician.main(["brisebois", "--live"]), 0)
            self.assertLessEqual(web.count("represent.opennorth.ca"), 2)
            self.assertEqual(web.count("sencanada.ca"), 2)  # senators are searched too
        self.assertIn("Conseil municipal de Senneville", self.output.getvalue())

    def test_live_lookup_by_place(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            self.assertEqual(lookup_politician.main(["mantas", "--live", "--place", "toronto"]), 0)
            self.assertEqual(web.count("/representatives/toronto-city-council/"), 1)


class CommandLineTests(ScraperTestCase):
    def test_typos_are_rejected_instead_of_ignored(self):
        # v4 silently ignored unknown flags: "--filtr Toronto" ran the full scrape.
        for module in (scrape_federal, scrape_provincial_territorial, scrape_local, scrape_all,
                       lookup_politician, scrape_budget_federal, scrape_budget_provincial_territorial,
                       scrape_budget_local, scrape_all_budgets, scrape_votes, changes_digest, make_maps,
                       scrape_budget_office_spending, scrape_careers):
            with self.subTest(module=module.__name__), mock.patch("sys.stderr"):
                argv = ["Jane", "--filtr", "x"] if module is lookup_politician else ["--filtr", "x"]
                with self.assertRaises(SystemExit) as ctx:
                    module.parse_args(argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_dispatcher_passes_arguments_and_exit_code(self):
        seen = {}

        def fake_main():
            seen["argv"] = list(sys.argv)
            return 1

        with mock.patch.object(scrape_local, "main", fake_main), \
                mock.patch.object(sys, "argv", ["politician_scrapers", "local", "--filter", "Toronto"]):
            with self.assertRaises(SystemExit) as ctx:
                cli_politicians.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(seen["argv"], ["politician_scrapers", "--filter", "Toronto"])

    def test_provincial_territorial_alias(self):
        with mock.patch.object(scrape_budget_provincial_territorial, "main", return_value=0) as fake, \
                mock.patch.object(sys, "argv", ["budget_scrapers", "provincial_territorial"]):
            with self.assertRaises(SystemExit) as ctx:
                cli_budgets.main()
        self.assertEqual(ctx.exception.code, 0)
        fake.assert_called_once()

    def test_new_subcommands_are_dispatched(self):
        cases = (("votes", scrape_votes, cli_politicians), ("digest", changes_digest, cli_politicians),
                 ("maps", make_maps, cli_politicians), ("careers", scrape_careers, cli_politicians),
                 ("spending", scrape_budget_office_spending, cli_budgets),
                 ("office_spending", scrape_budget_office_spending, cli_budgets))
        for command, module, cli in cases:
            with self.subTest(command=command), mock.patch.object(module, "main", return_value=0) as fake, \
                    mock.patch.object(sys, "argv", ["scrapers", command, "--help-me"]):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()
                self.assertEqual(ctx.exception.code, 0)
                fake.assert_called_once()
                self.assertEqual(sys.argv, ["scrapers", "--help-me"])
