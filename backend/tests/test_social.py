import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from nyansa import devserver
from nyansa.devserver import LiveState, build_store, make_handler, refresh_once
from nyansa.models import Source
from nyansa.runner import refresh
from nyansa.services import ingest_manual
from nyansa.social import (MAX_WATCHES, assert_public_host, WatchBook, WatchError, load_demo_social, load_seed, social_view, sync_sources,
                           validate_watch, watch_source)
from nyansa.store import InMemoryStore

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def feed(*items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                   f"<pubDate>{(NOW - timedelta(hours=h)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>" for t, u, d, h in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


class PlatformsTest(unittest.TestCase):
    """Bluesky (compte), YouTube (chaîne), Reddit (subreddit) : flux RSS publics, hôtes fixes."""

    UC = "UC" + "a" * 22

    def test_mastodon_ids_are_unchanged_by_platform_support(self):
        import hashlib

        a = validate_watch({"kind": "hashtag", "value": "Mali"})
        self.assertEqual((a.platform, a.id), ("mastodon", validate_watch({"kind": "hashtag", "value": "Mali", "platform": "mastodon"}).id))
        # formule d'origine : les veilles Mastodon déjà enregistrées gardent leur identifiant
        self.assertEqual(a.id, hashlib.sha1(b"hashtag|mali|mastodon.social").hexdigest()[:10])

    def test_urls_use_fixed_hosts(self):
        cases = [
            ({"kind": "account", "platform": "bluesky", "value": "@media.bsky.social"}, "https://bsky.app/profile/media.bsky.social/rss"),
            ({"kind": "account", "platform": "bluesky", "value": "did:plc:abc123"}, "https://bsky.app/profile/did:plc:abc123/rss"),
            ({"kind": "channel", "platform": "youtube", "value": f"https://www.youtube.com/channel/{self.UC}"}, f"https://www.youtube.com/feeds/videos.xml?channel_id={self.UC}"),
            ({"kind": "channel", "platform": "reddit", "value": "r/Nigeria"}, "https://www.reddit.com/r/Nigeria/.rss"),
        ]
        for raw, url in cases:
            with self.subTest(raw=raw):
                w = validate_watch(raw)
                self.assertEqual(watch_source(w).url, url)
                self.assertEqual((watch_source(w).channel, watch_source(w).default_reliability), ("social", "E"))
                self.assertEqual(w.instance, "")

    def test_distinct_platforms_give_distinct_ids(self):
        self.assertNotEqual(validate_watch({"kind": "channel", "platform": "reddit", "value": "Nigeria"}).id,
                            validate_watch({"kind": "account", "platform": "bluesky", "value": "nigeria.bsky.social"}).id)

    def test_invalid_values_and_combinations(self):
        bad = [
            ({"kind": "channel", "platform": "youtube", "value": "pas-un-identifiant"}, "Chaîne YouTube"),
            ({"kind": "channel", "platform": "reddit", "value": "a"}, "Subreddit"),
            ({"kind": "channel", "platform": "reddit", "value": "../etc/passwd"}, "Subreddit"),
            ({"kind": "account", "platform": "bluesky", "value": "pas un nom"}, "Compte Bluesky"),
            ({"kind": "account", "platform": "bluesky", "value": "evil.com/../x"}, "Compte Bluesky"),
            ({"kind": "hashtag", "platform": "bluesky", "value": "Mali"}, "type possible"),
            ({"kind": "channel", "value": "Nigeria"}, "type possible"),  # Mastodon n'a pas de « chaîne »
            ({"kind": "account", "platform": "telegram", "value": "x"}, "Plateforme invalide"),
        ]
        for raw, fragment in bad:
            with self.subTest(raw=raw), self.assertRaises(WatchError) as cm:
                validate_watch(raw)
            self.assertIn(fragment, str(cm.exception))

    def test_keyword_ignores_platform_and_collects_nothing(self):
        w = validate_watch({"kind": "keyword", "value": "coup d'état", "platform": "anything"})
        self.assertEqual((w.platform, watch_source(w)), ("", None))

    def test_persistence_roundtrip_keeps_platform(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "w.json"
            book = WatchBook(path)
            w = book.add({"kind": "channel", "platform": "reddit", "value": "Ghana"})
            self.assertEqual(WatchBook(path).get(w.id).platform, "reddit")

    def test_youtube_atom_feed_is_read_with_media_description(self):
        from nyansa.ingest import parse_feed

        atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">'
                b'<entry><title>Attaque \xc3\xa0 Bamako</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>'
                b'<published>2026-10-18T08:00:00+00:00</published>'
                b'<media:group><media:description>Une attaque terroriste a eu lieu \xc3\xa0 Bamako, au Mali.</media:description></media:group></entry></feed>')
        (doc,) = parse_feed(atom, "yt")
        self.assertEqual((doc.title, doc.url), ("Attaque à Bamako", "https://www.youtube.com/watch?v=abc"))
        self.assertIn("Bamako, au Mali", doc.text)

    def test_platform_source_end_to_end_routes_a_post(self):
        st, book = InMemoryStore(), WatchBook()
        w = book.add({"kind": "channel", "platform": "reddit", "value": "Nigeria"}, now=NOW)
        sync_sources(st, book)
        atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Attaque terroriste signal\xc3\xa9e \xc3\xa0 Maiduguri, au Nigeria</title>'
                b'<link href="https://www.reddit.com/r/Nigeria/comments/1/x/"/><updated>2026-10-18T08:00:00+00:00</updated>'
                b'<content type="html">Une attaque terroriste a eu lieu \xc3\xa0 Maiduguri, Nigeria.</content></entry></feed>')
        with mock.patch.object(devserver, "assert_public_host"):
            refresh_once(st, LiveState(), threading.RLock(), fetcher=lambda u: atom)
        v = social_view(st, book, None, NOW)
        self.assertEqual((v["posts"][0]["status"], v["posts"][0]["country"], v["posts"][0]["watches"]), ("unconfirmed", "NGA", [w.id]))


