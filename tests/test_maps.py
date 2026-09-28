import json
import math
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from unittest import mock

from support import REPRESENT, FakeWeb, ScraperTestCase, http_error, json_response, sample_represent

import make_maps

FEDERAL_SET = "federal-electoral-districts-2023-representation-order"
KML = "{http://www.opengis.net/kml/2.2}"


def square(lng, lat, size):
    """A one-part MultiPolygon: a square with its south-west corner at (lng, lat)."""
    return {"type": "MultiPolygon", "coordinates": [[[
        [lng, lat], [lng + size, lat], [lng + size, lat + size], [lng, lat + size], [lng, lat]]]]}


def two_parts(first, second):
    return {"type": "MultiPolygon", "coordinates": first["coordinates"] + second["coordinates"]}


# boundary url -> (name, external_id, simple shape) for every boundary the
# sample representatives point to, except Saskatoon's ward 7 (Represent
# has no shape for it), plus one federal riding nobody in the sample holds.
BOUNDARIES = {
    f"/boundaries/{FEDERAL_SET}/59028/": ("Richmond East—Steveston", "59028", square(-123.15, 49.10, 0.1)),
    f"/boundaries/{FEDERAL_SET}/59019/": ("Nanaimo—Ladysmith", "59019", square(-124.0, 49.0, 0.2)),
    f"/boundaries/{FEDERAL_SET}/24053/": ("Outremont", "24053", square(-73.63, 45.50, 0.04)),
    f"/boundaries/{FEDERAL_SET}/48007/": ("Calgary East", "48007", square(-114.0, 51.0, 0.08)),
    f"/boundaries/{FEDERAL_SET}/48014/": ("Calgary Skyview", "48014", square(-114.0, 51.12, 0.08)),
    f"/boundaries/{FEDERAL_SET}/24021/": ("Côte-Nord—Kawawachikamach—Nitassinan", "24021",
                                          two_parts(square(-68.0, 49.5, 2.0), square(-63.0, 49.3, 0.5))),
    f"/boundaries/{FEDERAL_SET}/35080/": ("Ottawa Centre", "35080", square(-75.72, 45.38, 0.05)),
    "/boundaries/ontario-electoral-districts-representation-act-2015/scarborough-centre/":
        ("Scarborough Centre", "90", square(-79.30, 43.74, 0.05)),
    "/boundaries/ontario-electoral-districts-representation-act-2015/haldimand-norfolk/":
        ("Haldimand—Norfolk", "31", square(-80.6, 42.7, 0.5)),
    "/boundaries/toronto-wards-2018/scarborough-agincourt/": ("Scarborough-Agincourt", "22", square(-79.33, 43.78, 0.05)),
    "/boundaries/toronto-wards-2018/humber-river-black-creek/":
        ("Humber River-Black Creek", "7", square(-79.55, 43.74, 0.05)),
    "/boundaries/census-subdivisions/3540040/": ("Huron East", "3540040", square(-81.4, 43.55, 0.15)),
    "/boundaries/census-subdivisions/3540010/": ("Bluewater", "3540010", square(-81.75, 43.4, 0.15)),
    "/boundaries/census-subdivisions/3540005/": ("South Huron", "3540005", square(-81.6, 43.25, 0.15)),
    "/boundaries/census-subdivisions/2466127/": ("Senneville", "2466127", square(-73.97, 45.41, 0.04)),
    "/boundaries/census-subdivisions/3530027/": ("Wellesley", "3530027", square(-80.87, 43.47, 0.26)),
    "/boundaries/senneville-districts/district-2/": ("District 2", "2", square(-73.96, 45.42, 0.01)),
    "/boundaries/wellesley-wards/ward-4/": ("Ward 4", "4", square(-80.75, 43.55, 0.08)),
    "/boundaries/census-divisions/3538/": ("Lambton", "3538", square(-82.5, 42.6, 0.8)),
}


