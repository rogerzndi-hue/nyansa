import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from nyansa.alerts import (AlertCenter, Rule, RuleError, build_email, fingerprint, in_quiet_hours, load_rules, matches,
                           parse_rules, post_webhook, send_email)
from nyansa.devserver import LiveState, build_store, make_handler, refresh_once
from nyansa.models import Source
from nyansa.runner import refresh
from nyansa.store import InMemoryStore

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
STAMP = "Sat, 17 Oct 2026 11:00:00 GMT"


def feed(*titles):
    items = "".join(f"<item><title>{t}</title><link>https://ex.test/{i}</link><description>d</description><pubDate>{STAMP}</pubDate></item>"
                    for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'.encode()


def make_store(*titles, channel="media", reliability="B", source_id="s1"):
    st = InMemoryStore()
    st.add_source(Source(source_id, source_id, "rss", reliability, url="https://x.test/f", channel=channel))
    refresh(st, lambda u: feed(*titles), NOW)
    return st


class Recorder:
    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    def __call__(self, *args):
        if self.fail:
            self.fail -= 1
            raise OSError("panne simulée")
        self.calls.append(args)


LOW = Rule(id="all", name="Tout", min_level="low")


class RulesTest(unittest.TestCase):
    def bad(self, entry, fragment):
        with self.assertRaises(RuleError) as cm:
            parse_rules([{"id": "r", **entry}])
        self.assertIn(fragment, str(cm.exception))

    def test_validation(self):
        self.bad({"min_level": "urgent"}, "min_level")
        self.bad({"countries": ["XXX"]}, "pays inconnu")
        self.bad({"region": "MARS"}, "région")
        self.bad({"domains": ["ZZZ"]}, "thème")
        self.bad({"webhook_url": "file:///etc/passwd"}, "http(s)")
        self.bad({"webhook_url": "javascript:alert(1)"}, "http(s)")
        self.bad({"emails": ["pas-un-mail"]}, "e-mail")
        self.bad({"quiet_start": "22:00"}, "quiet_start")
        self.bad({"quiet_start": "25:00", "quiet_end": "06:00"}, "HH:MM")
        self.bad({"max_external_per_hour": 0}, "max_external_per_hour")
        with self.assertRaises(RuleError):
            parse_rules([{"id": "a"}, {"id": "a"}])
        with self.assertRaises(RuleError):
            parse_rules([{"name": "sans id"}])

    def test_example_file_is_valid_and_default_is_prudent(self):
        rules = load_rules(Path(__file__).resolve().parents[2] / "config" / "alert_rules.example.json")
        self.assertEqual(len(rules), 3)
        (default,) = load_rules(None)
        self.assertEqual((default.min_level, default.include_social, default.webhook_url, default.emails), ("high", False, None, ()))

    def test_invalid_file_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text("{pas du json", encoding="utf-8")
            with self.assertRaises(RuleError) as cm:
                load_rules(p)
            self.assertIn("r.json", str(cm.exception))

    def test_quiet_hours_cross_midnight(self):
        r = Rule(id="q", name="q", quiet_start="22:00", quiet_end="06:00")
        at = lambda h: datetime(2026, 10, 17, h, 30, tzinfo=timezone.utc)  # noqa: E731
        self.assertTrue(in_quiet_hours(r, at(23)) and in_quiet_hours(r, at(3)))
        self.assertFalse(in_quiet_hours(r, at(12)))
        self.assertFalse(in_quiet_hours(Rule(id="n", name="n"), at(3)))


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.st = make_store("Attaque terroriste à Bamako, au Mali")
        (self.ev,) = self.st.events()

    def test_criteria(self):
        m = lambda **kw: matches(Rule(id="x", name="x", **{"min_level": "low", **kw}), self.ev, "single_source")  # noqa: E731
        self.assertTrue(m())
        self.assertTrue(m(countries=("MLI",)) and not m(countries=("NER",)))
        self.assertTrue(m(region="WEST") and not m(region="EAST"))
        self.assertTrue(m(domains=("SEC",)) and not m(domains=("ECO",)))
        self.assertTrue(m(keywords=("TERRORISTE",)) and m(keywords=("terroriste",)) and not m(keywords=("inondation",)))
        self.assertFalse(m(active=False))
        self.assertEqual(m(min_level="critical"), self.ev.level == "critical")
        self.assertEqual(m(min_level="high"), self.ev.level in ("high", "critical"))

    def test_social_only_excluded_unless_accepted(self):
        self.assertFalse(matches(LOW, self.ev, "unconfirmed_social"))
        self.assertTrue(matches(Rule(id="s", name="s", min_level="low", include_social=True), self.ev, "unconfirmed_social"))


class CenterTest(unittest.TestCase):
    def test_one_alert_per_incident_then_silence(self):
        st = make_store("Attaque terroriste à Bamako, au Mali", "Grève générale au Sénégal")
        c = AlertCenter([LOW], external=False)
        first = c.collect(st, NOW)
        self.assertEqual(len(first), 2)
        self.assertEqual(c.collect(st, NOW + timedelta(minutes=15)), [])
        a = next(x for x in first if x["country"] == "MLI")
        self.assertEqual((a["reason"], a["status"], a["verification"]), ("new", "new", "single_source"))
        self.assertTrue(a["map_url"].startswith("https://www.openstreetmap.org/search?query=Mali"))
        self.assertEqual(a["sources"][0]["url"], "https://ex.test/0")

    def test_escalation_only_when_level_rises(self):
        st = make_store("Attaque terroriste à Bamako, au Mali")
        (ev,) = st.events()
        ev.impact = 1
        c = AlertCenter([LOW], external=False)
        self.assertEqual(len(c.collect(st, NOW)), 1)
        ev.impact = 1
        self.assertEqual(c.collect(st, NOW), [])
        ev.impact, ev.probability_override = 5, 5
        again = c.collect(st, NOW)
        self.assertEqual([(a["reason"], a["level"]) for a in again], [("escalation", "critical")])
        ev.impact = 3  # baisse : rien
        self.assertEqual(c.collect(st, NOW), [])

    def test_social_only_event_is_flagged_unconfirmed(self):
        st = make_store("Attaque terroriste à Bamako, au Mali", channel="social", reliability="E")
        self.assertEqual(AlertCenter([LOW], external=False).collect(st, NOW), [])
        (a,) = AlertCenter([Rule(id="s", name="s", min_level="low", include_social=True)], external=False).collect(st, NOW)
        self.assertEqual(a["verification"], "unconfirmed_social")
        self.assertIn("NON CONFIRMÉ", a["verification_label"])

    def test_no_repeat_after_restart_thanks_to_stable_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "alerts.json"
            st1 = make_store("Attaque terroriste à Bamako, au Mali")
            self.assertEqual(len(AlertCenter([LOW], path, external=False).collect(st1, NOW)), 1)
            st2 = make_store("Attaque terroriste à Bamako, au Mali")  # redémarrage : nouveaux identifiants d'événements
            self.assertNotEqual(st1.events()[0].id, st2.events()[0].id)
            self.assertEqual(fingerprint(st1.events()[0]), fingerprint(st2.events()[0]))
            c2 = AlertCenter([LOW], path, external=False)
            self.assertEqual(c2.collect(st2, NOW), [])
            self.assertEqual(len(c2.list()), 1)
            if os.name == "posix":
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_corrupt_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "alerts.json"
            p.write_text("{casse", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                AlertCenter([LOW], p)
            self.assertEqual(p.read_text(encoding="utf-8"), "{casse")

    def test_acknowledge(self):
        c = AlertCenter([LOW], external=False)
        c.collect(make_store("Attaque terroriste à Bamako, au Mali"), NOW)
        aid = c.list()[0]["id"]
        done = c.acknowledge(aid, "Awa", NOW)
        self.assertEqual((done["status"], done["acked_by"]), ("ack", "Awa"))
        self.assertEqual(c.acknowledge(aid, "Autre", NOW)["acked_by"], "Awa")  # idempotent, le premier reste
        self.assertEqual(c.list(status="new"), [])
        with self.assertRaises(KeyError):
            c.acknowledge("inconnu", "x")


class DeliveryTest(unittest.TestCase):
    def center(self, rule, **kw):
        self.hook, self.mail = Recorder(), Recorder()
        return AlertCenter([rule], webhook=self.hook, email=self.mail, **kw)

    def rule(self, **kw):
        return Rule(id="r", name="r", min_level="low", webhook_url="https://hook.test/x", emails=("a@b.org",), **kw)

    def test_concurrent_delivery_does_not_send_same_webhook_twice(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_webhook(url, payload):
            calls.append(url)
            entered.set()
            release.wait(3)

        c = AlertCenter(
            [self.rule()],
            webhook=blocking_webhook,
            email=lambda alert, recipients: None,
        )
        c.collect(make_store("Attaque terroriste ? Bamako, au Mali"), NOW)

        worker = threading.Thread(target=c.deliver_pending, args=(NOW,))
        worker.start()

        self.assertTrue(entered.wait(2))
        self.assertEqual(c.deliver_pending(NOW), 0)

        release.set()
        worker.join(4)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 1)

    def test_interrupted_sending_delivery_is_recovered_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            path.write_text(
                json.dumps({
                    "alerts": [{
                        "deliveries": [{
                            "channel": "webhook",
                            "status": "sending",
                            "attempts": 0,
                        }]
                    }],
                    "seen": {},
                }),
                encoding="utf-8",
            )

            c = AlertCenter([], path=path)

            delivery = c._alerts[0]["deliveries"][0]
            self.assertEqual(delivery["status"], "failed")
            self.assertEqual(delivery["attempts"], 0)

    def test_sent_to_webhook_and_email(self):
        c = self.center(self.rule())
        c.collect(make_store("Attaque terroriste à Bamako, au Mali"), NOW)
        self.assertEqual(c.deliver_pending(NOW), 2)
        url, payload = self.hook.calls[0]
        self.assertEqual(url, "https://hook.test/x")
        self.assertNotIn("deliveries", payload)
        self.assertEqual(payload["country"], "MLI")
        self.assertEqual(self.mail.calls[0][1], ("a@b.org",))
        self.assertEqual({d["channel"]: d["status"] for d in c.list()[0]["deliveries"]}, {"app": "sent", "webhook": "sent", "email": "sent"})
        self.assertEqual(c.deliver_pending(NOW), 0)  # rien à renvoyer

    def test_failures_are_retried_three_times_then_abandoned(self):
        c = self.center(self.rule())
        self.hook.fail = 99
        c.collect(make_store("Attaque terroriste à Bamako, au Mali"), NOW)
        for _ in range(5):
            c.deliver_pending(NOW)
        hook = next(d for d in c.list()[0]["deliveries"] if d["channel"] == "webhook")
        self.assertEqual((hook["status"], hook["attempts"]), ("failed", 3))
        self.assertIn("panne simulée", hook["error"])
        self.assertEqual(len(self.mail.calls), 1)  # l'échec du webhook n'empêche pas l'e-mail

    def test_quiet_hours_defer_but_critical_goes_through(self):
        c = self.center(self.rule(quiet_start="10:00", quiet_end="14:00"))
        st = make_store("Attaque terroriste à Bamako, au Mali", "Grève générale au Sénégal")
        ev = next(e for e in st.events() if e.country_iso3 == "MLI")
        ev.impact, ev.probability_override = 5, 5
        c.collect(st, NOW)
        c.deliver_pending(NOW)
        by_country = {a["country"]: a for a in c.list()}
        self.assertEqual(by_country["MLI"]["level"], "critical")
        self.assertTrue(all(d["status"] == "sent" for d in by_country["MLI"]["deliveries"]))
        self.assertEqual({d["status"] for d in by_country["SEN"]["deliveries"] if d["channel"] != "app"}, {"deferred"})
        c.deliver_pending(NOW + timedelta(hours=3))  # la plage est terminée
        self.assertEqual({d["status"] for d in c.list()[0]["deliveries"]} | {d["status"] for d in c.list()[1]["deliveries"]}, {"sent"})

    def test_hourly_cap_per_rule(self):
        c = self.center(Rule(id="r", name="r", min_level="low", webhook_url="https://hook.test/x", max_external_per_hour=1))
        c.collect(make_store("Attaque terroriste à Bamako, au Mali", "Grève générale au Sénégal", "Inondations au Niger"), NOW)
        self.assertEqual(c.deliver_pending(NOW), 1)
        statuses = sorted(d["status"] for a in c.list() for d in a["deliveries"] if d["channel"] == "webhook")
        self.assertEqual(statuses, ["sent", "skipped", "skipped"])

    def test_reservation_save_failure_restores_all_delivery_states(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            c = self.center(
                self.rule(max_external_per_hour=1),
                path=path,
            )
            c.collect(
                make_store(
                    "Attaque terroriste ? Bamako, au Mali",
                    "Gr?ve g?n?rale au S?n?gal",
                    "Inondations au Niger",
                ),
                NOW,
            )

            before_file = path.read_bytes()
            before_statuses = [
                delivery["status"]
                for alert in c.list()
                for delivery in alert["deliveries"]
                if delivery["channel"] == "webhook"
            ]
            self.assertGreaterEqual(len(before_statuses), 2)
            self.assertTrue(all(status == "pending" for status in before_statuses))

            with mock.patch(
                "nyansa.alerts.atomic_write_json",
                side_effect=OSError("disque indisponible"),
            ):
                with self.assertRaises(OSError):
                    c.deliver_pending(NOW)

            after_statuses = [
                delivery["status"]
                for alert in c.list()
                for delivery in alert["deliveries"]
                if delivery["channel"] == "webhook"
            ]

            self.assertEqual(after_statuses, before_statuses)
            self.assertEqual(path.read_bytes(), before_file)
            self.assertEqual(self.hook.calls, [])
            self.assertEqual(self.mail.calls, [])

    def test_demo_mode_never_notifies_externally(self):
        c = self.center(self.rule(), external=False)
        c.collect(make_store("Attaque terroriste à Bamako, au Mali"), NOW)
        self.assertEqual(c.deliver_pending(NOW), 0)
        self.assertEqual((self.hook.calls, self.mail.calls), ([], []))
        ext = [d for d in c.list()[0]["deliveries"] if d["channel"] != "app"]
        self.assertTrue(ext and all(d["status"] == "skipped" and "démonstration" in d["error"] for d in ext))


class TransportTest(unittest.TestCase):
    def test_real_webhook_receives_json(self):
        payload = {"title": "Coup d'?tat ? Bamako", "country": "MLI"}

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 204

        opener = mock.MagicMock()
        opener.open.return_value = response

        dns_result = [(2, 1, 6, "", ("93.184.216.34", 443))]

        with mock.patch("nyansa.ingest.socket.getaddrinfo", return_value=dns_result):
            with mock.patch("nyansa.alerts.urllib.request.build_opener", return_value=opener):
                post_webhook("https://example.com/hook", payload)

        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10.0)

        with self.assertRaises(ValueError):
            post_webhook("file:///etc/passwd", {})

    def test_webhook_rejects_private_dns_destination(self):
        private_dns = [(2, 1, 6, "", ("127.0.0.1", 443))]

        with mock.patch("nyansa.ingest.socket.getaddrinfo", return_value=private_dns):
            with self.assertRaises(ValueError):
                post_webhook("https://example.com/hook", {})


    def alert(self, title="Attaque à Bamako", channel="media"):
        return {"level_label": "Critique", "country_name": "Mali", "region_label": "Afrique de l'Ouest", "domain_label": "Sécurité",
                "criticality": 20, "verification_label": "Réseaux sociaux seuls : NON CONFIRMÉ", "event_time": "t", "created_at": "t",
                "title": title, "map_url": "https://www.openstreetmap.org/search?query=Mali",
                "sources": [{"name": "Mastodon", "channel": channel, "reliability": "E", "url": "https://m.test/1"}]}

    def test_email_headers_cannot_be_injected(self):
        msg = build_email(self.alert("Coup d'État\r\nBcc: pirate@evil.test"), ("a@b.org",), "n@x.org")
        self.assertNotIn("\n", msg["Subject"])
        self.assertIsNone(msg["Bcc"])
        body = msg.get_content()
        self.assertIn("NON CONFIRMÉ", body)
        self.assertIn("https://m.test/1", body)
        self.assertIn("à vérifier", body)

    def test_smtp_not_configured_is_explicit(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError) as cm:
            send_email(self.alert(), ("a@b.org",))
        self.assertIn("SMTP non configuré", str(cm.exception))


class LiveIntegrationTest(unittest.TestCase):
    def test_refresh_once_creates_and_delivers_alerts(self):
        st, live, lock = InMemoryStore(), LiveState(), threading.RLock()
        st.add_source(Source("s", "S", "rss", "B", url="https://x.test/f"))
        hook = Recorder()
        c = AlertCenter([Rule(id="r", name="r", min_level="low", webhook_url="https://hook.test/x")], webhook=hook)
        refresh_once(st, live, lock, fetcher=lambda u: feed("Attaque terroriste à Bamako, au Mali"), alerts=c)
        refresh_once(st, live, lock, fetcher=lambda u: feed("Attaque terroriste à Bamako, au Mali"), alerts=c)
        self.assertEqual(len(hook.calls), 1)  # le second cycle ne répète pas
        self.assertEqual(len(c.list()), 1)


class HttpTest(unittest.TestCase):
    def setUp(self):
        st = build_store()
        self.center = AlertCenter([Rule(id="r", name="r", min_level="low", webhook_url="https://secret-host.test/token/ABC123")], external=False)
        self.center.collect(st, NOW)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(st, alerts=self.center))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = dict(headers or {})
        body = None
        if method == "POST":
            h.update({"Content-Type": "application/json"})
            body = "{}"
        c.request(method, path, body, h)
        r = c.getresponse()
        out = r.status, json.loads(r.read())
        c.close()
        return out

    def test_list_filter_and_rules_hide_webhook_secret(self):
        s, data = self.call("GET", "/api/alerts")
        self.assertEqual(s, 200)
        self.assertGreater(len(data["alerts"]), 0)
        self.assertIs(data["external"], False)
        self.assertEqual(self.call("GET", "/api/alerts?status=bogus")[0], 422)
        s, rules = self.call("GET", "/api/alerts/rules")
        self.assertEqual(rules[0]["webhook_host"], "secret-host.test")
        self.assertNotIn("ABC123", json.dumps(rules))

    def test_ack_needs_header_and_a_writing_role(self):
        aid = self.call("GET", "/api/alerts")[1]["alerts"][0]["id"]
        self.assertEqual(self.call("POST", f"/api/alerts/{aid}/ack")[0], 403)  # sans X-Nyansa
        ok = {"X-Nyansa": "1", "X-Dev-User": "Awa"}
        self.assertEqual(self.call("POST", f"/api/alerts/{aid}/ack", {**ok, "X-Dev-Role": "executive"})[0], 403)
        s, done = self.call("POST", f"/api/alerts/{aid}/ack", {**ok, "X-Dev-Role": "analyst"})
        self.assertEqual((s, done["status"], done["acked_by"]), (200, "ack", "Awa"))
        self.assertEqual(self.call("POST", "/api/alerts/inconnu/ack", {**ok, "X-Dev-Role": "analyst"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/alerts/{aid}/ack", {**ok, "Origin": "http://evil.test"})[0], 403)


if __name__ == "__main__":
    unittest.main()
