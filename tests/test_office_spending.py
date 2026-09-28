import csv
import html
import os
from unittest import mock

from support import FakeResponse, FakeWeb, ScraperTestCase, http_error, load_fixture

import budget_common
import represent_common as rc
import scrape_budget_office_spending as sos

SAMPLE = load_fixture("represent_sample.json")
REPORTS = "https://www.ourcommons.ca/proactivedisclosure/en/members"

# Rows as published in the 2025-2026 fourth-quarter summary (a selection),
# chosen for their quirks: titles ("Hon.", "Right Hon."), negative amounts
# in brackets, an MP listed under two ridings, former MPs' last bills and
# vacant seats. Rachel Bendayan is left out on purpose.
BASE_ROWS = [
    ("Aboultaif, Ziad", "Edmonton Manning", "Conservative", "$102,030.93", "$26,572.52", "$967.09", "$12,209.98"),
    ("Alghabra, Hon. Omar", "Mississauga Centre", "Liberal", "$0.00", "$0.00", "$0.00", "$21.44"),
    ("Atwin, Jenica", "Fredericton", "Liberal", "$862.47", "$0.00", "$0.00", "($200.65)"),
    ("Bains, Parm", "Richmond East—Steveston", "Liberal", "$98,393.00", "$36,974.17", "$1,612.84", "$41,934.44"),
    ("Carney, Right Hon. Mark", "Nepean", "Liberal", "$83,660.95", "$0.00", "$2,927.42", "$68,651.60"),
    ("Chahal, George", "Calgary Skyview", "Liberal", "$0.00", "$2,204.42", "$0.00", "$118.98"),
    ("Gill, Amanpreet", "Calgary Skyview", "Conservative", "$92,731.49", "$86,911.23", "$1,188.18", "$143,046.65"),
    ("Gill, Marilène", "Côte-Nord—Kawawachikamach—Nitassinan", "Bloc Québécois",
     "$116,016.89", "$67,514.60", "$3,056.13", "$51,765.31"),
    ("Hallan, Jasraj", "Calgary East", "Conservative", "$115,204.41", "$69,622.12", "$70.72", "$29,284.88"),
    ("Kronis, Tamara", "Nanaimo—Ladysmith", "Conservative", "$106,940.09", "$28,236.60", "$5,318.83", "$92,915.09"),
    ("Poilievre, Hon. Pierre", "Battle River—Crowfoot", "Conservative", "$57,884.67", "$279.00", "$0.00", "$14,854.35"),
    ("Poilievre, Hon. Pierre", "Carleton", "Conservative", "$0.00", "$0.00", "$0.00", "$205.04"),
    ("Vacant", "Scarborough Southwest", "Liberal", "$32,198.70", "$0.00", "$0.00", "$108.00"),
    ("Vacant", "Terrebonne", "Liberal", "$15,946.01", "$0.00", "$0.00", "$5,133.13"),
]
# Published quarters, oldest first: all of 2025-2026 and the first of 2026-2027.
QUARTERS = [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2027, 1)]
QUARTER_NAMES = {1: "First", 2: "Second", 3: "Third", 4: "Fourth"}


def money(value):
    text = f"${abs(value):,.2f}"
    return f"({text})" if value < 0 else text


def quarter_rows(quarter):
    """The fixture rows for one quarter: each MP's salaries go up $1,000 a
    quarter (so no two reports are the same); Kronis isn't in the newest
    quarter, and by then Scarborough Southwest has a new MP."""
    step = QUARTERS.index(quarter)
    rows = []
    for name, riding, caucus, salaries, *rest in BASE_ROWS:
        if quarter == (2027, 1) and (name.startswith("Kronis") or riding == "Scarborough Southwest"):
            continue
        if name != "Vacant":
            salaries = money(sos.parse_money(salaries) + 1000 * step)
        rows.append((name, riding, caucus, salaries, *rest))
    if quarter == (2027, 1):
        rows.append(("Begum, Doly", "Scarborough Southwest", "Liberal", "$23,302.42", "$0.00", "$0.00", "$12,042.37"))
    return rows