class FakeBoundaries:
    """Serves Represent's boundary endpoints in their live formats: a
    set's listing, all of a set's simple shapes at once (labelled by name
    only, and refused past 350 like Represent), and one boundary's shape."""

    def __init__(self, boundaries, totals=None):
        self.boundaries = dict(boundaries)
        self.totals = {"census-subdivisions": 5162, **(totals or {})}  # the real set is huge

    def in_set(self, set_slug):
        return [(url, info) for url, info in self.boundaries.items() if url.split("/")[2] == set_slug]

    def handle(self, url):
        split = urllib.parse.urlsplit(url)
        parts = [p for p in split.path.split("/") if p]
        if len(parts) == 2:
            members = self.in_set(parts[1])
            if not members:
                return http_error(url, 404)
            limit = int(urllib.parse.parse_qs(split.query).get("limit", ["20"])[0])
            objects = [{"url": u, "name": name, "related": {"boundary_set_url": f"/boundary-sets/{parts[1]}/"},
                        "boundary_set_name": parts[1], "external_id": ext}
                       for u, (name, ext, _) in members][:limit]
            total = self.totals.get(parts[1], len(members))
            return json_response({"objects": objects, "meta": {
                "offset": 0, "limit": limit, "total_count": total, "previous": None,
                "next": f"/boundaries/{parts[1]}/?limit={limit}&offset={limit}" if total > limit else None}})
        if len(parts) == 3 and parts[2] == "simple_shape":
            members = self.in_set(parts[1])
            if not members:
                return http_error(url, 404)
            if self.totals.get(parts[1], len(members)) > 350:
                return http_error(url, 400)
            return json_response({"objects": [{"name": name, "simple_shape": shape}
                                              for _, (name, _, shape) in members]})
        if len(parts) == 4 and parts[3] == "simple_shape":
            info = self.boundaries.get(f"/boundaries/{parts[1]}/{parts[2]}/")
            return json_response(info[2]) if info else http_error(url, 404)
        return http_error(url, 404)

    def install(self, web):
        web.route(f"{REPRESENT}/boundaries/", self.handle)
        return self


class MapTestCase(ScraperTestCase):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb()
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)
        self.represent = sample_represent().install(self.web)
        self.boundaries = FakeBoundaries(BOUNDARIES).install(self.web)

    def map_file(self, name):
        return os.path.join(self.tmp, "maps", name)

    def load_geojson(self, stem):
        with open(self.map_file(f"{stem}.geojson"), encoding="utf-8") as f:
            return json.load(f)

    def properties(self, stem):
        return {f["properties"]["district"]: f["properties"] for f in self.load_geojson(stem)["features"]}

    def page_data(self, stem):
        """The data embedded in an HTML map, decoded like the browser would."""
        with open(self.map_file(f"{stem}.html"), encoding="utf-8") as f:
            page = f.read()
        match = re.search(r"var MAP_DATA = (.*);\n</script>", page)
        return page, json.loads(match.group(1))

    def boundary_requests(self):
        return [url for _, url in self.web.requests if "/boundaries/" in url]


class FederalMapTests(MapTestCase):
    def test_mps_are_joined_to_their_ridings(self):
        self.assertEqual(make_maps.main(["--level", "federal"]), 0)
        ridings = self.properties("federal_map")
        self.assertEqual(len(ridings), 6)  # Ottawa Centre has no MP in the sample
        bains = ridings["Richmond East—Steveston"]
        self.assertEqual((bains["representatives"], bains["party"], bains["elected_office"]), ("Parm Bains", "Liberal", "MP"))
        self.assertEqual((bains["province_territory"], bains["external_id"], bains["seats"]), ("BC", "59028", 1))
        self.assertEqual(bains["boundary_url"], f"{REPRESENT}/boundaries/{FEDERAL_SET}/59028/")
        self.assertEqual(bains["fill"], "#D71920")
        self.assertEqual(ridings["Calgary East"]["fill"], "#1A4782")
        self.assertEqual(ridings["Outremont"]["representatives"], "Rachel Bendayan")
        # Six ridings: one listing request and one for all the shapes, not six.
        self.assertEqual(len(self.boundary_requests()), 2)

    def test_geojson_is_valid_for_qgis(self):
        make_maps.main(["--level", "federal"])
        geo = self.load_geojson("federal_map")
        self.assertEqual((geo["type"], geo["name"]), ("FeatureCollection", "federal_map"))
        for feature in geo["features"]:
            self.assertEqual(feature["geometry"]["type"], "MultiPolygon")
            self.assertFalse([k for k in feature if k.startswith("_")])
            for polygon in feature["geometry"]["coordinates"]:
                for ring in polygon:
                    self.assertEqual(ring[0], ring[-1])
                    self.assertTrue(all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in ring))
        # Bigger districts come first, so they're drawn under smaller ones.
        self.assertEqual(geo["features"][0]["properties"]["district"],
                         "Côte-Nord—Kawawachikamach—Nitassinan")

    def test_province_filter(self):
        self.assertEqual(make_maps.main(["--level", "federal", "--province", "AB"]), 0)
        self.assertEqual(set(self.properties("federal_map_AB")), {"Calgary East", "Calgary Skyview"})
        _, data = self.page_data("federal_map_AB")
        self.assertEqual(data["title"], "Federal ridings — Alberta")

    def test_legend_counts_seats_per_party(self):
        make_maps.main(["--level", "federal"])
        _, data = self.page_data("federal_map")
        legend = [(item["label"], item["count"]) for item in data["legend"]]
        self.assertEqual(legend, [("Conservative", 3), ("Liberal", 2), ("Bloc Québécois", 1)])
        self.assertEqual(data["legend"][2]["colour"], "#33B2CC")


