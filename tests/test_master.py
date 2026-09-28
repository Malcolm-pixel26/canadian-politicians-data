import os

from support import ScraperTestCase, load_fixture

import represent_common as rc

SAMPLE = load_fixture("represent_sample.json")

# master.xlsx's columns as written by the v4 scripts (before this rewrite).
V4_MASTER_FIELDS = rc.CSV_FIELDS[:16] + [
    "first_elected", "committees", "openparliament_url", "first_seen", "last_seen", "status",
]


def rep(name):
    return next(r for r in SAMPLE["representatives"] if r["name"] == name)


def mp(who="Jasraj Hallan", **overrides):
    row = rc.flatten_representative(rep(who), "federal")
    row.update(overrides)
    return row


def councillor(who="Bill Dennis", **overrides):
    row = rc.flatten_representative(rep(who), "local", province="ON")
    row.update(overrides)
    return row


class MasterTests(ScraperTestCase):
    def upsert(self, rows, category="local", **kwargs):
        return rc.upsert_master(self.master_path, rows, category, **kwargs)

    def tab(self, category="local"):
        rows, _ = rc._load_sheet(self.master_path, rc.CATEGORY_TO_MASTER_SHEET[category], rc.MASTER_FIELDS)
        return {r["name"]: r for r in rows}

    def kinds(self, changes):
        return sorted((c["change_type"], c["field"]) for c in changes)

    # --- adds / updates ---------------------------------------------------

    def test_first_run_adds_everyone(self):
        changes = self.upsert([councillor(), councillor("Kevin Marriott")])
        self.assertEqual(self.kinds(changes), [("added", ""), ("added", "")])
        row = self.tab()["Bill Dennis"]
        self.assertEqual((row["status"], row["record_id"]), ("active", "Lambton County Council|Bill Dennis"))

    def test_rerun_without_changes_records_nothing(self):
        self.upsert([councillor()])
        self.assertEqual(self.upsert([councillor()]), [])

    def test_changed_field_is_logged(self):
        self.upsert([councillor()])
        changes = self.upsert([councillor(phone="1 519 000-0000")])
        self.assertEqual(self.kinds(changes), [("updated", "phone")])
        self.assertEqual(changes[0]["old_value"], "1 519 332-0330")

    def test_blanked_field_is_logged(self):
        # v4 skipped blank new values, so a removed email stayed forever.
        self.upsert([mp()], category="federal")
        changes = self.upsert([mp(email="")], category="federal")
        self.assertEqual(self.kinds(changes), [("updated", "email")])
        self.assertEqual(self.tab("federal")["Jasraj Hallan"]["email"], "")

    # --- identity -------------------------------------------------------

    def test_council_starting_to_publish_urls_is_not_churn(self):
        # v4 keyed on `url`, so this logged "removed" + "added" and reset first_seen.
        self.upsert([councillor()])
        first_seen = self.tab()["Bill Dennis"]["first_seen"]
        changes = self.upsert([councillor(url="https://www.lambtononline.ca/councillors/bill-dennis")])
        self.assertEqual(self.kinds(changes), [("updated", "url")])
        row = self.tab()["Bill Dennis"]
        self.assertEqual((row["first_seen"], row["record_id"]), (first_seen, "Lambton County Council|Bill Dennis"))

    def test_site_redesign_changes_url_not_identity(self):
        self.upsert([mp()], category="federal")
        changes = self.upsert([mp(url="https://www.ourcommons.ca/members/new-site/105630")], category="federal")
        self.assertEqual(self.kinds(changes), [("updated", "url")])

    def test_name_fix_is_matched_by_url(self):
        self.upsert([mp()], category="federal")
        changes = self.upsert([mp(name="Jasraj Singh Hallan", first_name="Jasraj Singh")], category="federal")
        self.assertEqual(self.kinds(changes), [("updated", "first_name"), ("updated", "name")])

    def test_first_name_variant_is_matched_by_seat(self):
        self.upsert([councillor()])
        changes = self.upsert([councillor(name="William Dennis", first_name="William")])
        self.assertEqual(self.kinds(changes), [("updated", "first_name"), ("updated", "name")])
        self.assertEqual(changes[0]["key"], "Lambton County Council|Bill Dennis")

    def test_seat_match_needs_an_unambiguous_candidate(self):
        # Two Lambton councillors named Dennis in the same multi-member district:
        # a renamed one can't be told apart by seat, so it's a new person.
        twin = councillor(name="Ann Dennis", first_name="Ann")
        self.upsert([councillor(), twin])
        changes = self.upsert([councillor(name="William Dennis", first_name="William"), twin])
        self.assertEqual(self.kinds(changes), [("added", ""), ("removed", "")])

    def test_shared_url_does_not_pair_the_wrong_people(self):
        shared = "https://www.lambtononline.ca/en/county-government/councillors.aspx"
        a, b = councillor(url=shared), councillor("Kevin Marriott", url=shared)
        self.upsert([a, b])
        self.assertEqual(self.upsert([b, a]), [])
        self.assertEqual(len(self.tab()), 2)

    def test_same_person_on_two_councils_keeps_two_rows(self):
        mayor = councillor("Joe Nowak", url="https://example.org/mayor")
        regional = dict(mayor, representative_set="Waterloo Regional Council")
        self.upsert([mayor, regional])
        self.assertEqual(self.upsert([mayor, regional]), [])
        rows, _ = rc._load_sheet(self.master_path, "Local", rc.MASTER_FIELDS)
        self.assertEqual(len(rows), 2)

    # --- removals -------------------------------------------------------

    def test_departure_and_return(self):
        self.upsert([councillor(), councillor("Kevin Marriott")])
        changes = self.upsert([councillor()])
        self.assertEqual(self.kinds(changes), [("removed", "")])
        self.assertEqual(self.tab()["Kevin Marriott"]["status"], "removed")

        changes = self.upsert([councillor(), councillor("Kevin Marriott")])
        self.assertEqual(self.kinds(changes), [("reactivated", "")])
        row = self.tab()["Kevin Marriott"]
        self.assertEqual((row["status"], row["record_id"]), ("active", "Lambton County Council|Kevin Marriott"))

    def test_filtered_run_leaves_other_councils_alone(self):
        toronto = rc.flatten_representative(rep("Nick Mantas"), "local", province="ON")
        self.upsert([councillor(), toronto])
        changes = self.upsert([toronto], attempted_sets=["Toronto City Council"], category_complete=False)
        self.assertEqual(changes, [])
        self.assertEqual(self.tab()["Bill Dennis"]["status"], "active")

    def test_council_that_comes_back_empty_is_not_wiped(self):
        toronto = rc.flatten_representative(rep("Nick Mantas"), "local", province="ON")
        self.upsert([councillor(), councillor("Kevin Marriott"), toronto])
        changes = self.upsert([toronto], attempted_sets=["Toronto City Council", "Lambton County Council"],
                              category_complete=True)
        self.assertEqual(changes, [])
        self.assertIn("Lambton County Council came back empty", self.output.getvalue())

    def test_vanished_council_is_removed_on_a_complete_run(self):
        toronto = rc.flatten_representative(rep("Nick Mantas"), "local", province="ON")
        self.upsert([councillor(), toronto])
        changes = self.upsert([toronto], attempted_sets=["Toronto City Council"], category_complete=True)
        self.assertEqual(self.kinds(changes), [("removed", "")])

    def test_empty_batch_marks_nobody_removed(self):
        self.upsert([councillor()])
        self.assertEqual(self.upsert([]), [])
        self.assertEqual(self.tab()["Bill Dennis"]["status"], "active")

    # --- federal enrichment ----------------------------------------------

    def test_enrichment_survives_a_no_enrich_run(self):
        enriched = mp(first_elected="2019-10-21", committees="Public Accounts (Chair)")
        self.upsert([enriched], category="federal")
        self.assertEqual(self.upsert([mp()], category="federal"), [])
        self.assertEqual(self.tab("federal")["Jasraj Hallan"]["committees"], "Public Accounts (Chair)")

    def test_fresh_enrichment_can_clear_a_value(self):
        self.upsert([mp(committees="Public Accounts (Chair)")], category="federal")
        fresh = mp(committees="", **{rc.FRESH_FIELDS_KEY: {"committees"}})
        changes = self.upsert([fresh], category="federal")
        self.assertEqual(self.kinds(changes), [("updated", "committees")])

    # --- the workbook itself ------------------------------------------------

    def test_reads_a_v4_workbook_by_header(self):
        from openpyxl import Workbook

        old_mp = {f: v for f, v in mp().items() if f in V4_MASTER_FIELDS}
        old_mp.update(first_elected="2019-10-21", first_seen="2025-01-01 00:00:00 UTC",
                      last_seen="2025-01-01 00:00:00 UTC", status="active")
        wb = Workbook()
        ws = wb.active
        ws.title = "Federal"
        ws.append([f.replace("_", " ").title() for f in V4_MASTER_FIELDS])
        ws.append([old_mp.get(f, "") for f in V4_MASTER_FIELDS])
        log = wb.create_sheet("Changelog")
        log.append([f.replace("_", " ").title() for f in rc.CHANGELOG_FIELDS])
        log.append(["2025-01-01 00:00:00 UTC", "federal", old_mp["url"], "Jasraj Hallan", "added", "", "", ""])
        wb.save(self.master_path)

        changes = self.upsert([mp()], category="federal")

        self.assertEqual(changes, [])  # new columns are filled in silently
        row = self.tab("federal")["Jasraj Hallan"]
        self.assertEqual(row["province_territory"], "AB")
        self.assertEqual(row["first_elected"], "2019-10-21")
        self.assertEqual(row["first_seen"], "2025-01-01 00:00:00 UTC")
        self.assertEqual(row["record_id"], old_mp["url"])  # same key as the v4 changelog used
        self.assertEqual(len(rc.read_master_changelog(self.master_path)), 1)

    def test_other_tabs_are_untouched(self):
        self.upsert([councillor()])
        self.upsert([mp()], category="federal")
        self.assertEqual(self.sheet_names(self.master_path), ["Federal", "Local", "Changelog"])
        self.assertEqual(list(self.tab()), ["Bill Dennis"])

    def test_previous_version_is_backed_up(self):
        self.upsert([councillor()])
        self.upsert([councillor(), councillor("Kevin Marriott")])
        backup = os.path.join(self.tmp, "master.backup.xlsx")
        rows, _ = rc._load_sheet(backup, "Local", rc.MASTER_FIELDS)
        self.assertEqual([r["name"] for r in rows], ["Bill Dennis"])