def period_menu():
    """The "Reporting Period" menu, shaped like the live page's."""
    items = []
    for fiscal_year_end, quarter in reversed(QUARTERS):
        start, end = sos.quarter_dates(fiscal_year_end, quarter)
        label = f"{QUARTER_NAMES[quarter]} Quarter Report – From {start:%B} {start.day}, {start.year} to {end:%B} {end.day}, {end.year}"
        items.append(
            f'<li><a href="/proactivedisclosure/en/members/{fiscal_year_end}/{quarter}?summaryId=0000-{quarter}">'
            f'{label}<span class="wb-inv">{fiscal_year_end - 1}-{fiscal_year_end} {QUARTER_NAMES[quarter]} Quarter Report</span></a></li>'
        )
    return ('<nav><h2>Reporting Period</h2><ul>' + "".join(items) + "</ul>"
            '<a href="/proactivedisclosure/en/archive-members/2019/1">Previous reports</a></nav>')


def page(rows, menu=True):
    """A Summary of Expenditures page: the menu, then the table (header
    footnote marks, links around names, cells spread over several lines)."""
    body = "".join(
        "<tr>\n  <td>\n    <a href=\"/proactivedisclosure/en/members/2026/4/abc\">" + html.escape(name) + "</a>\n  </td>\n"
        + "".join(f"  <td class=\"text-right\">\n    {html.escape(cell)}\n  </td>\n" for cell in cells)
        + "</tr>\n"
        for name, *cells in rows
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><title>Members – Summary of Expenditures</title></head><body>"
        + (period_menu() if menu else "")
        + '<a href="/proactivedisclosure/en/members/csv">CSV</a>'
        '<table class="table"><thead><tr><th scope="col">Name</th><th scope="col">Constituency</th>'
        '<th scope="col">Caucus</th><th scope="col">Salaries <sup>**</sup></th><th scope="col">Travel <sup>**</sup></th>'
        '<th scope="col">Hospitality <sup>**</sup></th><th scope="col">Contracts <sup>**</sup></th></tr></thead>'
        f"<tbody>\n{body}</tbody></table><p>** Amounts are expenses processed in the quarter.</p></body></html>"
    )


class FakeCommons:
    def __init__(self):
        self.pages = {f"{REPORTS}/{y}/{q}": page(quarter_rows((y, q))) for y, q in QUARTERS}
        self.pages[REPORTS] = page(quarter_rows(QUARTERS[-1]))

    def handle(self, url):
        text = self.pages.get(url)
        return FakeResponse(text) if text is not None else http_error(url, 404)

    def install(self, web):
        web.route(REPORTS, self.handle)
        return self


def poilievre():
    """A master.xlsx row for an MP who moved ridings (Carleton to Battle
    River-Crowfoot, after a by-election)."""
    rep = dict(SAMPLE["representatives"][3], name="Pierre Poilievre", first_name="Pierre", last_name="Poilievre",
               district_name="Battle River—Crowfoot", url="https://www.ourcommons.ca/Members/en/pierre-poilievre(25524)")
    return rc.flatten_representative(rep, "federal")


class OfficeSpendingTestCase(ScraperTestCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb()
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)
        self.commons = FakeCommons().install(self.web)

    def fill_master(self):
        rows = [rc.flatten_representative(r, "federal") for r in SAMPLE["representatives"]
                if r["representative_set_name"] == "House of Commons"]
        rc.upsert_master(self.master_path, rows + [poilievre()], "federal")

    def spending_tab(self):
        return {(r["Name"], r["Constituency"]): r
                for r in self.read_sheet(budget_common.budget_workbook_path(), sos.SHEET_NAME)}

    def by_name(self):
        return {name: row for (name, _), row in self.spending_tab().items()}


