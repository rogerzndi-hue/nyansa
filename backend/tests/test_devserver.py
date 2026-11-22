import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from nyansa.countries import RATED_COUNTRIES
from nyansa.devserver import build_store, make_handler


class DevServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(build_store()))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_risk_covers_all_rated_countries(self):
        status, body, _ = self.get("/api/risk/countries")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual({d["iso3"] for d in data}, {c.iso3 for c in RATED_COUNTRIES})

    def test_events_filters_and_errors(self):
        _, body, _ = self.get("/api/events?country=mli")
        events = json.loads(body)
        self.assertTrue(events and all(e["country"] == "MLI" for e in events))
        self.assertEqual(self.get("/api/events?country=XXX")[0], 404)
        self.assertEqual(self.get("/api/events?domain=nope")[0], 422)

    def test_static_and_traversal(self):
        self.assertEqual(self.get("/")[0], 200)
        self.assertEqual(self.get("/..%2f..%2fREADME.md")[0], 404)
        self.assertEqual(self.get("/api/inconnu")[0], 404)


class LiveModeTest(unittest.TestCase):
    def test_live_mode_exposes_reports_and_no_demo_data(self):
        from datetime import datetime, timezone
        from email.utils import format_datetime

        from nyansa.devserver import LiveState, refresh_once
        from nyansa.models import Source
        from nyansa.store import InMemoryStore

        now = format_datetime(datetime.now(timezone.utc))
        rss = (f'<?xml version="1.0"?><rss version="2.0"><channel><item><title>Attaque terroriste à Bamako, au Mali</title>'
               f"<link>https://x.test/1</link><pubDate>{now}</pubDate></item></channel></rss>").encode()

        def fetcher(url):
            if "bad" in url:
                raise OSError("coupé")
            return rss

        store, live, lock = InMemoryStore(), LiveState(), threading.RLock()
        store.add_source(Source("a", "A", "rss", "C", url="https://a.test/f"))
        store.add_source(Source("b", "B", "rss", "C", url="https://bad.test/f"))
        refresh_once(store, live, lock, fetcher=fetcher)

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, demo=False, live=live, lock=lock))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            info = json.load(urllib.request.urlopen(base + "/api/sources"))
            self.assertEqual({r["source_id"]: r["ok"] for r in info["reports"]}, {"a": True, "b": False})
            events = json.load(urllib.request.urlopen(base + "/api/events"))
            self.assertEqual([e["country"] for e in events], ["MLI"])
            self.assertEqual(json.load(urllib.request.urlopen(base + "/api/unrouted")), [])
            meta = json.load(urllib.request.urlopen(base + "/api/meta"))
            self.assertFalse(meta["demo"])
            self.assertEqual(meta["mode"], "live")
        finally:
            server.shutdown()
            server.server_close()