class ValidationTest(unittest.TestCase):
    def bad(self, raw, fragment):
        with self.assertRaises(WatchError) as cm:
            validate_watch(raw)
        self.assertIn(fragment, str(cm.exception))

    def test_hashtag_normalised_and_id_stable(self):
        a = validate_watch({"kind": "hashtag", "value": "#Mali"})
        b = validate_watch({"kind": "hashtag", "value": "mali"})
        self.assertEqual((a.value, a.instance, a.id), ("Mali", "mastodon.social", b.id))
        self.assertEqual(a.source_id, f"watch-{a.id}")

    def test_instance_must_be_a_public_domain(self):
        for host in ("127.0.0.1", "localhost", "intranet.local", "10.0.0.5", "evil", "https://x.org", "a b.org", "x.org/path"):
            with self.subTest(host=host):
                self.bad({"kind": "hashtag", "value": "Mali", "instance": host}, "Instance invalide")
        self.assertEqual(validate_watch({"kind": "hashtag", "value": "Mali", "instance": "Piaille.FR"}).instance, "piaille.fr")

    def test_public_host_guard_blocks_names_resolving_to_internal_addresses(self):
        fake = lambda *ips: (lambda host, port: [(2, 1, 6, "", (ip, 0)) for ip in ips])  # noqa: E731
        assert_public_host("https://piaille.fr/tags/x.rss", fake("93.184.216.34"))
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "::1"):
            with self.subTest(ip=ip), self.assertRaises(WatchError):
                assert_public_host("https://127.0.0.1.nip.io/tags/x.rss", fake(ip))
        with self.assertRaises(WatchError):  # une seule adresse interne parmi plusieurs suffit à refuser
            assert_public_host("https://x.org/", fake("93.184.216.34", "10.0.0.1"))
        with self.assertRaises(WatchError):
            assert_public_host("https://x.org/", mock.Mock(side_effect=OSError("DNS")))

    def test_account_forms(self):
        w = validate_watch({"kind": "account", "value": "@ministere@piaille.fr"})
        self.assertEqual((w.value, w.instance), ("ministere", "piaille.fr"))
        self.bad({"kind": "account", "value": "bad name!"}, "Compte")
        self.bad({"kind": "account", "value": "x@127.0.0.1"}, "Instance invalide")

    def test_other_rules(self):
        self.bad({"kind": "hashtag", "value": "a"}, "Hashtag")
        self.bad({"kind": "hashtag", "value": "deux mots"}, "Hashtag")
        self.bad({"kind": "keyword", "value": "x"}, "Mot-clé")
        self.bad({"kind": "radio", "value": "xx"}, "kind invalide")
        self.bad({"kind": "keyword", "value": "coup", "countries": ["XXX"]}, "Pays inconnu")
        self.bad({"kind": "keyword", "value": "coup", "categories": ["ZZZ"]}, "Catégorie")
        self.bad("pas un objet", "Objet attendu")
        self.assertEqual(validate_watch({"kind": "keyword", "value": "coup", "countries": ["mli", "MLI"]}).countries, ("MLI",))

    def test_sources_built_from_watches(self):
        h = validate_watch({"kind": "hashtag", "value": "Mali"})
        s = watch_source(h)
        self.assertEqual((s.url, s.channel, s.default_reliability, s.kind), ("https://mastodon.social/tags/Mali.rss", "social", "E", "rss"))
        a = watch_source(validate_watch({"kind": "account", "value": "ministere@piaille.fr"}))
        self.assertEqual(a.url, "https://piaille.fr/@ministere.rss")
        self.assertIsNone(watch_source(validate_watch({"kind": "keyword", "value": "coup d'État"})))
        self.assertFalse(watch_source(validate_watch({"kind": "hashtag", "value": "Mali", "active": False})).active)


