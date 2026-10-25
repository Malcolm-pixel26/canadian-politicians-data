import math
import unittest
from pathlib import Path
import tempfile
from openpyxl import Workbook
from public_site.build import build, public_map, public_snapshot


class PublicBuildTests(unittest.TestCase):
    def row(self, **updates):
        return dict({"name": "Test Representative", "province_territory": "ON", "status": "active"}, **updates)

    def test_only_public_fields_are_exported(self):
        payload = public_snapshot({"Federal|one": self.row(password_hash="secret", session_token="secret", notes="private", email="official@example.com")})
        person = payload["people"][0]
        self.assertNotIn("password_hash", person)
        self.assertNotIn("session_token", person)
        self.assertNotIn("notes", person)
        self.assertEqual(person["email"], "official@example.com")
        self.assertEqual(person["category"], "federal")

    def test_ids_are_stable_and_sheet_scoped(self):
        first = public_snapshot({"Federal|one": self.row(), "Local|one": self.row()})
        again = public_snapshot({"Federal|one": self.row()})
        self.assertEqual(len({p["id"] for p in first["people"]}), 2)
        self.assertEqual(next(p["id"] for p in first["people"] if p["category"] == "federal"), again["people"][0]["id"])

    def test_invalid_or_empty_records_fail_the_build(self):
        for rows in ({}, {"Federal|one": self.row(name="")}, {"Federal|one": self.row(status="unknown")},
                     {"Federal|one": self.row(province_territory="XX")}, {"Unknown|one": self.row()}):
            with self.assertRaises(ValueError):
                public_snapshot(rows)

    def test_removed_records_are_preserved(self):
        self.assertEqual(public_snapshot({"Federal|one": self.row(status="removed")})["people"][0]["status"], "removed")

    def test_map_validation_and_property_allowlist(self):
        feature = {"geometry": {"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[0,0]]]},
                   "properties": {"district": "Test district", "html": "<script>bad()</script>"}}
        result = public_map({"type": "FeatureCollection", "features": [feature]})
        self.assertNotIn("html", result["features"][0]["properties"])
        feature["geometry"]["coordinates"][0][1] = [math.nan, 0]
        with self.assertRaises(ValueError):
            public_map({"type": "FeatureCollection", "features": [feature]})

    def test_required_source_group_failure_preserves_existing_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            master = root / 'master.xlsx'
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = 'Federal'
            sheet.append(['record_id', 'name', 'representative_set', 'status'])
            sheet.append(['one', 'Example', 'House of Commons', 'active'])
            workbook.save(master)
            output = root / 'site'
            output.mkdir()
            (output / 'index.html').write_text('previous successful bundle')
            with self.assertRaisesRegex(ValueError, 'Senate of Canada'):
                build(master, None, output, ['House of Commons', 'Senate of Canada'])
            self.assertEqual((output / 'index.html').read_text(), 'previous successful bundle')

    def test_invalid_maps_fail(self):
        for feature in (None, "bad", {"geometry": {"type": "Point", "coordinates": [0,0]}},
                        {"geometry": {"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[2,0]]]}}):
            with self.assertRaises(ValueError):
                public_map({"type": "FeatureCollection", "features": [feature]})


if __name__ == "__main__":
    unittest.main()

