from support import FakeRepresent, FakeWeb, ScraperTestCase, http_error, load_fixture, pages, sample_represent

import represent_common as rc

SAMPLE = load_fixture("represent_sample.json")
LOCAL_SETS = [s for s in SAMPLE["sets"] if rc.classify_set(s) == "local"]
LOCAL_REPS = [r for r in SAMPLE["representatives"]
              if r["related"]["representative_set_url"] in {s["url"] for s in LOCAL_SETS}]


class FetchTests(ScraperTestCase):
    def test_many_sets_use_one_bulk_download(self):
        with FakeWeb() as web:
            sample_represent(max_page=4).install(web)
            result = rc.fetch_category("local")
            bulk_requests = web.count("/representatives/?")
            per_set_requests = web.count("/representatives/toronto-city-council/")
        self.assertEqual(len(result.sets), 6)
        self.assertEqual(len(result.rows), len(LOCAL_REPS))
        self.assertEqual(bulk_requests, pages(len(SAMPLE["representatives"]), 4))
        self.assertEqual(per_set_requests, 0)
        self.assertEqual({r["category"] for r in result.rows}, {"local"})
        self.assertEqual({r["province_territory"] for r in result.rows}, {"ON", "QC", "SK"})

    def test_real_scale_local_fetch_is_five_requests(self):
        # Represent's real size: the House of Commons (343 MPs) plus 108
        # councils, 3,810 people in all -> the set list + 4 pages of 1,000.
        commons = SAMPLE["sets"][0]
        councils = [
            {"name": f"Town {i} Council", "url": f"/representative-sets/town-{i}/",
             "data_url": f"https://scrapers.herokuapp.com/represent/ca_on_town_{i}/",
             "related": {"representatives_url": f"/representatives/town-{i}/"}}
            for i in range(108)
        ]
        reps = [{"name": f"MP {i}", "related": {"representative_set_url": commons["url"]}} for i in range(343)]
        reps += [{"name": f"Councillor {i}", "related": {"representative_set_url": councils[i % 108]["url"]}}
                 for i in range(3810 - 343)]
        with FakeWeb() as web:
            FakeRepresent([commons] + councils, reps).install(web)
            result = rc.fetch_category("local")
            self.assertEqual(len(web.requests), 5)
        self.assertEqual(len(result.rows), 3810 - 343)

    def test_few_sets_are_fetched_individually(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            result = rc.fetch_category("federal")
            self.assertEqual(web.count("/representatives/?"), 0)
            self.assertEqual(web.count("/representatives/house-of-commons/"), 1)
        self.assertEqual(result.fetched_sets, ["House of Commons"])
        self.assertEqual(len(result.rows), 6)

    def test_a_large_set_is_paginated(self):
        with FakeWeb() as web:
            sample_represent(max_page=2).install(web)
            result = rc.fetch_category("federal")
            self.assertEqual(web.count("/representatives/house-of-commons/"), 3)
        self.assertEqual(len(result.rows), 6)

    def test_incomplete_bulk_download_falls_back_to_per_set(self):
        with FakeWeb() as web:
            fake = sample_represent()
            fake.bulk_total_override = len(SAMPLE["representatives"]) + 5  # data changed mid-download
            fake.install(web)
            result = rc.fetch_category("local")
            self.assertEqual(web.count("/representatives/toronto-city-council/"), 1)
        self.assertEqual(len(result.rows), len(LOCAL_REPS))
        self.assertEqual(result.failed_sets, [])

    def test_a_failing_set_is_skipped_not_fatal(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            web.route(lambda url: "/representatives/?" in url, lambda url: http_error(url, 500))
            web.route(lambda url: "/representatives/huron-county-council/" in url, lambda url: http_error(url, 500))
            result = rc.fetch_category("local")
        self.assertEqual(result.failed_sets, ["Huron County Council"])
        self.assertEqual(len(result.fetched_sets), 5)
        self.assertNotIn("Huron County Council", {r["representative_set"] for r in result.rows})

    def test_an_outage_gives_up_quickly_instead_of_trying_every_set(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            web.route(lambda url: "/representatives/" in url, lambda url: ConnectionRefusedError(111, "refused"))
            result = rc.fetch_category("local")
            elapsed = web.requests[-1][0] - web.requests[0][0]
            attempted = web.count("/representatives/")
        self.assertEqual(len(result.failed_sets), 6)
        self.assertEqual(result.rows, [])
        # bulk (3 attempts) + 3 sets (3 attempts each), then it stops
        self.assertEqual(attempted, rc.NETWORK_MAX_ATTEMPTS * (1 + rc.MAX_CONSECUTIVE_FAILURES))
        self.assertLess(elapsed, 60)

    def test_downloads_are_shared_within_a_run(self):
        with FakeWeb() as web:
            sample_represent().install(web)
            rc.fetch_category("local")
            rc.fetch_category("provincial_territorial")
            rc.fetch_category("local")
            self.assertEqual(web.count("/representative-sets/"), 1)
            self.assertEqual(web.count("/representatives/?"), 1)
