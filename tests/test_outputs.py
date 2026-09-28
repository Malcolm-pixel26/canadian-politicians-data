import csv
import json
import os
from unittest import mock

from support import ScraperTestCase

import represent_common as rc


class WriterTests(ScraperTestCase):
    def test_csv_ignores_keys_it_was_not_asked_for(self):
        # v4 crashed here (DictWriter extrasaction="raise") on every
        # default federal run, before master.xlsx was ever updated.
        path = os.path.join(self.tmp, "x.csv")
        rc.write_csv(path, [{"name": "Jane Doe", "committees": "Finance", "_fresh_fields": {"x"}}])
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["name"], "Jane Doe")
        self.assertNotIn("committees", rows[0])

    def test_json_drops_internal_keys(self):
        path = os.path.join(self.tmp, "x.json")
        rc.write_json(path, [{"name": "Jane Doe", rc.FRESH_FIELDS_KEY: {"committees"}}])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), [{"name": "Jane Doe"}])

    def test_xlsx_survives_control_characters(self):
        path = os.path.join(self.tmp, "x.xlsx")
        rc.write_xlsx(path, [{"name": "Jane\x0bDoe", "postal_address": "Line 1\nLine 2"}])
        row = self.read_sheet(path, "Representatives")[0]
        self.assertEqual((row["Name"], row["Postal Address"]), ("JaneDoe", "Line 1\nLine 2"))

    def test_xlsx_never_writes_live_formulas(self):
        path = os.path.join(self.tmp, "x.xlsx")
        rc.write_xlsx(path, [{"name": "=HYPERLINK(\"http://evil\")"}])
        from openpyxl import load_workbook

        cell = load_workbook(path)["Representatives"]["C2"]
        self.assertEqual(cell.data_type, "s")

    def test_sheet_names_are_made_valid(self):
        self.assertEqual(rc._sheet_title("Council / Conseil: [Test]"), "Council - Conseil- -Test-")
        self.assertEqual(len(rc._sheet_title("Legislative Assembly of the Northwest Territories")), 31)

    def test_locked_file_is_saved_alongside_instead_of_crashing(self):
        path = os.path.join(self.tmp, "all_local.csv")
        rc.write_csv(path, [{"name": "Old"}])
        real_replace = os.replace

        def locked(src, dst):
            if dst == path:
                raise PermissionError(13, "file is open in Excel")
            return real_replace(src, dst)

        with mock.patch("os.replace", locked):
            written = rc.write_csv(path, [{"name": "New"}])
        self.assertNotEqual(written, path)
        self.assertIn("(unsaved ", written)
        with open(path, encoding="utf-8-sig") as f:
            self.assertIn("Old", f.read())
        with open(written, encoding="utf-8-sig") as f:
            self.assertIn("New", f.read())
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".tmp-")], [])

    def test_output_dir_option(self):
        target = os.path.join(self.tmp, "elsewhere")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rc.OUTPUT_DIR_ENV_VAR, None)
            parser = rc.add_common_args(__import__("argparse").ArgumentParser())
            rc.apply_common_args(parser.parse_args(["--output-dir", target]))
            self.assertEqual(rc.get_master_path(), os.path.join(target, "master.xlsx"))

    def test_default_output_is_desktop(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            os.environ.pop(rc.OUTPUT_DIR_ENV_VAR, None)
            with mock.patch("sys.platform", "linux"):
                self.assertEqual(rc.get_output_root(), os.path.join(self.tmp, "Desktop", "output"))
