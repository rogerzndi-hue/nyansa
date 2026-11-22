import unittest
from pathlib import Path
from datetime import datetime, timezone

from nyansa.models import Source
from nyansa.runner import refresh
from nyansa.store import InMemoryStore

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Attaque terroriste près de Ouagadougou au Burkina Faso</title>
<description>Une attaque terroriste a fait des victimes au Burkina Faso.</description>
<link>https://example.org/1</link><pubDate>Wed, 14 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Météo et recettes</title><description>Rien de pertinent.</description>
<link>https://example.org/2</link></item></channel></rss>""".encode()


def store_with(*sources):
    s = InMemoryStore()
    for src in sources:
        s.add_source(src)
    return s


class RunnerTest(unittest.TestCase):
    def test_routes_and_dedups_on_second_run(self):
        s = store_with(Source("a", "A", "rss", url="https://example.org/feed", default_reliability="B"))
        first = refresh(s, lambda url: FEED, NOW)[0]
        self.assertTrue(first.ok)
        self.assertEqual((first.documents, first.routed, first.unrouted), (2, 1, 1))
        self.assertEqual(first.reasons, {"no_country": 1})
        second = refresh(s, lambda url: FEED, NOW)[0]
        self.assertEqual(second.duplicates, 2)
        self.assertEqual(len(s.events()), 1)

    def test_failing_source_does_not_block_others(self):
        s = store_with(
            Source("bad", "Bad", "rss", url="https://bad.example/feed", default_reliability="C"),
            Source("ok", "Ok", "rss", url="https://ok.example/feed", default_reliability="C"),
        )

        def fetcher(url):
            if "bad" in url:
                raise OSError("réseau coupé")
            return FEED

        reports = {r.source_id: r for r in refresh(s, fetcher, NOW)}
        self.assertFalse(reports["bad"].ok)
        self.assertIn("réseau coupé", reports["bad"].error)
        self.assertTrue(reports["ok"].ok)

    def test_inactive_and_non_rss_skipped(self):
        s = store_with(
            Source("off", "Off", "rss", url="https://x.example/f", default_reliability="C", active=False),
            Source("manual", "M", "manual", default_reliability="C"),
        )
        self.assertEqual(refresh(s, lambda u: FEED, NOW), [])

    def test_invalid_xml_is_reported(self):
        s = store_with(Source("a", "A", "rss", url="https://example.org/feed", default_reliability="B"))
        r = refresh(s, lambda u: b"<html>pas un flux", NOW)[0]
        self.assertFalse(r.ok)
        self.assertTrue(r.error)


MASTODON_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>#mali</title>
<item><guid>https://mastodon.social/@x/1</guid><link>https://mastodon.social/@x/1</link>
<pubDate>Wed, 14 Oct 2026 08:00:00 GMT</pubDate>
<description>&lt;p&gt;Attaque terroriste signalée à Bamako, au Mali.&lt;/p&gt;</description></item>
<item><guid>g2</guid><link>https://mastodon.social/@x/2</link></item></channel></rss>""".encode()


class UntitledFeedTest(unittest.TestCase):
    """Régression : Mastodon n'envoie souvent pas de <title> ; ces éléments étaient écartés en silence (flux « OK », 0 document)."""

    def test_item_without_title_uses_text_and_empty_item_is_counted(self):
        from nyansa.ingest import parse_feed_stats

        docs, skipped = parse_feed_stats(MASTODON_FEED, "m")
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0].title.startswith("Attaque terroriste signalée à Bamako"))
        self.assertEqual(skipped, 1)

    def test_long_text_gives_short_title(self):
        from nyansa.ingest import TITLE_FALLBACK_CHARS, parse_feed

        feed = ("<rss version='2.0'><channel><item><description>" + "mot " * 200 + "</description></item></channel></rss>").encode()
        title = parse_feed(feed, "m")[0].title
        self.assertLessEqual(len(title), TITLE_FALLBACK_CHARS + 1)
        self.assertTrue(title.endswith("…"))

    def test_runner_routes_untitled_post_and_reports_skipped(self):
        s = store_with(Source("m", "M", "rss", url="https://mastodon.social/tags/mali.rss", default_reliability="E", channel="social"))
        r = refresh(s, lambda u: MASTODON_FEED, NOW)[0]
        self.assertEqual((r.documents, r.routed, r.skipped), (1, 1, 1))