class RunTests(OfficeSpendingTestCase):
    def test_latest_four_quarters_per_mp(self):
        self.fill_master()
        self.assertEqual(sos.main([]), 0)
        rows = self.by_name()
        bains = rows["Parm Bains"]
        # 2025-2026 Q2-Q4 and 2026-2027 Q1: four quarters, with $1,000-a-quarter raises.
        self.assertEqual(bains["Salaries"], round(4 * 98393.00 + (1 + 2 + 3 + 4) * 1000, 2))
        self.assertEqual(bains["Contracts"], round(4 * 41934.44, 2))
        self.assertEqual(bains["Total"], round(bains["Salaries"] + 4 * (36974.17 + 1612.84 + 41934.44), 2))
        self.assertEqual((bains["Quarters"], bains["Match Status"], bains["Province Territory"]), (4, "matched", "BC"))
        self.assertEqual(bains["Caucus"], "Liberal")
        self.assertEqual(rows["Tamara Kronis"]["Quarters"], 3)
        self.assertEqual(rows["Amanpreet S. Gill"]["Match Status"], "matched (first + last name only -- check)")
        self.assertEqual(rows["Rachel Bendayan"]["Match Status"], sos.NO_SPENDING_STATUS)
        self.assertEqual(rows["Rachel Bendayan"]["Constituency"], "Outremont")
        self.assertIsNone(rows["Rachel Bendayan"]["Total"])

    def test_an_mp_listed_under_two_ridings_is_one_row(self):
        self.fill_master()
        sos.main([])
        pierre = self.by_name()["Pierre Poilievre"]
        self.assertEqual(pierre["Constituency"], "Battle River—Crowfoot; Carleton")
        self.assertEqual(pierre["Match Status"], "matched")
        self.assertEqual(pierre["Contracts"], round(4 * (14854.35 + 205.04), 2))

    def test_former_mps_and_vacant_seats_are_kept(self):
        self.fill_master()
        sos.main([])
        rows = self.spending_tab()
        self.assertEqual(rows[("Omar Alghabra", "Mississauga Centre")]["Match Status"], sos.NOT_IN_MASTER_STATUS)
        self.assertEqual(rows[("Mark Carney", "Nepean")]["Match Status"], sos.NOT_IN_MASTER_STATUS)
        self.assertEqual(rows[("George Chahal", "Calgary Skyview")]["Travel"], round(4 * 2204.42, 2))
        # One row per vacant seat, never merged into one "Vacant" person.
        self.assertEqual(rows[("Vacant", "Terrebonne")]["Match Status"], sos.VACANT_STATUS)
        self.assertEqual(rows[("Vacant", "Scarborough Southwest")]["Quarters"], 3)
        self.assertEqual(rows[("Doly Begum", "Scarborough Southwest")]["Quarters"], 1)
        # Corrections are negative amounts, and stay negative.
        self.assertEqual(rows[("Jenica Atwin", "Fredericton")]["Contracts"], round(4 * -200.65, 2))

    def test_tab_order_notes_and_sorting(self):
        self.fill_master()
        sos.main([])
        path = budget_common.budget_workbook_path()
        self.assertEqual(self.sheet_names(path), [sos.SHEET_NAME, "Notes"])
        totals = [r["Total"] for r in self.read_sheet(path, sos.SHEET_NAME) if r["Total"] is not None]
        self.assertEqual(totals, sorted(totals, reverse=True))
        notes = {r["Category"]: r["Scope / Notes"] for r in self.read_sheet(path, "Notes")}
        self.assertIn("July 1, 2025 to June 30, 2026 (2025-2026 Q2, Q3, Q4; 2026-2027 Q1)", notes[sos.SHEET_NAME])
        self.assertIn("not the MP's own pay", notes[sos.SHEET_NAME])

    def test_quarterly_csv_keeps_every_published_row(self):
        sos.main([])
        with open(os.path.join(self.tmp, "federal", "mp_office_spending_by_quarter.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), sum(len(quarter_rows(q)) for q in QUARTERS[1:]))
        self.assertEqual(list(rows[0]), sos.QUARTER_CSV_FIELDS)
        begum = next(r for r in rows if r["name"] == "Begum, Doly")
        self.assertEqual((begum["fiscal_year"], begum["quarter"], begum["period_start"], begum["period_end"]),
                         ("2026-2027", "1", "2026-04-01", "2026-06-30"))
        self.assertEqual(begum["total"], "35344.79")
        self.assertIn("Poilievre, Hon. Pierre", {r["name"] for r in rows})  # exactly as published

    def test_fiscal_year_and_quarter_count(self):
        self.fill_master()
        self.assertEqual(sos.main(["--fiscal-year", "2025-26"]), 0)
        self.assertEqual(self.by_name()["Parm Bains"]["Salaries"], round(4 * 98393.00 + (0 + 1 + 2 + 3) * 1000, 2))
        self.assertEqual(self.web.count("/members/2027/1"), 0)

        self.assertEqual(sos.main(["--quarters", "9"]), 0)
        self.assertIn("Only 5 quarter(s) are published -- using all of them.", self.output.getvalue())

        self.assertEqual(sos.main(["--quarters", "1"]), 0)
        rows = self.by_name()
        self.assertEqual(rows["Parm Bains"]["Salaries"], 98393.00 + 4 * 1000)
        self.assertEqual(rows["Tamara Kronis"]["Match Status"], sos.NO_SPENDING_STATUS)

    def test_unpublished_fiscal_year(self):
        self.assertEqual(sos.main(["--fiscal-year", "2023-2024"]), 1)
        self.assertIn("No reports are published for 2023-2024. Published fiscal years: 2025-2026, 2026-2027.",
                      self.output.getvalue())
        self.assertFalse(os.path.exists(budget_common.budget_workbook_path()))


class FailureTests(OfficeSpendingTestCase):
    def test_house_of_commons_down(self):
        self.web.route(REPORTS, lambda url: OSError("Name or service not known"))
        self.assertEqual(sos.main([]), 1)
        self.assertIn("Couldn't reach www.ourcommons.ca", self.output.getvalue())
        self.assertFalse(os.path.exists(budget_common.budget_workbook_path()))

    def test_changed_page_layout_stops_the_run(self):
        self.commons.pages[f"{REPORTS}/2026/4"] = "<html><body><p>Coming soon</p></body></html>"
        self.assertEqual(sos.main([]), 1)
        self.assertIn("no expenditures table", self.output.getvalue())
        self.assertFalse(os.path.exists(budget_common.budget_workbook_path()))

    def test_the_same_report_twice_is_not_double_counted(self):
        self.commons.pages[f"{REPORTS}/2026/3"] = self.commons.pages[f"{REPORTS}/2026/4"]
        self.assertEqual(sos.main([]), 1)
        self.assertIn("returned the same report for 2025-2026 Q3 and 2025-2026 Q4", self.output.getvalue())

    def test_unreadable_amount_stops_the_run(self):
        self.commons.pages[f"{REPORTS}/2026/4"] = page([("Bains, Parm", "Richmond East—Steveston", "Liberal",
                                                         "$98,393.00", "N/A", "$0.00", "$0.00")])
        self.assertEqual(sos.main([]), 1)
        self.assertIn("unexpected amount 'N/A' for Bains, Parm", self.output.getvalue())

    def test_missing_period_menu(self):
        self.commons.pages[REPORTS] = page(quarter_rows(QUARTERS[-1]), menu=False)
        self.assertEqual(sos.main([]), 1)
        self.assertIn("Couldn't find the list of quarterly reports", self.output.getvalue())


class ParsingTests(ScraperTestCase):
    def test_parse_money(self):
        cases = {"$102,030.93": 102030.93, "($200.65)": -200.65, "($1,976.73)": -1976.73, "$0.00": 0.0,
                 "-$5.00": -5.0, "": 0.0, "—": 0.0, " $1,304.00 ": 1304.0, "$12.50*": 12.5}
        for text, value in cases.items():
            self.assertEqual(sos.parse_money(text), value, text)
        for text in ("N/A", "$1.2.3", "Salaries $5.00"):
            with self.assertRaises(ValueError):
                sos.parse_money(text)

    def test_split_report_name(self):
        cases = {
            "Bains, Parm": ("Parm", "Bains"),
            "Bendayan, Hon. Rachel": ("Rachel", "Bendayan"),
            "Carney, Right Hon. Mark": ("Mark", "Carney"),
            "Sgro, Hon. Judy A.": ("Judy A.", "Sgro"),
            "Martinez Ferrada, Hon. Soraya": ("Soraya", "Martinez Ferrada"),
            "d'Entremont, Chris": ("Chris", "d'Entremont"),
            "Honey, Honor": ("Honor", "Honey"),  # not a title
            "Vacant": ("", "Vacant"),
        }
        for value, expected in cases.items():
            self.assertEqual(sos.split_report_name(value), expected, value)

    def test_parse_summary(self):
        rows = sos.parse_summary(page(quarter_rows((2026, 4))))
        self.assertEqual(len(rows), len(BASE_ROWS))
        atwin = next(r for r in rows if r["name"] == "Atwin, Jenica")
        self.assertEqual(atwin, {"name": "Atwin, Jenica", "constituency": "Fredericton", "caucus": "Liberal",
                                 "salaries": 3862.47, "travel": 0.0, "hospitality": 0.0, "contracts": -200.65})
        with self.assertRaises(ValueError):
            sos.parse_summary("<table><tr><td>Name</td><td>Riding</td></tr></table>")

    def test_available_quarters(self):
        self.assertEqual(sos.available_quarters(page([])), sorted(QUARTERS, reverse=True))
        # Links to one MP's report within a quarter, or to the old archive, aren't quarters.
        self.assertEqual(sos.available_quarters(page(quarter_rows((2026, 4)), menu=False)), [])
        # A drop-down menu instead of links works too.
        menu = ('<select><option value="/proactivedisclosure/en/members/2026/4">Fourth Quarter Report</option>'
                "<option value='/proactivedisclosure/en/members/2026/3?summaryId=x'>Third Quarter Report</option>"
                '</select><div data-url="https://www.ourcommons.ca/proactivedisclosure/en/members/2026/2/"></div>')
        self.assertEqual(sos.available_quarters(menu), [(2026, 4), (2026, 3), (2026, 2)])
        # Without usable links, the menu's labels are enough.
        labels = "<li><span>2025-2026</span>\n Fourth Quarter Report</li><li>2024-2025 First Quarter Report</li>"
        self.assertEqual(sos.available_quarters(labels), [(2026, 4), (2025, 1)])
        self.assertEqual(sos.available_quarters("<p>nothing here</p>"), [])

    def test_quarters_and_fiscal_years(self):
        self.assertEqual([str(d) for d in sos.quarter_dates(2026, 4)], ["2026-01-01", "2026-03-31"])
        self.assertEqual([str(d) for d in sos.quarter_dates(2026, 1)], ["2025-04-01", "2025-06-30"])
        self.assertEqual(sos.describe_quarters([(2026, 4), (2026, 3)]),
                         "October 1, 2025 to March 31, 2026 (2025-2026 Q3, Q4)")
        for value in ("2025-2026", "2025-26", "2025/2026"):
            self.assertEqual(sos._fiscal_year_arg(value), 2026)
        for value in ("2025", "2025-2027", "2025-27", "twenty"):
            with self.assertRaises(Exception):
                sos._fiscal_year_arg(value)
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                sos.parse_args(["--fiscal-year", "2025-2026", "--quarters", "2"])

    def test_riding_differs_is_flagged(self):
        entries = sos.spending_by_person([((2025, 4), {"name": "Poilievre, Hon. Pierre", "constituency": "Carleton",
                                                      "caucus": "Conservative", "salaries": 1.0, "travel": 0.0,
                                                      "hospitality": 0.0, "contracts": 0.0})])
        rows = sos.build_spending_rows([{"name": "Pierre Poilievre", "first_name": "Pierre", "last_name": "Poilievre",
                                         "district_name": "Battle River—Crowfoot", "province_territory": "AB"}],
                                       entries)
        self.assertEqual(rows[0]["match_status"], "matched; riding differs -- check")
        self.assertEqual((rows[0]["constituency"], rows[0]["total"]), ("Carleton", 1.0))