class ShapeCacheTests(MapTestCase):
    def test_shapes_are_cached_and_refresh_refetches(self):
        make_maps.main(["--level", "federal"])
        self.assertEqual(len(self.boundary_requests()), 2)
        cache = os.path.join(self.tmp, "_cache", "boundaries", f"{FEDERAL_SET}.json")
        with open(cache, encoding="utf-8") as f:
            self.assertIn("35080", json.load(f))  # the whole set is kept, not just what was mapped

        self.web.requests.clear()
        self.assertEqual(make_maps.main(["--level", "federal", "--province", "BC"]), 0)
        self.assertEqual(self.boundary_requests(), [])
        self.assertEqual(set(self.properties("federal_map_BC")), {"Richmond East—Steveston", "Nanaimo—Ladysmith"})

        make_maps.main(["--level", "federal", "--refresh"])
        self.assertEqual(len(self.boundary_requests()), 2)

    def test_old_shapes_are_downloaded_again(self):
        make_maps.main(["--level", "federal"])
        self.web.requests.clear()
        later = time.time() + make_maps.SHAPE_CACHE_MAX_AGE_SECONDS + 60
        with mock.patch("time.time", return_value=later):
            make_maps.main(["--level", "federal"])
        self.assertEqual(len(self.boundary_requests()), 2)

    def test_a_failed_refresh_falls_back_to_the_cache(self):
        make_maps.main(["--level", "federal"])
        self.web.route(f"{REPRESENT}/boundaries/", lambda url: http_error(url, 503))
        self.assertEqual(make_maps.main(["--level", "federal", "--refresh"]), 1)  # reported...
        self.assertEqual(len(self.properties("federal_map")), 6)                 # ...but the map is complete
        self.assertIn("couldn't download 6 district shape(s)", self.output.getvalue())


    def test_boundaries_with_the_same_name_are_fetched_one_at_a_time(self):
        # Bulk shapes are labelled by name only, so two "Ward 1"s can't be told apart.
        self.boundaries.boundaries.update({
            "/boundaries/dup-wards/ward-1a/": ("Ward 1", "1a", square(0, 0, 1)),
            "/boundaries/dup-wards/ward-1b/": ("Ward 1", "1b", square(2, 0, 1)),
            "/boundaries/dup-wards/ward-2/": ("Ward 2", "2", square(4, 0, 1)),
        })
        urls = ["/boundaries/dup-wards/ward-1a/", "/boundaries/dup-wards/ward-1b/", "/boundaries/dup-wards/ward-2/"]
        shapes, failed = make_maps.fetch_boundary_shapes(urls)
        self.assertEqual((sorted(shapes), failed), (urls, []))
        self.assertEqual(shapes["/boundaries/dup-wards/ward-1b/"]["shape"], square(2, 0, 1))
        self.assertEqual(shapes["/boundaries/dup-wards/ward-2/"]["name"], "Ward 2")
        singles = [u for u in self.boundary_requests() if u.endswith("/simple_shape") and "/ward-" in u]
        self.assertEqual(len(singles), 2)


