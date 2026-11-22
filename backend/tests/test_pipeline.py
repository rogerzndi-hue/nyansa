"""Tests du pipeline, du stockage, du scoring et de l'ingestion de flux."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nyansa import services
from nyansa.ingest import FeedError, _assert_public_url, _PublicRedirectHandler, load_sources, parse_feed
from nyansa.models import Event, Source, SourceRef
from nyansa.pipeline import process_document
from nyansa.scoring import ScoringConfig, compute_country_risk, score_events
from nyansa.store import InMemoryStore
from nyansa.taxonomy import Domain

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def make_store() -> InMemoryStore:
    store = InMemoryStore()
    store.add_source(Source("agence", "Agence régionale", "rss", "B", language="fr"))
    store.add_source(Source("media", "Média international", "rss", "B", language="en"))
    store.add_source(Source("ong", "ONG locale", "rss", "C", language="fr"))
    store.add_source(Source("manual", "Saisie manuelle", "manual", "C"))
    return store


def ingest(store, title, text="", source="agence", published=NOW):
    return services.ingest_manual(store, title=title, text=text, source_id=source, published_at=published)


class PipelineTest(unittest.TestCase):
    def test_routed_event_has_country_domain_and_explainable_fields(self):
        store = make_store()
        out = ingest(store, "Attaque armée contre un convoi près de Ouagadougou", "Des hommes armés ont attaqué un convoi au Burkina Faso.")
        self.assertEqual((out.status, out.created), ("routed", True))
        (event,) = store.events()
        self.assertEqual((event.country_iso3, event.domain, event.event_type), ("BFA", Domain.SEC, "armed_attack"))
        self.assertEqual(event.admiralty, "B3")
        self.assertEqual((event.probability, event.impact, event.criticality, event.level), (3, 4, 12, "high"))
        self.assertIn("attaque", event.evidence)

    def test_exact_duplicate_is_dropped_but_other_source_is_kept(self):
        store = make_store()
        title, text = "Épidémie de choléra en RDC", "Les autorités sanitaires confirment de nouveaux cas."
        self.assertEqual(ingest(store, title, text).status, "routed")
        self.assertEqual(ingest(store, title, text).status, "duplicate")
        self.assertEqual(ingest(store, title, text, source="media").status, "routed")
        self.assertEqual(len(store.documents()), 2)

    def test_two_sources_merge_into_one_event_and_raise_credibility(self):
        store = make_store()
        ingest(store, "Attaque armée contre un convoi près de Ouagadougou", source="agence")
        out = ingest(store, "Convoi visé par une attaque armée près de Ouagadougou", source="media", published=NOW + timedelta(hours=3))
        self.assertEqual((out.status, out.created), ("routed", False))
        (event,) = store.events()
        self.assertEqual((event.source_count, event.info_credibility, event.admiralty), (2, 2, "B2"))
        self.assertEqual((event.probability, event.criticality), (4, 16))
        self.assertEqual(event.level, "critical")
        ingest(store, "Attaque armée contre un convoi à Ouagadougou", source="ong", published=NOW + timedelta(hours=5))
        self.assertEqual((event.info_credibility, event.admiralty), (1, "B1"))

    def test_similar_titles_far_apart_in_time_are_separate_events(self):
        store = make_store()
        ingest(store, "Attaque armée contre un convoi près de Ouagadougou", source="agence", published=NOW - timedelta(days=5))
        ingest(store, "Attaque armée contre un convoi près de Ouagadougou", source="media")
        self.assertEqual(len(store.events()), 2)

    def test_unrouted_reasons_are_explicit_never_guessed(self):
        store = make_store()
        self.assertEqual(ingest(store, "Les prix du cacao en forte hausse").reason, "no_country")
        self.assertEqual(ingest(store, "Attaque dans l'est du Congo").reason, "ambiguous_country")
        self.assertEqual(ingest(store, "Inauguration d'un stade à Dakar", "Une cérémonie.").reason, "no_domain")
        self.assertEqual(ingest(store, "Piraterie dans le golfe de Guinée").reason, "no_country")
        self.assertEqual(len(store.unrouted()), 4)
        self.assertEqual(store.events(), [])

    def test_unknown_source_is_an_error(self):
        with self.assertRaises(ValueError):
            ingest(make_store(), "Attaque à Dakar", source="inconnue")

    def test_future_publication_date_is_capped(self):
        store = make_store()
        ingest(store, "Attentat à Bamako", published=datetime.now(timezone.utc) + timedelta(days=30))
        (event,) = store.events()
        self.assertLessEqual(event.occurred_at, datetime.now(timezone.utc))

    def test_html_is_stripped_and_language_detected(self):
        store = make_store()
        ingest(store, "<b>Épidémie</b> de choléra au Malawi", "<p>Les autorités ont annoncé que les cas sont en hausse dans les districts.</p>")
        (event,) = store.events()
        self.assertEqual(event.title, "Épidémie de choléra au Malawi")
        self.assertEqual(event.language, "fr")
        self.assertNotIn("<", event.summary)

    def test_event_filters(self):
        store = make_store()
        ingest(store, "Attaque armée à Bamako")
        ingest(store, "Épidémie de choléra au Malawi")
        self.assertEqual({e.country_iso3 for e in store.events(domain=Domain.HUM)}, {"MWI"})
        self.assertEqual({e.country_iso3 for e in store.events(country="MLI")}, {"MLI"})
        self.assertEqual(len(store.events(min_criticality=12)), 2)  # source B, crédibilité 3 : 3 x 4 = 12
        self.assertEqual(len(store.events(min_criticality=13)), 0)
        self.assertEqual(len(store.events(since=NOW + timedelta(days=1))), 0)


def make_event(days_ago, domain=Domain.SEC, impact=4, source="agence", reliability="B", cred_sources=1, title=None, publisher_id=None):
    refs = [SourceRef(f"d{i}", f"{source}{i}" if i else source, reliability, publisher_id=publisher_id) for i in range(cred_sources)]
    return Event(
        occurred_at=NOW - timedelta(days=days_ago), country_iso3="MLI", domain=domain,
        event_type="armed_attack", impact=impact, title=title or f"Événement {days_ago}j",
        summary="", language="fr", refs=refs,
    )


class ScoringTest(unittest.TestCase):
    def test_no_events_gives_zero_with_a_coverage_warning(self):
        risk = compute_country_risk([], "MLI", NOW)
        self.assertEqual((risk.score, risk.level, risk.confidence), (0.0, "low", "low"))
        self.assertIn("absence de données", risk.notes[0])

    def test_more_events_and_more_recent_events_raise_the_score(self):
        one = score_events([make_event(1)], NOW)
        five = score_events([make_event(1) for _ in range(5)], NOW)
        old = score_events([make_event(25)], NOW)
        self.assertLess(old, one)
        self.assertLess(one, five)

    def test_score_is_bounded_and_saturates(self):
        huge = score_events([make_event(0, impact=5, cred_sources=3) for _ in range(500)], NOW)
        self.assertLessEqual(huge, 100.0)
        self.assertGreater(huge, 60.0)

    def test_events_outside_window_or_in_the_future_are_ignored(self):
        self.assertEqual(score_events([make_event(45), make_event(-3)], NOW), 0.0)

    def test_concentrated_stress_scores_higher_than_the_same_events_spread_out(self):
        # Choix de conception (max_share = 0.7) : le domaine le plus chaud domine le score global.
        single = score_events([make_event(1, Domain.SEC) for _ in range(4)], NOW)
        spread = score_events([make_event(1, d) for d in (Domain.SEC, Domain.SOC, Domain.GOV, Domain.ECO)], NOW)
        self.assertGreater(spread, 0)
        self.assertGreater(single, spread)

    def test_explanation_ranks_the_heaviest_recent_event_first(self):
        events = [make_event(1, impact=5, title="Coup"), make_event(20, impact=2, title="Grève"), make_event(2, impact=3, title="Marche")]
        risk = compute_country_risk(events, "MLI", NOW, languages=["fr", "en"])
        self.assertEqual(risk.contributions[0].title, "Coup")
        self.assertTrue(all(c.points >= 0 for c in risk.contributions))
        self.assertGreater(risk.domain_scores["SEC"], risk.domain_scores["GEO"])

    def test_trend_compares_with_30_days_earlier(self):
        recent_only = compute_country_risk([make_event(1)], "MLI", NOW)
        self.assertGreater(recent_only.trend_30d, 0)
        self.assertEqual(recent_only.trend_30d, round(recent_only.score - 0.0, 1))

    def test_confidence_and_bias_notes(self):
        few = compute_country_risk([make_event(1)], "MLI", NOW, languages=["fr"])
        self.assertEqual(few.confidence, "low")
        self.assertTrue(any("score fragile" in n for n in few.notes))
        many = [make_event(i, source=f"s{i % 5}") for i in range(12)]
        rich = compute_country_risk(many, "MLI", NOW, languages=["fr", "en", "ar"])
        self.assertEqual(rich.confidence, "high")
        self.assertEqual(rich.notes, ())

    def test_multiple_feeds_from_one_publisher_do_not_raise_confidence(self):
        events = [
            make_event(
                i, source=f"beninwebtv-{i % 5}",
                publisher_id="beninwebtv",
            )
            for i in range(12)
        ]
        risk = compute_country_risk(events, "MLI", NOW, languages=["fr", "en", "ar"])
        self.assertEqual(risk.confidence, "low")
        self.assertEqual(risk.source_count, 5)

    def test_weights_are_configurable(self):
        events = [make_event(1, Domain.CYB, impact=5) for _ in range(3)]
        default = score_events(events, NOW)
        heavy = score_events(events, NOW, ScoringConfig(domain_weights={d: (5.0 if d == Domain.CYB else 0.1) for d in Domain}))
        self.assertGreater(heavy, 0)
        self.assertNotEqual(default, heavy)


class ServicesTest(unittest.TestCase):
    def test_risk_map_covers_54_countries_sorted(self):
        store = make_store()
        ingest(store, "Attentat à Bamako", "Un attentat terroriste a visé le centre de Bamako, Mali.")
        ranking = services.risk_map(store, NOW)
        self.assertEqual(len(ranking), 54)
        self.assertEqual(ranking[0].iso3, "MLI")
        self.assertTrue(all(a.score >= b.score for a, b in zip(ranking, ranking[1:])))

    def test_event_view_exposes_derived_fields(self):
        store = make_store()
        ingest(store, "Attentat à Bamako")
        view = services.event_to_dict(store.events()[0])
        self.assertEqual((view["country"], view["region"], view["admiralty"], view["level"]), ("MLI", "WEST", "B3", "high"))

    def test_coverage_report_flags_missing_countries_and_languages(self):
        store = make_store()
        ingest(store, "Attentat à Bamako", "Les autorités ont annoncé que le bilan est lourd dans la capitale.")
        ingest(store, "Les prix du cacao en forte hausse")
        report = services.coverage_report(store, NOW + timedelta(hours=1))
        self.assertEqual(report["documents"], 2)
        self.assertEqual(report["routing_rate"], 0.5)
        self.assertIn("MLI", report["countries_single_source"])
        self.assertNotIn("MLI", report["countries_without_events"])
        self.assertIn("KEN", report["countries_without_events"])
        self.assertEqual(len(report["countries_without_events"]), 53)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Exemple</title>
<item><title>Attaque à Niamey</title><link>https://exemple.test/a</link>
<description>&lt;p&gt;Une &lt;b&gt;attaque&lt;/b&gt; a eu lieu.&lt;/p&gt;</description>
<pubDate>Tue, 13 Oct 2026 09:30:00 +0200</pubDate></item>
<item><title></title><description>sans titre</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Exemple</title>
<entry><title>Cholera outbreak in Malawi</title><link href="https://exemple.test/b"/>
<updated>2026-10-13T08:00:00Z</updated><summary>Cases are rising.</summary></entry></feed>"""


