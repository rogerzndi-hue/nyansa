import json
import copy
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nyansa.archive import DocumentArchive
from nyansa.models import RawDocument, Source
from nyansa.pipeline import process_document
from nyansa.store import InMemoryStore

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def make_store() -> InMemoryStore:
    s = InMemoryStore()
    s.add_source(Source("m1", "Média 1", "rss", url="https://m1.test/f", default_reliability="B"))
    return s


def add(store, title, days_ago, url=None):
    when = NOW - timedelta(days=days_ago)
    process_document(store, RawDocument("m1", title, "Attentat, terroristes.", when, url=url, published_at=when))


class ArchiveTest(unittest.TestCase):
    def test_roundtrip_rebuilds_events(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            s1 = make_store()
            add(s1, "Attaque terroriste à Bamako, au Mali", 1, "https://m1.test/1")
            self.assertEqual(DocumentArchive(path).save(s1, NOW), 1)
            s2 = make_store()
            self.assertEqual(DocumentArchive(path).load(s2, NOW), 1)
            self.assertEqual(len(s2.documents()), 1)
            self.assertEqual(len(s2.events()), 1)
            self.assertEqual(s2.events()[0].country_iso3, "MLI")

    def test_retention_drops_old_documents(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            s1 = make_store()
            add(s1, "Attaque terroriste à Bamako, au Mali", 1)
            add(s1, "Attaque terroriste à Gao, au Mali", 40)
            self.assertEqual(DocumentArchive(path).save(s1, NOW), 1)
            s2 = make_store()
            self.assertEqual(DocumentArchive(path).load(s2, NOW), 1)

    def test_missing_corrupt_or_unknown_source_is_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            arch = DocumentArchive(path)
            self.assertEqual(arch.load(make_store(), NOW), 0)
            path.write_text("{pas du json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                arch.load(make_store(), NOW)
            path.write_text(json.dumps([{"source_id": "inconnue", "title": "x", "fetched_at": NOW.isoformat()}, 42, {"title": None}]), encoding="utf-8")
            self.assertEqual(arch.load(make_store(), NOW), 0)

    def test_reload_twice_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            s1 = make_store()
            add(s1, "Attaque terroriste à Bamako, au Mali", 1)
            arch = DocumentArchive(path)
            arch.save(s1, NOW)
            s2 = make_store()
            arch.load(s2, NOW)
            arch.load(s2, NOW)
            self.assertEqual(len(s2.documents()), 1)

    def test_non_list_json_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            path.write_text(json.dumps({"documents": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                DocumentArchive(path).load(make_store(), NOW)

    def test_pipeline_failure_rolls_back_entire_store(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "documents.json"
            store = make_store()
            add(store, "Attaque terroriste à Bamako, au Mali", 1, "https://m1.test/existing")

            first_time = NOW - timedelta(hours=12)
            second_time = first_time + timedelta(minutes=1)
            rows = [
                {
                    "source_id": "m1",
                    "title": "Attaque terroriste à Bamako, au Mali",
                    "text": "Attentat, terroristes.",
                    "url": "https://m1.test/archive-1",
                    "fetched_at": first_time.isoformat(),
                    "published_at": first_time.isoformat(),
                },
                {
                    "source_id": "m1",
                    "title": "Attaque terroriste à Gao, au Mali",
                    "text": "Attentat, terroristes.",
                    "url": "https://m1.test/archive-2",
                    "fetched_at": second_time.isoformat(),
                    "published_at": second_time.isoformat(),
                },
            ]
            path.write_text(json.dumps(rows), encoding="utf-8")

            attributes = (
                "_documents",
                "_doc_keys",
                "_doc_by_id",
                "_extra_tokens",
                "_version",
                "_events",
                "_by_country",
                "_audit",
            )
            before = copy.deepcopy(tuple(getattr(store, name) for name in attributes))
            sources_before = copy.deepcopy(store._sources)

            from nyansa.pipeline import process_document as original_process_document

            calls = {"count": 0}

            def process_then_fail(target_store, document):
                calls["count"] += 1
                if calls["count"] == 1:
                    original_process_document(target_store, document)
                    return
                raise RuntimeError("Panne simulée pendant le pipeline")

            with patch("nyansa.archive.process_document", side_effect=process_then_fail):
                with self.assertRaisesRegex(RuntimeError, "Panne simul"):
                    DocumentArchive(path).load(store, NOW)

            after = tuple(getattr(store, name) for name in attributes)
            self.assertEqual(calls["count"], 2)
            self.assertEqual(after, before)
            self.assertEqual(store._sources, sources_before)