class LocalMapTests(MapTestCase):
    def test_councils_mayors_and_shared_districts(self):
        self.assertEqual(make_maps.main(["--level", "local"]), 0)
        districts = self.properties("local_map")
        lambton = districts["Lambton"]
        self.assertEqual(lambton["representatives"], "Kevin Marriott (Warden); Bill Dennis (Councillor)")
        self.assertEqual((lambton["seats"], lambton["elected_office"]), (2, "Warden; Councillor"))
        self.assertEqual(districts["Senneville"]["representatives"], "Julie Brisebois")
        self.assertEqual(districts["District 2"]["representatives"], "Pierre Matuszewski")
        # Colour tells councils apart: same council, same colour.
        self.assertEqual(districts["Senneville"]["fill"], districts["District 2"]["fill"])
        self.assertNotIn("Ward 7", districts)

        _, data = self.page_data("local_map")
        self.assertEqual(data["legend"], [])
        self.assertEqual(data["unplaced"], ["Holly Kelleher (Councillor, Ward 7, Saskatoon City Council)"])
        self.assertIn("Not on the map (1):", data["notes"])
        by_name = {f["properties"]["district"]: f["properties"] for f in data["features"]}
        self.assertEqual([r["name"] for r in by_name["Lambton"]["reps"]], ["Kevin Marriott", "Bill Dennis"])

    def test_census_subdivisions_are_fetched_one_at_a_time(self):
        make_maps.main(["--level", "local"])
        requests = self.boundary_requests()
        subdivisions = [u for u in requests if "/census-subdivisions/" in u]
        # One listing shows the set is far too big to download whole, then
        # only the five subdivisions that are needed.
        self.assertEqual(len(subdivisions), 6)
        self.assertFalse(any(u.endswith("/census-subdivisions/simple_shape") for u in requests))
        self.assertIn("Represent has no shape for /boundaries/saskatoon-wards/ward-7/", self.output.getvalue())

    def test_filter_skips_levels_with_nothing_to_map(self):
        self.assertEqual(make_maps.main(["--filter", "toronto"]), 0)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "maps"))),
                         ["local_map_toronto.geojson", "local_map_toronto.html", "local_map_toronto.kml"])
        self.assertEqual(make_maps.main(["--filter", "nowhere"]), 1)