class CumulativeCountersTest(unittest.TestCase):
    """Régression : après le 2e cycle, tout est « doublon » ; le panneau Sources affichait « 0 rattachés, 0 à qualifier »."""

    def test_totals_survive_second_cycle(self):
        s = store_with(Source("a", "A", "rss", url="https://example.org/feed", default_reliability="B"))
        refresh(s, lambda u: FEED, NOW)
        second = refresh(s, lambda u: FEED, NOW)[0]
        self.assertEqual((second.routed, second.unrouted, second.duplicates), (0, 0, 2))
        self.assertEqual((second.total, second.total_routed, second.total_unrouted), (2, 1, 1))


class CamelCaseHashtagTest(unittest.TestCase):
    """Régression : « #BurkinaFaso » n'était pas reconnu comme pays (publication « pays non reconnu »)."""

    def test_expand_hashtags(self):
        from nyansa.runner import expand_hashtags

        self.assertEqual(expand_hashtags("#ThomasSankara, leader of #BurkinaFaso, killed in a #coup"), "Thomas Sankara, leader of Burkina Faso, killed in a #coup")
        self.assertEqual(expand_hashtags("#mali #Niger #NEWS"), "#mali #Niger #NEWS")

    def test_post_with_camelcase_country_hashtag_is_routed(self):
        feed = ("""<rss version="2.0"><channel><item><link>https://mastodon.social/@x/7</link>
<description>Thomas Sankara was killed by soldiers during a mutiny in 1987. #BurkinaFaso #coup</description></item></channel></rss>""").encode()
        s = store_with(Source("m", "M", "rss", url="https://mastodon.social/tags/x.rss", default_reliability="E", channel="social"))
        r = refresh(s, lambda u: feed, NOW)[0]
        self.assertEqual((r.routed, r.unrouted), (1, 0))
        self.assertEqual(s.events()[0].country_iso3, "BFA")


if __name__ == "__main__":
    unittest.main()


SOCIAL_FEED = ("""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>@jean@mastodon.social : attaque terroriste à Bamako, au Mali</title>
<description>""" + "Détails écrits par @awa.diallo, écrire à awa@exemple.org. " * 30 + """</description>
<link>https://mastodon.social/@x/1</link><pubDate>Wed, 14 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>""").encode()


class SocialChannelTest(unittest.TestCase):
    def setUp(self):
        from nyansa import services

        self.services = services
        self.store = store_with(Source("m", "Mastodon #Mali", "rss", url="https://mastodon.example/tags/Mali.rss",
                                       default_reliability="E", channel="social"))
        refresh(self.store, lambda url: SOCIAL_FEED, NOW)

    def test_handles_and_emails_removed_and_text_truncated(self):
        doc = self.store.documents()[0]
        self.assertNotIn("jean", doc.title)
        self.assertNotIn("awa.diallo", doc.text)
        self.assertNotIn("exemple.org", doc.text)
        self.assertIn("@…", doc.text)
        self.assertIn("[e-mail]", doc.text)
        self.assertLessEqual(len(doc.text), 400)

    def test_fediverse_handle_replaced_cleanly(self):
        from nyansa.runner import anonymize

        self.assertEqual(anonymize("@jean@mastodon.social : alerte"), "@… : alerte")
        self.assertEqual(anonymize("écrire à awa@exemple.org"), "écrire à [e-mail]")
        self.assertEqual(anonymize("via @awa.diallo, merci"), "via @…, merci")

    def test_event_exposes_social_channel_and_source_name(self):
        (event,) = self.store.events()
        src = self.services.event_to_dict(event, self.store)["sources"][0]
        self.assertEqual((src["channel"], src["source_name"], src["reliability"]), ("social", "Mastodon #Mali", "E"))
        self.assertEqual(src["url"], "https://mastodon.social/@x/1")

    def test_social_only_event_is_low_probability(self):
        (event,) = self.store.events()
        self.assertEqual(event.best_reliability, "E")
        self.assertLessEqual(event.probability, 2)

    def test_channel_validated_when_loading_sources(self):
        import json
        import tempfile
        from pathlib import Path

        from nyansa.ingest import load_sources

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            base = {"id": "x", "name": "X", "kind": "rss", "url": "https://x.example/f", "default_reliability": "E"}
            path.write_text(json.dumps([{**base, "channel": "social"}]), encoding="utf-8")
            self.assertEqual(load_sources(path)[0].channel, "social")
            path.write_text(json.dumps([{**base, "channel": "radio"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sources(path)

    def test_shipped_config_social_sources_are_low_reliability(self):
        from nyansa.ingest import load_sources

        social = [s for s in load_sources(Path(__file__).resolve().parents[2] / "config" / "sources.json") if s.channel == "social"]
        self.assertTrue(social)
        self.assertTrue(all(s.default_reliability in ("D", "E", "F") for s in social))