class IngestTest(unittest.TestCase):
    def test_parse_rss(self):
        doc, untitled = parse_feed(RSS.encode(), "agence", NOW)
        self.assertEqual((doc.title, doc.text, doc.url), ("Attaque à Niamey", "Une attaque a eu lieu.", "https://exemple.test/a"))
        self.assertEqual(doc.published_at, datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc))
        # un élément sans titre mais avec texte est conservé (titre de secours = début du texte), jamais écarté en silence
        self.assertEqual((untitled.title, untitled.text), ("sans titre", "sans titre"))

    def test_parse_atom(self):
        (doc,) = parse_feed(ATOM.encode(), "media", NOW)
        self.assertEqual((doc.title, doc.url), ("Cholera outbreak in Malawi", "https://exemple.test/b"))
        self.assertEqual(doc.published_at, datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc))

    def test_hostile_or_invalid_feeds_are_rejected(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'
        for bad in (bomb, b"<html></html>", b"pas du xml"):
            with self.assertRaises(FeedError):
                parse_feed(bad, "x")

    def test_feed_end_to_end_through_the_pipeline(self):
        store = make_store()
        for doc in parse_feed(RSS.encode(), "agence", NOW) + parse_feed(ATOM.encode(), "media", NOW):
            process_document(store, doc)
        self.assertEqual({e.country_iso3 for e in store.events()}, {"NER", "MWI"})

    def test_fetch_url_guard_rejects_private_and_invalid_destinations(self):
        for url in (
            "http://127.0.0.1/feed",
            "http://10.0.0.1/feed",
            "file:///etc/passwd",
            "https://user:pass@example.com/feed",
            "http://example.com:8080/feed",
        ):
            with self.subTest(url=url):
                with self.assertRaises(FeedError):
                    _assert_public_url(url)

    def test_https_redirect_to_http_is_rejected(self):
        handler = _PublicRedirectHandler()
        request = urllib.request.Request("https://example.com/feed")

        with self.assertRaises(FeedError):
            handler.redirect_request(
                request, None, 302, "Found", {},
                "http://example.com/feed",
            )

    def test_redirect_to_private_address_is_rejected(self):
        handler = _PublicRedirectHandler()
        request = urllib.request.Request("https://example.com/feed")

        with self.assertRaises(FeedError):
            handler.redirect_request(
                request, None, 302, "Found", {},
                "http://127.0.0.1/admin",
            )

    def test_load_sources_skips_templates_without_url(self):
        entries = [
            {"id": "ok", "name": "Ok", "kind": "rss", "url": "https://exemple.test/rss", "default_reliability": "B"},
            {"id": "modele", "name": "À renseigner", "kind": "rss", "url": None, "default_reliability": "C"},
            {"id": "manual", "name": "Manuel", "kind": "manual", "url": None, "default_reliability": "C"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            self.assertEqual([s.id for s in load_sources(path)], ["ok", "manual"])


if __name__ == "__main__":
    unittest.main()


class StaleRepostTest(unittest.TestCase):
    def _run(self, url, published):
        from nyansa.models import RawDocument, Source
        from nyansa.pipeline import process_document
        from nyansa.store import InMemoryStore

        store = InMemoryStore()
        store.add_source(Source("s", "S", "rss", "B"))
        return process_document(store, RawDocument("s", "Attaque terroriste à Bamako, au Mali", "Attentat, terroristes.",
                                                   published, url=url, published_at=published))

    def test_old_article_reposted_is_rejected_as_stale(self):
        from datetime import datetime, timezone
        out = self._run("https://radioblackout.org/2026/06/mali-la-guerre/", datetime(2026, 10, 20, tzinfo=timezone.utc))
        self.assertEqual((out.status, out.reason), ("unrouted", "stale"))

    def test_recent_or_undated_url_is_kept(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        for url in ("https://x.test/2026/10/mali-attaque", "https://x.test/mali-attaque", "https://x.test/2026-10-19-mali", None):
            with self.subTest(url=url):
                self.assertNotEqual(self._run(url, now).reason, "stale")

    def test_url_date_parsing(self):
        from datetime import datetime, timezone
        from nyansa.pipeline import url_date
        self.assertEqual(url_date("https://a.test/2026/09/x"), datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(url_date("https://a.test/2026-09-12-x"), datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertIsNone(url_date("https://a.test/item/123456"))
        self.assertIsNone(url_date("https://a.test/2026/13/x"))