class BookTest(unittest.TestCase):

    def test_write_failure_restores_add(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watches.json"
            book = WatchBook(path)

            with mock.patch(
                "nyansa.social.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    book.add(
                        {"kind": "hashtag", "value": "Ghana"}, "Awa", NOW
                    )

            self.assertEqual(book.list(), [])
            self.assertFalse(path.exists())

    def test_write_failure_restores_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watches.json"
            book = WatchBook(path)
            watch = book.add(
                {"kind": "hashtag", "value": "Mali"}, "Awa", NOW
            )
            before_memory = book.list()
            before_disk = path.read_bytes()

            with mock.patch(
                "nyansa.social.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    book.remove(watch.id)

            self.assertEqual(book.list(), before_memory)
            self.assertEqual(path.read_bytes(), before_disk)

    def test_write_failure_restores_set_active(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watches.json"
            book = WatchBook(path)
            watch = book.add(
                {"kind": "hashtag", "value": "Mali"}, "Awa", NOW
            )
            before_memory = book.list()
            before_disk = path.read_bytes()

            with mock.patch(
                "nyansa.social.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    book.set_active(watch.id, False)

            self.assertEqual(book.list(), before_memory)
            self.assertEqual(path.read_bytes(), before_disk)
            self.assertTrue(book.get(watch.id).active)

    def test_add_duplicate_remove_toggle_limit(self):
        b = WatchBook()
        w = b.add({"kind": "hashtag", "value": "Mali"}, "Awa", NOW)
        self.assertEqual((w.created_by, w.created_at), ("Awa", "2026-10-18T12:00:00+00:00"))
        with self.assertRaises(WatchError):
            b.add({"kind": "hashtag", "value": "mali"})
        self.assertFalse(b.set_active(w.id, False).active)
        self.assertEqual(b.remove(w.id).id, w.id)
        with self.assertRaises(KeyError):
            b.remove(w.id)
        for i in range(MAX_WATCHES):
            b.add({"kind": "keyword", "value": f"mot{i:03d}"})
        with self.assertRaises(WatchError):
            b.add({"kind": "keyword", "value": "de trop"})

    def test_persistence_roundtrip_keeps_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "watches.json"
            b = WatchBook(p)
            w = b.add({"kind": "hashtag", "value": "Sahel", "countries": ["MLI"], "categories": ["SEC"]}, "Awa", NOW)
            b.set_active(w.id, False)
            again = WatchBook(p).get(w.id)
            self.assertEqual((again.active, again.countries, again.categories, again.created_by, again.created_at),
                             (False, ("MLI",), ("SEC",), "Awa", "2026-10-18T12:00:00+00:00"))
            if os.name == "posix":
                self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")

    def test_corrupt_file_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.json"
            p.write_text("{casse", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                WatchBook(p)
            self.assertEqual(p.read_text(encoding="utf-8"), "{casse")

    def test_seed_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seed.json"
            p.write_text(json.dumps([{"kind": "hashtag", "value": "Sahel"}]), encoding="utf-8")
            self.assertEqual(len(load_seed(p)), 1)
            self.assertEqual(load_seed(Path(d) / "absent.json"), [])
            p.write_text(json.dumps([{"kind": "hashtag", "value": "x"}]), encoding="utf-8")
            with self.assertRaises(WatchError) as cm:
                load_seed(p)
            self.assertIn("seed.json", str(cm.exception))

    def test_sync_sources_adds_updates_and_deactivates(self):
        st, b = InMemoryStore(), WatchBook()
        w = b.add({"kind": "hashtag", "value": "Mali"})
        b.add({"kind": "keyword", "value": "coup"})
        sync_sources(st, b)
        self.assertEqual([s.id for s in st.sources()], [w.source_id])  # le mot-clé ne crée aucune source
        self.assertTrue(st.get_source(w.source_id).active)
        b.set_active(w.id, False)
        sync_sources(st, b)
        self.assertFalse(st.get_source(w.source_id).active)
        b.set_active(w.id, True)
        sync_sources(st, b)
        b.remove(w.id)
        sync_sources(st, b)
        self.assertFalse(st.get_source(w.source_id).active)  # désactivée, jamais supprimée : l'historique reste lisible


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.st, self.book = InMemoryStore(), WatchBook()
        self.st.add_source(Source("media", "Média A", "rss", "B", url="https://m.test/f"))
        self.tag = self.book.add({"kind": "hashtag", "value": "Mali"}, now=NOW)
        sync_sources(self.st, self.book)

    def ingest(self, source_id, title, text="d", hours=1.0, url="https://p.test/1"):
        return ingest_manual(self.st, title=title, text=text, source_id=source_id, url=url, published_at=NOW - timedelta(hours=hours))

    def view(self, **kw):
        return social_view(self.st, self.book, None, NOW, **kw)

    def by_title(self, v):
        return {p["title"]: p for p in v["posts"]}

    def test_three_statuses(self):
        self.ingest("media", "Coup d'État en cours à Bamako, au Mali", url="https://m.test/a")
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins")
        self.ingest(self.tag.source_id, "Attaque signalée à Kidal au Mali", "Une attaque armée a eu lieu à Kidal, au Mali.")
        self.ingest(self.tag.source_id, "Rumeur sans lieu")
        p = self.by_title(self.view())
        self.assertEqual(p["Coup d'État en cours à Bamako selon des témoins"]["status"], "corroborated")
        self.assertEqual(p["Coup d'État en cours à Bamako selon des témoins"]["media_sources"][0]["url"], "https://m.test/a")
        self.assertEqual(p["Attaque signalée à Kidal au Mali"]["status"], "unconfirmed")
        self.assertEqual(p["Rumeur sans lieu"]["status"], "to_qualify")
        self.assertEqual(p["Rumeur sans lieu"]["reason"], "no_country")

    def test_social_posts_never_corroborate_each_other(self):
        self.st.add_source(Source("autre", "Autre flux social", "rss", "E", channel="social"))
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins")
        self.ingest("autre", "Coup d'État en cours à Bamako selon des témoins", url="https://p.test/2")
        self.assertEqual({p["status"] for p in self.view()["posts"]}, {"unconfirmed"})

    def test_media_documents_are_not_posts(self):
        self.ingest("media", "Coup d'État en cours à Bamako, au Mali")
        self.assertEqual(self.view()["posts"], [])

    def test_filters_and_keyword_watch(self):
        kw = self.book.add({"kind": "keyword", "value": "coup d'état", "countries": ["MLI"]})
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins")
        self.ingest(self.tag.source_id, "Grève générale à Dakar au Sénégal", "Grève à Dakar, Sénégal.", url="https://p.test/3")
        v = self.view()
        p = self.by_title(v)
        self.assertEqual(set(p["Coup d'État en cours à Bamako selon des témoins"]["watches"]), {self.tag.id, kw.id})
        self.assertEqual(p["Grève générale à Dakar au Sénégal"]["watches"], [self.tag.id])
        self.assertEqual(self.view(watch=kw.id)["total"], 1)
        self.assertEqual(self.view(status="unconfirmed")["total"], 2)
        self.assertEqual(self.view(country="SEN")["total"], 1)
        self.assertEqual(self.view(limit=1)["total"], 2)
        self.assertEqual(len(self.view(limit=1)["posts"]), 1)

    def test_same_post_from_two_feeds_is_shown_once(self):
        other = self.book.add({"kind": "hashtag", "value": "Sahel"}, now=NOW)
        sync_sources(self.st, self.book)
        self.ingest(self.tag.source_id, "Attaque signalée à Kidal au Mali", "Une attaque armée a eu lieu à Kidal, au Mali.", url="https://p.test/9")
        self.ingest(other.source_id, "Attaque signalée à Kidal au Mali", "Une attaque armée a eu lieu à Kidal, au Mali.", url="https://p.test/9")
        self.ingest(other.source_id, "Autre message distinct à Kidal au Mali : attaque", "Attaque armée à Kidal, Mali.", url="https://p.test/9")
        v = self.view()
        self.assertEqual(v["total"], 2)  # le doublon est fusionné, le message distinct (même adresse) reste
        merged = self.by_title(v)["Attaque signalée à Kidal au Mali"]
        self.assertEqual(set(merged["watches"]), {self.tag.id, other.id})
        self.assertEqual(len(merged["also_in"]), 1)

    def test_relevant_filter_hides_to_qualify_and_counts_them(self):
        self.ingest(self.tag.source_id, "Attaque signalée à Kidal au Mali", "Une attaque armée a eu lieu à Kidal, au Mali.", url="https://p.test/1")
        self.ingest(self.tag.source_id, "Playlist du soir", "Musique", url="https://p.test/2")
        v = self.view(status="relevant")
        self.assertEqual([p["status"] for p in v["posts"]], ["unconfirmed"])
        self.assertEqual(v["by_status"], {"corroborated": 0, "unconfirmed": 1, "to_qualify": 1})
        self.assertEqual(self.view()["total"], 2)
        self.assertEqual(self.view(status="to_qualify")["total"], 1)

    def test_inactive_watch_does_not_match(self):
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins")
        self.book.set_active(self.tag.id, False)
        self.assertEqual(self.view()["posts"][0]["watches"], [])

    def test_unsafe_post_url_is_dropped(self):
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins", url="javascript:alert(1)")
        self.assertIsNone(self.view()["posts"][0]["url"])

    def test_summary_counts_last_24h_only(self):
        self.ingest(self.tag.source_id, "Coup d'État en cours à Bamako selon des témoins", hours=2)
        self.ingest(self.tag.source_id, "Attaque signalée à Kidal au Mali", "Une attaque armée à Kidal, au Mali.", hours=60, url="https://p.test/9")
        s = self.view()
        self.assertEqual(s["summary"]["posts_24h"], 1)
        self.assertEqual(s["total"], 2)
        w = s["watches"][0]
        self.assertEqual((w["posts_24h"], w["posts_7d"]), (1, 2))

    def test_connector_states(self):
        kw = self.book.add({"kind": "keyword", "value": "coup"})
        off = self.book.add({"kind": "hashtag", "value": "Sahel", "active": False})
        conn = lambda v, w: next(x for x in v["watches"] if x["id"] == w.id)["connector"]["status"]  # noqa: E731
        v = self.view()
        self.assertEqual((conn(v, self.tag), conn(v, kw), conn(v, off)), ("pending", "filter", "inactive"))
        ok = [{"source_id": self.tag.source_id, "ok": True, "at": NOW, "documents": 4, "error": None}]
        self.assertEqual(conn(social_view(self.st, self.book, ok, NOW), self.tag), "ok")
        bad = [{"source_id": self.tag.source_id, "ok": False, "at": NOW, "documents": 0, "error": "HTTP 404"}]
        v = social_view(self.st, self.book, bad, NOW)
        self.assertEqual(next(x for x in v["watches"] if x["id"] == self.tag.id)["connector"]["error"], "HTTP 404")
        self.assertEqual(conn(social_view(self.st, self.book, None, NOW, demo=True), self.tag), "demo")

    def test_demo_social_shows_each_status(self):
        st, b = build_store(), WatchBook()
        self.assertEqual(load_demo_social(st, b), 3)
        v = social_view(st, b, None, demo=True)
        self.assertEqual({k: v["summary"][k] for k in ("corroborated", "unconfirmed", "to_qualify")}, {"corroborated": 1, "unconfirmed": 1, "to_qualify": 1})
        self.assertTrue(all("[DÉMO]" in p["title"] for p in v["posts"]))


class EndToEndTest(unittest.TestCase):
    def test_new_watch_is_collected_and_flagged_unconfirmed(self):
        st, live, lock, book = InMemoryStore(), LiveState(), threading.RLock(), WatchBook()
        w = book.add({"kind": "hashtag", "value": "Mali"})
        book.add({"kind": "keyword", "value": "témoins"})
        sync_sources(st, book)
        seen = []

        def fetcher(url):
            seen.append(url)
            return feed(("@awa@x.social Attaque signalée à Kidal au Mali selon des témoins", "https://p.test/1", "Une attaque armée à Kidal, au Mali.", 1))

        with mock.patch.object(devserver, "assert_public_host", lambda url: None):  # pas de DNS dans les tests
            refresh_once(st, live, lock, fetcher=fetcher)
        self.assertEqual(seen, ["https://mastodon.social/tags/Mali.rss"])
        v = social_view(st, book, live.snapshot(), NOW + timedelta(hours=1))
        (post,) = v["posts"]
        self.assertEqual((post["status"], post["country"]), ("unconfirmed", "MLI"))
        self.assertNotIn("awa", post["title"])
        self.assertEqual(len(post["watches"]), 2)
        self.assertEqual(next(x for x in v["watches"] if x["id"] == w.id)["connector"]["status"], "ok")

    def test_watch_feed_pointing_to_internal_address_is_refused_not_fetched(self):
        st, live, lock, book = InMemoryStore(), LiveState(), threading.RLock(), WatchBook()
        book.add({"kind": "hashtag", "value": "Mali", "instance": "127.0.0.1.nip.io"})
        sync_sources(st, book)
        fetched = []
        with mock.patch.object(devserver, "assert_public_host", side_effect=WatchError("résout vers une adresse non publique")):
            reports = refresh_once(st, live, lock, fetcher=lambda u: fetched.append(u) or feed())
        self.assertEqual(fetched, [])
        self.assertFalse(reports[0].ok)
        self.assertIn("non publique", reports[0].error)

    def test_refresh_loop_wakes_up_immediately(self):
        calls = []
        stop, wake = threading.Event(), threading.Event()
        with mock.patch.object(devserver, "refresh_once", lambda *a, **k: calls.append(1)):
            t = threading.Thread(target=devserver.refresh_loop, args=(None, None, None, 3600, stop, None, wake), daemon=True)
            t.start()
            time.sleep(0.2)
            self.assertEqual(len(calls), 1)
            wake.set()
            time.sleep(0.3)
            self.assertEqual(len(calls), 2)  # sans attendre l'intervalle d'une heure
            stop.set()
            wake.set()
            t.join(2)
            self.assertFalse(t.is_alive())


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.store, self.book, self.woken = build_store(), WatchBook(), []
        load_demo_social(self.store, self.book)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, watches=self.book, on_watch_change=lambda: self.woken.append(1)))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = dict(headers or {})
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body if body is not None else {})
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.status, json.loads(r.read())
        c.close()
        return out

    OK = {"X-Nyansa": "1", "X-Dev-Role": "analyst", "X-Dev-User": "Awa"}

    def test_get_view_and_filters(self):
        s, v = self.call("GET", "/api/social")
        self.assertEqual((s, len(v["posts"]), len(v["watches"])), (200, 3, 2))
        self.assertTrue(v["demo"] and "Mastodon" in v["limits"])
        self.assertEqual(self.call("GET", "/api/social?status=unconfirmed")[1]["total"], 1)
        self.assertEqual(self.call("GET", "/api/social?status=bogus")[0], 422)
        self.assertEqual(self.call("GET", "/api/social?limit=abc")[0], 422)

    def test_create_requires_header_role_and_valid_input(self):
        body = {"kind": "hashtag", "value": "Sahel"}
        self.assertEqual(self.call("POST", "/api/social/watches", body)[0], 403)  # sans X-Nyansa
        for role in ("executive", "validator"):
            self.assertEqual(self.call("POST", "/api/social/watches", body, {**self.OK, "X-Dev-Role": role})[0], 403)
        self.assertEqual(self.call("POST", "/api/social/watches", body, {**self.OK, "Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.woken, [])
        s, w = self.call("POST", "/api/social/watches", body, self.OK)
        self.assertEqual((s, w["value"], w["created_by"], w["source_id"] is not None), (201, "Sahel", "Awa", True))
        self.assertEqual(self.woken, [1])
        self.assertTrue(self.store.get_source(w["source_id"]).active)  # la source de collecte existe déjà
        self.assertEqual(self.call("POST", "/api/social/watches", body, self.OK)[0], 422)  # doublon
        self.assertEqual(self.call("POST", "/api/social/watches", {"kind": "hashtag", "value": "Mali", "instance": "127.0.0.1"}, self.OK)[0], 422)
        self.assertEqual(self.woken, [1])  # un refus ne réveille rien

    def test_toggle_and_delete(self):
        s, w = self.call("POST", "/api/social/watches", {"kind": "hashtag", "value": "Sahel"}, self.OK)
        wid = w["id"]
        self.assertEqual(self.call("POST", f"/api/social/watches/{wid}/toggle", {}, self.OK)[0], 422)  # booléen obligatoire
        s, off = self.call("POST", f"/api/social/watches/{wid}/toggle", {"active": False}, self.OK)
        self.assertEqual((s, off["active"]), (200, False))
        self.assertFalse(self.store.get_source(w["source_id"]).active)
        self.assertEqual(self.call("POST", f"/api/social/watches/{wid}/delete", {}, self.OK)[0], 200)
        self.assertIsNone(self.book.get(wid))
        self.assertEqual(self.call("POST", f"/api/social/watches/{wid}/delete", {}, self.OK)[0], 404)
        self.assertEqual(self.call("POST", "/api/social/watches/inconnu/toggle", {"active": True}, self.OK)[0], 404)
        self.assertEqual(self.call("POST", f"/api/social/watches/{wid}/explode", {}, self.OK)[0], 404)
        actions = [a.action for a in self.store.audit_log()]
        self.assertIn("watch.add", actions)
        self.assertIn("watch.delete", actions)


if __name__ == "__main__":
    unittest.main()


class CollapseRepeatsTest(unittest.TestCase):
    def _post(self, title, source="s1", at="2026-10-20T10:00:00+00:00"):
        return {"title": title, "source_id": source, "published_at": at, "watches": ["w"]}

    def test_bot_repeats_collapsed_within_same_source(self):
        from nyansa.social import collapse_repeats

        base = "23-5 Woman Cries Out Over Army Cover Up Soldier Who Kill Hubby On Val Day In Edo"
        posts = [self._post(f"OsazuwaAkonedo NewsChronicles ~ News{n} @… {base}") for n in ("Reel", "Hour", "Recap")]
        out = collapse_repeats(posts)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["repeats"], 2)

    def test_distinct_posts_and_other_sources_kept(self):
        from nyansa.social import collapse_repeats

        a = "Attack on military post in Burkina Faso, JNIM claims responsibility today"
        out = collapse_repeats([self._post(a), self._post(a, source="s2"), self._post("Floods displace thousands in southern Mozambique this week")])
        self.assertEqual(len(out), 3)
        self.assertTrue(all(p["repeats"] == 0 for p in out))
