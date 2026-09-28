import copy
from unittest import mock

from support import REPRESENT, FakeFederalSources, FakeWeb, ScraperTestCase, http_error, json_response, load_fixture

import lookup_politician
import represent_common as rc

POSTCODE = load_fixture("represent_postcode.json")


def neighbouring_mp():
    """A second MP linked to the same postal code, as happens when one
    straddles a riding boundary (shape copied from the fixture)."""
    mp = copy.deepcopy(next(r for r in POSTCODE["representatives_centroid"] if r["elected_office"] == "MP"))
    mp.update(name="Test Neighbour", first_name="Test", last_name="Neighbour",
              district_name="Mississauga East—Cooksville",
              url="https://www.ourcommons.ca/Members/en/test-neighbour(1)")
    return mp


class PostalCodeTests(ScraperTestCase):
    def lookup(self, *argv, payload=POSTCODE, sources=None):
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/postcodes/", lambda url: json_response(payload))
            if sources:
                sources.install(web)
            exit_code = lookup_politician.main(list(argv))
        return exit_code, web, self.output.getvalue()

    def test_every_level_of_government_in_one_request(self):
        exit_code, web, out = self.lookup("--postal-code", "l5g 4l3", "--no-enrich")
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(web.requests), 1)
        self.assertIn("Representatives for L5G 4L3 (Mississauga, ON):", out)
        self.assertIn("MP Charles Sousa (Liberal) -- Mississauga—Lakeshore", out)
        self.assertIn("MPP Rudy Cuzzetto (Progressive Conservative Party of Ontario)", out)
        self.assertIn(
            "Local office: Main office - Mississauga, 30-1077 North Service road, Mississauga ON L4Y 1A6, "
            "1 905 273-8033", out)
        # She's both the city's mayor and on the regional council.
        self.assertEqual(out.count("Mayor Carolyn Parrish"), 2)
        levels = [out.index(title) for title in ("\nFederal", "\nProvincial / territorial", "\nLocal")]
        self.assertEqual(levels, sorted(levels))
        peel = out[out.index("Peel Regional Council"):]
        self.assertLess(peel.index("Regional Chair Nando Iannicca"), peel.index("Councillor Stephen Dasko"))
        self.assertNotIn("one of these", out)

    def test_a_postal_code_straddling_ridings_is_flagged(self):
        payload = dict(POSTCODE, representatives_concordance=[neighbouring_mp()])
        _, _, out = self.lookup("--postal-code", "L5G4L3", "--no-enrich", payload=payload)
        self.assertEqual(out.count("<- one of these"), 2)
        self.assertIn("straddles a district boundary", out)
        self.assertIn("--point", out)

    def test_duplicates_across_both_lists_are_shown_once(self):
        payload = dict(POSTCODE, representatives_concordance=POSTCODE["representatives_centroid"])
        _, _, out = self.lookup("--postal-code", "L5G4L3", "--no-enrich", payload=payload)
        self.assertEqual(out.count("MP Charles Sousa"), 1)
        self.assertNotIn("one of these", out)

    def test_category_filter(self):
        _, _, out = self.lookup("--postal-code", "L5G4L3", "--category", "federal", "--no-enrich")
        self.assertIn("Charles Sousa", out)
        self.assertNotIn("Rudy Cuzzetto", out)

    def test_the_mp_is_enriched(self):
        sources = FakeFederalSources(committees={"114349": [("Finance", "Member", None)]})
        _, _, out = self.lookup("--postal-code", "L5G4L3", sources=sources)
        self.assertIn("Latest federal data for Charles Sousa:", out)
        self.assertIn("Committees: Finance", out)

    def test_unknown_postal_code(self):
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/postcodes/", lambda url: http_error(url, 404))
            self.assertEqual(lookup_politician.main(["--postal-code", "K0K 0K0"]), 1)
        self.assertIn("doesn't know the postal code K0K 0K0", self.output.getvalue())

    def test_exact_point(self):
        reps = POSTCODE["representatives_centroid"]
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/representatives/?point=43.58,-79.58",
                      lambda url: json_response({"objects": reps, "meta": {"next": None, "total_count": len(reps)}}))
            self.assertEqual(lookup_politician.main(["--point", "43.58,-79.58", "--no-enrich"]), 0)
        out = self.output.getvalue()
        self.assertIn("Representatives for 43.58, -79.58:", out)
        self.assertIn("MP Charles Sousa", out)


class ArgumentTests(ScraperTestCase):
    def test_postal_code_normalization(self):
        self.assertEqual(rc.normalize_postal_code("k1a 0a6"), "K1A0A6")
        self.assertEqual(rc.normalize_postal_code(" K1A-0A6 "), "K1A0A6")
        for bad in ("12345", "K1A0A", "W1A0A6", "D1A1A1", "K1A 0A6 7", ""):
            self.assertIsNone(rc.normalize_postal_code(bad), bad)

    def test_exactly_one_target(self):
        bad_argv = ([], ["Jane", "--postal-code", "K1A0A6"], ["--postal-code", "12345"],
                    ["--point", "north"], ["--point", "91,0"])
        for argv in bad_argv:
            with self.subTest(argv=argv), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    lookup_politician.parse_args(argv)
        self.assertEqual(lookup_politician.parse_args(["--postal-code", "k1a 0a6"]).postal_code, "K1A0A6")
        self.assertEqual(lookup_politician.parse_args(["--point", "45.5,-73.6"]).point, (45.5, -73.6))
