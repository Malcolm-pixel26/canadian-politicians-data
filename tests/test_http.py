import http.client
import urllib.error
from unittest import mock

from support import (
    REPRESENT,
    FakeRepresent,
    FakeResponse,
    FakeWeb,
    ScraperTestCase,
    http_error,
    json_response,
)

import represent_common as rc


class RetryTests(ScraperTestCase):
    def test_dropped_connection_is_retried(self):
        # v4 only caught URLError, so this crashed the whole run.
        calls = []

        def flaky(url):
            calls.append(url)
            if len(calls) == 1:
                return http.client.RemoteDisconnected("Remote end closed connection without response")
            return json_response({"ok": True})

        with FakeWeb() as web:
            web.route(f"{REPRESENT}/x", flaky)
            self.assertEqual(rc.http_get_json(f"{REPRESENT}/x"), {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_truncated_response_is_retried(self):
        calls = []

        def truncated(url):
            calls.append(url)
            if len(calls) == 1:
                return http.client.IncompleteRead(b"{", 10)
            return json_response([])

        with FakeWeb() as web:
            web.route(f"{REPRESENT}/x", truncated)
            self.assertEqual(rc.http_get_json(f"{REPRESENT}/x"), [])

    def test_rate_limit_waits_for_retry_after(self):
        responses = [http_error(f"{REPRESENT}/x", 503, {"Retry-After": "30"}), json_response([1])]
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/x", lambda url: responses.pop(0))
            self.assertEqual(rc.http_get_json(f"{REPRESENT}/x"), [1])
            (first, _), (second, _) = web.requests
        self.assertGreaterEqual(second - first, 30)

    def test_backoff_outlasts_a_rate_limit_window(self):
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/x", lambda url: http_error(url, 503))
            with self.assertRaises(rc.FetchError) as ctx:
                rc.http_get(f"{REPRESENT}/x")
            self.assertEqual(web.count("/x"), rc.MAX_ATTEMPTS)
            elapsed = web.requests[-1][0] - web.requests[0][0]
        self.assertGreaterEqual(elapsed, 60)  # v4 gave up after ~6 seconds
        self.assertEqual(ctx.exception.status, 503)

    def test_no_network_fails_fast(self):
        with FakeWeb() as web:
            web.route(REPRESENT, lambda url: urllib.error.URLError("[Errno -2] Name or service not known"))
            with self.assertRaises(rc.FetchError):
                rc.http_get(f"{REPRESENT}/x")
            self.assertEqual(len(web.requests), rc.NETWORK_MAX_ATTEMPTS)
            self.assertLess(web.requests[-1][0] - web.requests[0][0], 10)

    def test_permanent_errors_are_not_retried(self):
        with FakeWeb() as web:
            with self.assertRaises(rc.FetchError) as ctx:
                rc.http_get(f"{REPRESENT}/missing")
            self.assertEqual(web.count("/missing"), 1)
        self.assertEqual(ctx.exception.status, 404)

    def test_invalid_json_is_a_fetch_error(self):
        with FakeWeb() as web:
            web.route(f"{REPRESENT}/x", lambda url: FakeResponse(b"<html>maintenance</html>"))
            with self.assertRaises(rc.FetchError):
                rc.http_get_json(f"{REPRESENT}/x")

    def test_retry_after_parsing(self):
        self.assertEqual(rc._retry_after_seconds("12"), 12)
        self.assertEqual(rc._retry_after_seconds("100000"), rc.MAX_RETRY_AFTER_SECONDS)
        self.assertIsNone(rc._retry_after_seconds("soon"))
        self.assertIsNone(rc._retry_after_seconds(None))
        self.assertEqual(rc._retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)


class ThrottleTests(ScraperTestCase):
    def test_requests_to_one_host_are_spaced_out(self):
        with FakeWeb(latency=0.05) as web:
            web.route(REPRESENT, lambda url: json_response({}))
            for i in range(80):
                rc.http_get_json(f"{REPRESENT}/{i}")
            peak = web.peak_per_window("represent.opennorth.ca")
        self.assertLessEqual(peak, 60)

    def test_hosts_are_throttled_independently(self):
        with FakeWeb(latency=0.0) as web:
            web.route("https://", lambda url: json_response({}))
            rc.http_get_json(f"{REPRESENT}/a")
            rc.http_get_json("https://api.openparliament.ca/a")
            (t1, _), (t2, _) = web.requests
        self.assertLess(t2 - t1, 0.5)

    def test_one_request_per_council_stays_under_the_limit(self):
        # The v4 bug: the delay only ran between pages of one council, so
        # 108 one-page councils went out back-to-back (109 requests/minute).
        sets = [
            {"name": f"Town {i} Council", "url": f"/representative-sets/town-{i}/",
             "data_url": f"https://scrapers.herokuapp.com/represent/ca_on_town_{i}/",
             "related": {"representatives_url": f"/representatives/town-{i}/"}}
            for i in range(108)
        ]
        with FakeWeb(latency=0.25) as web, mock.patch.object(rc, "BULK_FETCH_MIN_SETS", 10_000):
            FakeRepresent(sets, []).install(web)
            result = rc.fetch_category("local")
            peak = web.peak_per_window("represent.opennorth.ca")
        self.assertEqual(len(result.fetched_sets), 108)
        self.assertLessEqual(peak, 60)
