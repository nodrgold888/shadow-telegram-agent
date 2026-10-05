import asyncio
import http.server
import os
import threading
import unittest
from unittest import mock

from shadow import keepalive
from shadow.keepalive import KeepAlive


class _Handler(http.server.BaseHTTPRequestHandler):
    fail_first = 0
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        if type(self).hits <= type(self).fail_first:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class KeepAliveTests(unittest.TestCase):
    def setUp(self):
        _Handler.hits = 0
        _Handler.fail_first = 0
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/ping"

    def tearDown(self):
        self.server.shutdown()

    def test_success_updates_state(self):
        ka = KeepAlive(self.url, retry_delays=())
        self.assertTrue(asyncio.run(ka.ping_once()))
        self.assertEqual((ka.ok_count, ka.consecutive_failures, ka.last_error), (1, 0, None))
        self.assertIsNotNone(ka.status()["last_ok_age_seconds"])

    def test_retries_then_recovers(self):
        _Handler.fail_first = 2
        ka = KeepAlive(self.url, retry_delays=(0, 0, 0))
        self.assertTrue(asyncio.run(ka.ping_once()))
        self.assertEqual(_Handler.hits, 3)
        self.assertEqual(ka.fail_count, 0)

    def test_failure_is_counted_and_never_raises(self):
        _Handler.fail_first = 99
        ka = KeepAlive(self.url, retry_delays=(0,))
        self.assertFalse(asyncio.run(ka.ping_once()))
        self.assertFalse(asyncio.run(ka.ping_once()))
        self.assertEqual((ka.fail_count, ka.consecutive_failures), (2, 2))
        self.assertEqual(ka.last_error, "HTTPError")

    def test_jitter_stays_in_bounds(self):
        ka = KeepAlive("http://x/ping", interval=300)
        for _ in range(200):
            self.assertTrue(270 <= ka.next_delay() <= 330)

    def test_config(self):
        with mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://a.onrender.com/", "KEEPALIVE_URL": "", "KEEPALIVE_INTERVAL_SECONDS": "5"}):
            self.assertEqual(keepalive.keepalive_url(), "https://a.onrender.com/ping")
            self.assertEqual(keepalive.keepalive_interval(), 60)
        with mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "", "KEEPALIVE_URL": ""}):
            self.assertEqual(keepalive.keepalive_url(), "")
            self.assertIsNone(keepalive.build_keepalive())


if __name__ == "__main__":
    unittest.main()