class FailureTests(MapTestCase):
    def test_unreachable_shapes_are_reported_and_an_old_map_kept(self):
        make_maps.main(["--level", "provincial"])
        with open(self.map_file("provincial_territorial_map.geojson"), encoding="utf-8") as f:
            before = f.read()
        self.web.route(f"{REPRESENT}/boundaries/", lambda url: OSError("network is unreachable"))
        self.assertEqual(make_maps.main(["--level", "provincial", "--refresh"]), 1)
        with open(self.map_file("provincial_territorial_map.geojson"), encoding="utf-8") as f:
            self.assertEqual(f.read(), before)  # refresh failed, so the cached shapes were used

        for name in os.listdir(os.path.join(self.tmp, "_cache", "boundaries")):
            os.remove(os.path.join(self.tmp, "_cache", "boundaries", name))
        self.assertEqual(make_maps.main(["--level", "provincial"]), 1)
        self.assertIn("no map written", self.output.getvalue())
        with open(self.map_file("provincial_territorial_map.geojson"), encoding="utf-8") as f:
            self.assertEqual(f.read(), before)  # nothing to draw -> the old map is left alone

    def test_represent_down_gives_a_clear_message(self):
        self.web.route(REPRESENT, lambda url: OSError("Name or service not known"))
        self.assertEqual(make_maps.main([]), 1)
        self.assertIn("Couldn't reach represent.opennorth.ca", self.output.getvalue())

    def test_breaker_stops_after_repeated_failures(self):
        self.web.route(f"{REPRESENT}/boundaries/", lambda url: http_error(url, 500))
        self.assertEqual(make_maps.main(["--level", "local"]), 1)
        # Bulk attempt for the census subdivisions, then three districts (with
        # retries each) before giving up on the rest.
        tried = {u.split("?")[0] for u in self.boundary_requests()}
        self.assertLessEqual(len(tried), 1 + make_maps.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("Represent seems to be unreachable", self.output.getvalue())


class KmlTests(MapTestCase):
    def kml(self, stem="federal_map"):
        return ET.parse(self.map_file(f"{stem}.kml")).getroot()

    def test_kml_is_shaped_for_google_my_maps(self):
        make_maps.main(["--level", "federal"])
        root = self.kml()
        placemarks = root.findall(f".//{KML}Placemark")
        # One placemark per polygon: the Cote-Nord riding comes in two pieces.
        self.assertEqual(len(placemarks), 7)
        self.assertEqual(root.findall(f".//{KML}MultiGeometry") + root.findall(f".//{KML}Folder"), [])
        names = [p.findtext(f"{KML}name") for p in placemarks]
        self.assertEqual(names.count("Côte-Nord—Kawawachikamach—Nitassinan"), 2)
        bains = next(p for p in placemarks if p.findtext(f"{KML}name") == "Richmond East—Steveston")
        data = {d.get("name"): d.findtext(f"{KML}value") for d in bains.iter(f"{KML}Data")}
        self.assertEqual((data["representatives"], data["party"], data["seats"]), ("Parm Bains", "Liberal", "1"))
        self.assertIn("Parm Bains -- MP, Liberal", bains.findtext(f"{KML}description"))
        style_id = bains.findtext(f"{KML}styleUrl").lstrip("#")
        style = root.find(f".//{KML}Style[@id='{style_id}']")
        self.assertEqual(style.findtext(f".//{KML}PolyStyle/{KML}color"), "802019d7")  # Liberal red, aabbggrr
        ring = bains.findtext(f".//{KML}coordinates").split()
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(ring[0], "-123.15000,49.10000")

    def test_text_is_escaped(self):
        self.represent.reps = [dict(r) for r in self.represent.reps]
        for rep in self.represent.reps:
            if rep["name"] == "Jasraj Hallan":
                rep["name"] = "Jasraj <b>Hallan</b> & Co\x07"
        make_maps.main(["--level", "federal"])
        root = self.kml()  # parses, so the markup and control character were handled
        values = [d.findtext(f"{KML}value") for d in root.iter(f"{KML}Data") if d.get("name") == "representatives"]
        self.assertIn("Jasraj <b>Hallan</b> & Co", values)

    def test_big_files_are_simplified_to_fit_my_maps(self):
        # A riding traced with 4,000 points on a slightly wobbly circle...
        ring = [[-114.0 + 0.1 * math.cos(2 * math.pi * i / 4000) + 0.00001 * (i % 2),
                 51.0 + 0.1 * math.sin(2 * math.pi * i / 4000)] for i in range(4000)]
        ring.append(ring[0])
        self.boundaries.boundaries[f"/boundaries/{FEDERAL_SET}/48007/"] = (
            "Calgary East", "48007", {"type": "MultiPolygon", "coordinates": [[ring]]})
        make_maps.main(["--level", "federal"])
        full_size = os.path.getsize(self.map_file("federal_map.kml"))
        self.assertNotIn("simplified", self.output.getvalue())

        with mock.patch.object(make_maps, "KML_MAX_BYTES", full_size // 2):
            self.assertEqual(make_maps.main(["--level", "federal"]), 0)
        self.assertLess(os.path.getsize(self.map_file("federal_map.kml")), full_size // 2)
        self.assertIn("simplified to about 25 m", self.output.getvalue())
        # ... while the GeoJSON keeps every point.
        calgary = next(f for f in self.load_geojson("federal_map")["features"]
                       if f["properties"]["district"] == "Calgary East")
        self.assertEqual(len(calgary["geometry"]["coordinates"][0][0]), 4001)

    def test_too_many_shapes_for_my_maps_is_flagged(self):
        with mock.patch.object(make_maps, "KML_MAX_PLACEMARKS", 5):
            make_maps.main(["--level", "federal"])
        self.assertIn("Google My Maps imports at most 5 shapes per layer", self.output.getvalue())


class HtmlTests(MapTestCase):
    def test_page_embeds_the_map_data_safely(self):
        self.represent.reps = [dict(r) for r in self.represent.reps]
        attack = "</script><script>alert(1)</script><!--"
        for rep in self.represent.reps:
            if rep["name"] == "Parm Bains":
                rep["name"] = attack
        make_maps.main(["--level", "federal"])
        page, data = self.page_data("federal_map")
        self.assertEqual(page.count("</script>"), 3)  # Leaflet, the data, the code -- nothing injected
        self.assertNotIn("<!--", page)
        names = [rep["name"] for f in data["features"] for rep in f["properties"]["reps"]]
        self.assertIn(attack, names)  # the name itself survives intact
        self.assertIn('integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="', page)
        self.assertNotIn("tile.openstreetmap.org", page)  # blocks pages opened from disk

    def test_page_data(self):
        make_maps.main(["--level", "federal"])
        page, data = self.page_data("federal_map")
        self.assertIn("<title>Federal ridings</title>", page)
        self.assertTrue(data["subtitle"].startswith("6 district(s), 6 representative(s)."))
        bains = next(f["properties"] for f in data["features"] if f["properties"]["district"].startswith("Richmond"))
        self.assertEqual(bains["colour"], "#D71920")
        self.assertEqual(bains["reps"][0]["name"], "Parm Bains")
        self.assertEqual(set(bains["reps"][0]), {"name", "office", "party", "email", "phone", "local_phone", "url", "photo"})


class GeometryTests(ScraperTestCase):
    def test_simplify_drops_points_within_tolerance(self):
        # A square with a 1 m bump on one side and a 500 m spike on another.
        lat = 45.0
        metre = 1 / make_maps.METRES_PER_DEGREE
        ring = [[0, lat], [0.005, lat + metre], [0.01, lat], [0.01, lat + 0.01], [0.015, lat + 0.011],
                [0.01, lat + 0.012], [0.01, lat + 0.02], [0, lat + 0.02], [0, lat]]
        simplified = make_maps._simplify_ring(ring, 10)
        self.assertNotIn([0.005, lat + metre], simplified)
        self.assertIn([0.015, lat + 0.011], simplified)
        self.assertEqual(simplified[0], simplified[-1])

    def test_tiny_islands_disappear_but_never_the_whole_district(self):
        island = [[0, 0], [0.0001, 0], [0.0001, 0.0001], [0, 0.0001], [0, 0]]
        mainland = [[1, 1], [1.5, 1], [1.5, 1.5], [1, 1.5], [1, 1]]
        self.assertEqual(make_maps.simplify_polygons([[mainland], [island]], 100), [[mainland]])
        self.assertEqual(make_maps.simplify_polygons([[island]], 100), [[island]])

    def test_clean_polygons(self):
        ring = [[1.0000001, 2.0], [1.0000002, 2.0], [3.0, 2.0], [3.0, 4.0]]  # repeated point, not closed
        self.assertEqual(make_maps.clean_polygons([[ring, [[0, 0], [0, 0]]]]),
                         [[[[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 2.0]]]])
        self.assertEqual(make_maps.clean_polygons([[[[0, 0], [0, 0], [0, 0]]]]), [])

    def test_party_colours(self):
        for party, colour in [("Liberal", "#D71920"), ("Ontario Liberal Party", "#D71920"),
                              ("Parti libéral du Québec", "#D71920"),
                              ("Progressive Conservative Party of Ontario", "#1A4782"),
                              ("United Conservative Party", "#1A4782"), ("NDP", "#F37021"),
                              ("Alberta New Democratic Party", "#F37021"), ("Bloc Québécois", "#33B2CC"),
                              ("Green Party of Ontario", "#3D9B35"), ("Parti Québécois", "#004C9D"),
                              ("Independent", "#8C8C8C"), ("", make_maps.NO_PARTY_COLOUR)]:
            self.assertEqual(make_maps.party_colour(party), colour, party)
        self.assertIn(make_maps.party_colour("Some New Party"), make_maps.PALETTE)
        self.assertEqual(make_maps.party_colour("Some New Party"), make_maps.party_colour("Some New Party"))

    def test_boundary_parts(self):
        self.assertEqual(make_maps.boundary_parts("/boundaries/toronto-wards-2018/ward-1/"),
                         ("toronto-wards-2018", "ward-1"))
        for url in ("", "/boundaries/toronto-wards-2018/", "/boundary-sets/x/y/", "/boundaries/../../etc/"):
            self.assertIsNone(make_maps.boundary_parts(url), url)


def write_spending_csv(tmp, rows):
    """The per-quarter CSV scrape_budget_office_spending.py writes: (published name, riding, total per quarter)."""
    import represent_common as rc
    import scrape_budget_office_spending as sos

    records = []
    for quarter in ((2026, 3), (2026, 4)):
        start, end = sos.quarter_dates(*quarter)
        for name, riding, amount in rows:
            records.append({"fiscal_year": sos.fiscal_year_label(quarter[0]), "quarter": quarter[1],
                            "period_start": start.isoformat(), "period_end": end.isoformat(), "name": name,
                            "constituency": riding, "caucus": "", "salaries": amount, "travel": 0, "hospitality": 0,
                            "contracts": 0, "total": amount})
    rc.write_csv(os.path.join(tmp, "federal", "mp_office_spending_by_quarter.csv"), records, sos.QUARTER_CSV_FIELDS)


def write_votes_summary(tmp, session, rows):
    """A summary CSV like scrape_votes.py's: (name, riding, votes held, took part)."""
    import represent_common as rc
    import scrape_votes

    fields = [c[0] for c in scrape_votes.SUMMARY_COLUMNS if c[0] not in scrape_votes.ACTIVITY_FIELDS]
    records = [{"name": name, "riding": riding, "votes": held, "yes": took_part, "no": 0, "paired": 0,
                "didnt_vote": held - took_part, "participation_pct": round(100 * took_part / held, 1)}
               for name, riding, held, took_part in rows]
    rc.write_csv(os.path.join(tmp, "federal", f"votes_{session}_summary.csv"), records, fields)


# Published "Last, First" names, as the House of Commons reports them.
SPENDING = [("Bains, Parm", "Richmond East—Steveston", 150000.0),
            ("Kronis, Tamara", "Nanaimo—Ladysmith", 120000.0),
            ("Hallan, Jasraj", "Calgary East", 190000.0),
            ("Gill, Amanpreet", "Calgary Skyview", 250000.0),
            ("Gill, Marilène", "Côte-Nord—Kawawachikamach—Nitassinan", 210000.0)]


class ColourByTests(MapTestCase):
    def test_ridings_shaded_by_office_spending(self):
        os.makedirs(os.path.join(self.tmp, "federal"), exist_ok=True)
        write_spending_csv(self.tmp, SPENDING)
        self.assertEqual(make_maps.main(["--colour-by", "spending"]), 0)
        # Only the federal map, under its own name: the party map is kept.
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "maps"))),
                         ["federal_map_spending.geojson", "federal_map_spending.html", "federal_map_spending.kml"])
        ridings = self.properties("federal_map_spending")
        self.assertEqual(ridings["Richmond East—Steveston"]["office_spending"], 300000.0)  # two quarters
        self.assertEqual(ridings["Calgary Skyview"]["office_spending"], 500000.0)  # "Amanpreet S. Gill" found
        self.assertEqual(ridings["Calgary Skyview"]["fill"], make_maps.MEASURE_COLOURS[-1])  # the most, darkest
        self.assertEqual(ridings["Nanaimo—Ladysmith"]["fill"], make_maps.MEASURE_COLOURS[0])  # the least
        self.assertIsNone(ridings["Outremont"]["office_spending"])  # Rachel Bendayan isn't in the file
        self.assertEqual(ridings["Outremont"]["fill"], make_maps.NO_DATA_COLOUR)

        page, data = self.page_data("federal_map_spending")
        self.assertIn("<title>Federal ridings by office spending</title>", page)
        self.assertEqual(data["legend_title"],
                         "MP office spending, October 1, 2025 to March 31, 2026 (2025-2026 Q3, Q4)")
        self.assertEqual(data["legend"][0], {"label": "$240,000", "colour": make_maps.MEASURE_COLOURS[0], "count": 1})
        self.assertEqual(data["legend"][-1], {"label": "No data", "colour": make_maps.NO_DATA_COLOUR, "count": 1})
        self.assertEqual(data["fill_opacity"], make_maps.MEASURE_FILL_OPACITY)
        outremont = next(f["properties"] for f in data["features"] if f["properties"]["district"] == "Outremont")
        self.assertTrue(outremont["dashed"])
        self.assertEqual(outremont["reps"][0]["measure"], "Office spending: no data found")
        bains = next(f["properties"] for f in data["features"] if f["properties"]["district"].startswith("Richmond"))
        self.assertEqual(bains["reps"][0]["measure"], "Office spending: $300,000 over 2 quarter(s)")

        root = ET.parse(self.map_file("federal_map_spending.kml")).getroot()
        values = [d.findtext(f"{KML}value") for d in root.iter(f"{KML}Data") if d.get("name") == "office_spending"]
        self.assertIn("300000.0", values)
        self.assertIn("", values)  # no data
        self.assertIn("d9", {s.findtext(f".//{KML}PolyStyle/{KML}color")[:2] for s in root.iter(f"{KML}Style")})

    def test_ridings_shaded_by_voting_attendance(self):
        os.makedirs(os.path.join(self.tmp, "federal"), exist_ok=True)
        latest = [("Parm Bains", "Richmond East—Steveston", 250, 241), ("Tamara Kronis", "Nanaimo—Ladysmith", 250, 220),
                  ("Rachel Bendayan", "Outremont", 250, 248), ("Jasraj Hallan", "Calgary East", 250, 228),
                  ("Amanpreet Gill", "Calgary Skyview", 250, 232)]
        write_votes_summary(self.tmp, "45-1", latest)
        write_votes_summary(self.tmp, "44-1", [("Parm Bains", "Richmond East—Steveston", 100, 50)])  # older
        write_votes_summary(self.tmp, "45-1_C-30", [("Parm Bains", "Richmond East—Steveston", 4, 1)])  # one bill
        self.assertEqual(make_maps.main(["--level", "federal", "--color-by", "attendance"]), 0)
        ridings = self.properties("federal_map_attendance")
        self.assertEqual(ridings["Richmond East—Steveston"]["vote_participation"], 96.4)
        self.assertEqual(ridings["Outremont"]["fill"], make_maps.MEASURE_COLOURS[-1])  # the most votes
        self.assertIsNone(ridings["Côte-Nord—Kawawachikamach—Nitassinan"]["vote_participation"])
        _, data = self.page_data("federal_map_attendance")
        self.assertEqual(data["legend_title"], "Share of recorded votes taken part in, session 45-1")
        bains = next(f["properties"] for f in data["features"] if f["properties"]["district"].startswith("Richmond"))
        self.assertEqual(bains["reps"][0]["measure"], "Took part in 96.4% of recorded votes (241 of 250)")

    def test_missing_data_is_explained_and_nothing_written(self):
        self.assertEqual(make_maps.main(["--colour-by", "spending"]), 1)
        self.assertIn("No office spending data yet -- run scrape_budget_office_spending.py first.", self.output.getvalue())
        os.makedirs(os.path.join(self.tmp, "federal"), exist_ok=True)
        write_votes_summary(self.tmp, "45-1_C-30", [("Parm Bains", "Richmond East—Steveston", 4, 1)])
        self.assertEqual(make_maps.main(["--colour-by", "attendance"]), 1)
        self.assertIn("No voting records yet -- run scrape_votes.py first", self.output.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "maps")) and os.listdir(os.path.join(self.tmp, "maps")))
        self.assertEqual(self.boundary_requests(), [])  # stopped before downloading any shapes

    def test_only_federal_maps_can_be_shaded(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                make_maps.parse_args(["--level", "local", "--colour-by", "spending"])
        self.assertEqual(ctx.exception.code, 2)


class ValueClassTests(ScraperTestCase):
    def test_equal_sized_classes(self):
        self.assertEqual(make_maps.value_classes(range(1, 11)), [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)])
        self.assertEqual(make_maps.value_classes([]), [])

    def test_equal_values_stay_together(self):
        self.assertEqual(make_maps.value_classes([1, 1, 1, 1, 2, 3]), [(1, 1), (2, 2), (3, 3)])
        self.assertEqual(make_maps.value_classes([7, 7, 7]), [(7, 7)])

    def test_fewer_classes_still_run_light_to_dark(self):
        self.assertEqual(make_maps._shades(5), list(make_maps.MEASURE_COLOURS))
        self.assertEqual(make_maps._shades(3), [make_maps.MEASURE_COLOURS[i] for i in (0, 2, 4)])
        self.assertEqual(make_maps._shades(1), [make_maps.MEASURE_COLOURS[2]])
