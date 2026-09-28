from collections import Counter

from support import ScraperTestCase, load_fixture

import represent_common as rc

SAMPLE = load_fixture("represent_sample.json")


def rep(name):
    return next(r for r in SAMPLE["representatives"] if r["name"] == name)


def rep_set(name):
    return next(s for s in SAMPLE["sets"] if s["name"] == name)


class ClassificationTests(ScraperTestCase):
    def test_every_live_set_is_classified(self):
        names = load_fixture("represent_set_names.json")["names"]
        counts = Counter(rc.classify_set({"name": n}) for n in names)
        self.assertEqual(counts, {"local": 108, "provincial_territorial": 12, "federal": 1})

    def test_province_comes_from_the_scraper_id(self):
        self.assertEqual(rc.province_for_set(rep_set("Toronto City Council")), "ON")
        self.assertEqual(rc.province_for_set(rep_set("Conseil municipal de Senneville")), "QC")
        self.assertEqual(rc.province_for_set(rep_set("Legislative Assembly of Ontario")), "ON")
        self.assertEqual(rc.province_for_set({"data_url": "https://scrapers.herokuapp.com/represent/ca_nt/"}), "NT")
        self.assertEqual(rc.province_for_set({"data_url": "https://scrapers.herokuapp.com/represent/ca_pe_stratford/"}), "PE")
        self.assertEqual(rc.province_for_set(rep_set("House of Commons")), "")
        self.assertEqual(rc.province_for_set({}), "")

    def test_mp_province_comes_from_the_riding_code(self):
        self.assertEqual(rc.province_for_representative(rep("Parm Bains")), "BC")
        self.assertEqual(rc.province_for_representative(rep("Rachel Bendayan")), "QC")
        self.assertEqual(rc.province_for_representative(rep("Jasraj Hallan")), "AB")
        self.assertEqual(rc.province_for_representative(rep("Nick Mantas")), "")


class FlattenTests(ScraperTestCase):
    def test_v4_columns_are_unchanged(self):
        row = rc.flatten_representative(rep("Rachel Bendayan"), "federal")
        self.assertEqual(row["phone"], "1 613 995-7691")
        self.assertEqual(row["postal_address"], "House of Commons, Ottawa ON K1A 0A6")
        self.assertEqual(row["url"], "https://www.ourcommons.ca/Members/en/rachel-bendayan(88567)")
        self.assertEqual(row["representative_set"], "House of Commons")
        self.assertEqual(list(row), rc.CSV_FIELDS)

    def test_constituency_office_roles_and_languages(self):
        row = rc.flatten_representative(rep("Rachel Bendayan"), "federal")
        self.assertEqual(row["province_territory"], "QC")
        self.assertEqual(row["constituency_phone"], "1 514 736-2727")
        self.assertEqual(row["constituency_address"],
                         "Main office - Montréal, 302-154 avenue Laurier Ouest, Montréal QC H2T 2N7")
        self.assertEqual(row["roles"], "Parliamentary Secretary to the Prime Minister (Parliamentary Secretary)")
        self.assertEqual(row["preferred_languages"], "French English")

    def test_first_constituency_office_with_contact_details_wins(self):
        row = rc.flatten_representative(rep("Tamara Kronis"), "federal")
        self.assertEqual(row["constituency_phone"], "1 250 434-7110")
        self.assertTrue(row["constituency_address"].startswith("Main office - Nanaimo"))

    def test_other_extra_fields_are_kept(self):
        row = rc.flatten_representative(rep("Bobbi Ann Brady"), "provincial_territorial", province="ON")
        self.assertEqual(row["extra_info"], "constituency_email: babrady-co@ola.org")

    def test_councillor_without_offices(self):
        row = rc.flatten_representative(rep("John Becker"), "local", province="ON")
        self.assertEqual((row["phone"], row["postal_address"], row["constituency_phone"]), ("", "", ""))
        self.assertEqual(row["province_territory"], "ON")

    def test_nulls_become_empty_strings(self):
        row = rc.flatten_representative({"name": "Jane Doe", "email": None, "offices": None, "extra": None}, "local")
        self.assertEqual(row["email"], "")
        self.assertEqual(row["roles"], "")
        self.assertTrue(all(v is not None for v in row.values()))


class HelperTests(ScraperTestCase):
    def test_phone_numbers_are_written_like_represents(self):
        for raw, expected in (("(867) 975-5050", "1 867 975-5050"), ("613-943-4619", "1 613 943-4619"),
                              ("1-800-267-7362", "1 800 267-7362"), ("+1 343.991.5530", "1 343 991-5530"),
                              ("  ", ""), ("613-943-4619 ext. 2", "613-943-4619 ext. 2"), ("TBA", "TBA")):
            with self.subTest(raw=raw):
                self.assertEqual(rc.format_phone(raw), expected)

    def test_province_codes_from_names(self):
        for text, expected in (("Manitoba", "MB"), ("Quebec - De Lorimier", "QC"), ("Québec", "QC"),
                               ("Ontario (Toronto)", "ON"), ("Nova Scotia", "NS"), ("Newfoundland and Labrador", "NL"),
                               ("Northwest Territories", "NT"), ("nunavut", "NU"), ("Ontarioville", ""), ("", "")):
            with self.subTest(text=text):
                self.assertEqual(rc.province_code(text), expected)

    def test_table_rows_keep_their_links(self):
        tables = rc.html_tables('<table><tr><th>Name</th></tr><tr><td><a href="/a">A</a> '
                                '<a href="mailto:a@b.ca">email</a></td></tr></table>')
        keys, rows = rc.find_table(tables, ("name",))
        self.assertEqual(rows, [["A email"]])
        self.assertEqual(rows[0].links, ["/a", "mailto:a@b.ca"])
