import copy
import json
import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nyansa.deliverables.builder import build_report, event_status, parse_params
from nyansa.deliverables.catalog import LABELS, SECTIONS, TEMPLATES, catalog
from nyansa.deliverables.report import Conflict, Forbidden, Invalid, NotFound, ReportStore, check_report
from nyansa.models import Source
from nyansa.runner import refresh
from nyansa.store import InMemoryStore

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def feed(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>d</description><pubDate>{d}</pubDate></item>" for t, u, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


def pub(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")


def make_store() -> InMemoryStore:
    s = InMemoryStore()
    for src in (Source("m1", "Média 1", "rss", url="https://m1.test/f", default_reliability="B"),
                Source("m2", "Média 2", "rss", url="https://m2.test/f", default_reliability="C"),
                Source("soc", "Mastodon #Mali", "rss", url="https://soc.test/f", default_reliability="E", channel="social")):
        s.add_source(src)
    pages = {
        "https://m1.test/f": feed(("Attaque terroriste à Bamako, au Mali", "https://m1.test/1", pub(1)),
                                  ("Grève générale à Lagos, au Nigeria", "https://m1.test/2", pub(2)),
                                  ("Inondations meurtrières à Accra, au Ghana", "https://m1.test/3", pub(60))),
        "https://m2.test/f": feed(("Attaque terroriste à Bamako, au Mali", "https://m2.test/1", pub(1))),
        "https://soc.test/f": feed(("Attaque terroriste signalée à Niamey, au Niger", "https://soc.test/p/1", pub(1)),
                                   ("Grève générale à Lagos, au Nigeria", "https://soc.test/p/2", pub(2))),
    }
    refresh(s, lambda url: pages[url], NOW)
    return s


def params(**kw):
    base = {"template_id": "analysis_note", "analyst": "Awa", "organization": "Org", "start": "2026-10-01", "end": "2026-10-15"}
    return parse_params({**base, **kw})


def fill(report: dict) -> list[dict]:
    secs = copy.deepcopy(report["sections"])
    for s in secs:
        if s["mode"] == "analyst":
            s["blocks"][0]["text"] = "Texte de l'analyste."
    return secs


class CatalogTest(unittest.TestCase):
    def test_25_distinct_templates_with_known_sections(self):
        self.assertEqual(len(TEMPLATES), 25)
        self.assertEqual(len({t.sections for t in TEMPLATES.values()}), 25)
        self.assertTrue(all(k in SECTIONS for t in TEMPLATES.values() for k in t.sections))
        self.assertTrue(all(t.sections[-2:] == ("sources", "coverage") for t in TEMPLATES.values()))
        self.assertEqual(len(catalog("en")), 25)

    def test_labels_cover_both_languages(self):
        self.assertEqual(set(LABELS["fr"]), set(LABELS["en"]))

    def test_executive_brief_has_decision_ready_sections_and_methodology(self):
        keys = TEMPLATES["executive_brief"].sections
        self.assertEqual(keys[:6], ("summary", "key_facts", "methodology", "impacts", "recommendations", "conclusion"))
        self.assertIn("coverage", keys)
        self.assertIn("sources", keys)
        self.assertIn("limites", SECTIONS["methodology"]["fr"].lower())


class BuilderTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_every_template_builds_in_both_languages(self):
        for tid, t in TEMPLATES.items():
            for lang in ("fr", "en"):
                raw = {"template_id": tid, "language": lang, "start": "2026-10-01", "end": "2026-10-15",
                       "countries": ["MLI"] if t.scope == "country" else [], "region": "WEST" if t.scope == "region" else None}
                r = build_report(self.store, parse_params(raw), NOW)
                self.assertEqual([s["key"] for s in r["sections"]][-1], "sources", tid)
                self.assertIn(r["sections"][-2]["key"], ("coverage",), tid)

    def test_each_fact_has_resolvable_sources(self):
        r = build_report(self.store, params(), NOW)
        n = len(r["sources"])
        facts = [b for s in r["sections"] for b in s["blocks"] if b["kind"] == "fact"]
        self.assertTrue(facts)
        for b in facts:
            self.assertTrue(b["refs"])
            self.assertTrue(all(1 <= x <= n for x in b["refs"]))
        self.assertEqual([s["n"] for s in r["sources"]], list(range(1, n + 1)))

    def test_status_rules_for_social_and_media(self):
        r = build_report(self.store, params(), NOW)
        by_text = {b["text"]: b for s in r["sections"] for b in s["blocks"] if b["kind"] == "fact"}
        status = {k.split(". ")[1].split(" [")[0]: v["status"] for k, v in by_text.items()}
        self.assertEqual(status["Attaque terroriste à Bamako, au Mali"], "confirmed")  # 2 médias
        self.assertEqual(status["Attaque terroriste signalée à Niamey, au Niger"], "unconfirmed_social")
        self.assertEqual(status["Grève générale à Lagos, au Nigeria"], "single_source")  # 1 média + 1 réseau : pas confirmé

    def test_period_country_and_domain_filters(self):
        titles = lambda r: " ".join(b["text"] for s in r["sections"] for b in s["blocks"] if b["kind"] == "fact")  # noqa: E731
        old = build_report(self.store, params(start="2026-08-01", end="2026-09-01"), NOW)
        self.assertIn("Ghana", titles(old))
        recent = build_report(self.store, params(), NOW)
        self.assertNotIn("Ghana", titles(recent))
        mali = build_report(self.store, params(countries=["MLI"]), NOW)
        self.assertNotIn("Nigeria", titles(mali))
        soc = build_report(self.store, params(domains=["SOC"]), NOW)
        self.assertIn("Grève", titles(soc))
        self.assertNotIn("terroriste", titles(soc))

    def test_empty_scope_is_flagged_not_invented(self):
        r = build_report(self.store, params(countries=["NAM"]), NOW)
        self.assertFalse([b for s in r["sections"] for b in s["blocks"] if b["kind"] == "fact"])
        self.assertTrue(r["warnings"])
        self.assertIn("Aucun événement", r["sections"][1]["blocks"][0]["text"])

    def test_demo_flag_is_carried(self):
        r = build_report(self.store, params(), NOW, demo=True)
        self.assertTrue(r["meta"]["demo"])
        self.assertTrue(any("FICTIVES" in w for w in r["warnings"]))

    def test_english_labels_and_instructions_not_interpreted(self):
        r = build_report(self.store, params(language="en", instructions="Insiste sur le Mali"), NOW)
        self.assertEqual(r["sections"][0]["title"], SECTIONS["object"]["en"])
        self.assertTrue(any("not interpreted" in w for w in r["warnings"]))

    def test_parameter_validation(self):
        for bad in ({"template_id": "nope"}, {"language": "ar"}, {"countries": ["XXX"]}, {"region": "MARS"}, {"detail": "huge"},
                    {"classification": "SECRET"}, {"start": "pas une date"}, {"start": "2026-10-10", "end": "2026-10-01"},
                    {"template_id": "country_profile"}, {"template_id": "regional_assessment"}, {"sections": ["scenarios"]}):
            with self.assertRaises(ValueError, msg=bad):
                parse_params({"template_id": "analysis_note", **bad})

    def test_section_selection_keeps_sources_and_coverage(self):
        r = build_report(self.store, params(sections=["object"]), NOW)
        self.assertEqual([s["key"] for s in r["sections"]], ["object", "coverage", "sources"])

    def test_event_status_function(self):
        for e in self.store.events():
            self.assertIn(event_status(e, self.store), ("confirmed", "single_source", "unconfirmed_social"))


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.reports = ReportStore()
        self.built = build_report(self.store, params(), NOW)
        self.r = self.reports.create(self.built, "awa", "analyst")

    def test_write_failure_restores_create(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            store = ReportStore(path)

            with mock.patch(
                "nyansa.deliverables.report.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    store.create(self.built, "awa", "analyst")

            self.assertEqual(store._reports, {})
            self.assertFalse(path.exists())

    def test_write_failure_restores_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            store = ReportStore(path)
            created = store.create(self.built, "awa", "analyst")
            before_memory = copy.deepcopy(store._reports)
            before_disk = path.read_bytes()

            with mock.patch(
                "nyansa.deliverables.report.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    store.update(
                        created["id"], "awa", "analyst",
                        expected_version=1,
                        title="Titre modifi?",
                    )

            self.assertEqual(store._reports, before_memory)
            self.assertEqual(path.read_bytes(), before_disk)

    def test_write_failure_restores_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            store = ReportStore(path)
            created = store.create(self.built, "awa", "analyst")

            store.update(
                created["id"], "awa", "analyst",
                expected_version=1,
                sections=fill(created),
            )

            before_memory = copy.deepcopy(store._reports)
            before_disk = path.read_bytes()

            with mock.patch(
                "nyansa.deliverables.report.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    store.transition(
                        created["id"], "submit", "awa", "analyst"
                    )

            self.assertEqual(store._reports, before_memory)
            self.assertEqual(path.read_bytes(), before_disk)

    def test_write_failure_restores_record_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            store = ReportStore(path)
            created = store.create(self.built, "awa", "analyst")
            before_memory = copy.deepcopy(store._reports)
            before_disk = path.read_bytes()

            with mock.patch(
                "nyansa.deliverables.report.atomic_write_json",
                side_effect=OSError("Panne disque simul?e"),
            ):
                with self.assertRaises(OSError):
                    store.record_export(
                        created["id"], "awa", "analyst", "pdf"
                    )

            self.assertEqual(store._reports, before_memory)
            self.assertEqual(path.read_bytes(), before_disk)


    def ready(self):
        return self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=fill(self.r))

    def test_submit_blocked_until_analyst_sections_filled(self):
        with self.assertRaises(Invalid) as cm:
            self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        self.assertTrue(any("vide" in i["message"] for i in cm.exception.issues))
        self.ready()
        self.assertEqual(self.reports.transition(self.r["id"], "submit", "awa", "analyst")["status"], "in_review")

    def test_na_section_counts_as_filled(self):
        secs = copy.deepcopy(self.r["sections"])
        for s in secs:
            if s["mode"] == "analyst":
                s["na"] = True
        self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)
        self.assertEqual(self.reports.transition(self.r["id"], "submit", "awa", "analyst")["status"], "in_review")

    def test_four_eyes_validation(self):
        self.ready()
        self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        with self.assertRaises(Forbidden):  # même personne
            self.reports.transition(self.r["id"], "validate", "awa", "admin")
        with self.assertRaises(Forbidden):  # mauvais rôle
            self.reports.transition(self.r["id"], "validate", "bob", "analyst")
        done = self.reports.transition(self.r["id"], "validate", "bob", "validator")
        self.assertEqual((done["status"], done["validated_by"]), ("validated", "bob"))

    def test_locked_after_submit_and_reject_needs_reason(self):
        self.ready()
        self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        with self.assertRaises(Conflict):
            self.reports.update(self.r["id"], "awa", "analyst", expected_version=2, title="x")
        with self.assertRaises(Invalid):
            self.reports.transition(self.r["id"], "reject", "bob", "validator", " ")
        self.assertEqual(self.reports.transition(self.r["id"], "reject", "bob", "validator", "Source à vérifier")["status"], "draft")
        self.reports.update(self.r["id"], "awa", "analyst", expected_version=2, title="Nouveau titre")

    def test_version_conflict_and_noop_update(self):
        self.ready()
        with self.assertRaises(Conflict):
            self.reports.update(self.r["id"], "carl", "analyst", expected_version=1, title="Autre")
        same = self.reports.update(self.r["id"], "awa", "analyst", expected_version=2)
        self.assertEqual(same["version"], 2)

    def test_fact_without_source_blocks_submission(self):
        secs = fill(self.r)
        secs[0]["blocks"].append({"id": "new1", "kind": "fact", "text": "Affirmation sans source", "refs": [], "auto": False})
        self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)
        with self.assertRaises(Invalid) as cm:
            self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        self.assertTrue(any("sans source" in i["message"] for i in cm.exception.issues))

    def test_reference_to_unknown_source_blocks_submission(self):
        secs = fill(self.r)
        secs[0]["blocks"][0].update(kind="fact", refs=[999])
        self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)
        with self.assertRaises(Invalid):
            self.reports.transition(self.r["id"], "submit", "awa", "analyst")

    def test_auto_blocks_are_immutable_but_removable(self):
        secs = fill(self.r)
        auto_sec = next(s for s in secs if s["key"] == "summary")
        auto_sec["blocks"][0]["text"] = "Texte réécrit"
        with self.assertRaises(Invalid):
            self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)
        auto_sec["blocks"][0] = copy.deepcopy(next(s for s in self.r["sections"] if s["key"] == "summary")["blocks"][0])
        auto_sec["blocks"].pop(1)  # retirer un bloc calculé est un choix éditorial autorisé
        self.assertEqual(self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)["version"], 2)

    def test_sources_section_mandatory_and_untouchable(self):
        secs = [s for s in fill(self.r) if s["key"] != "sources"]
        with self.assertRaises(Invalid):
            self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)
        secs = fill(self.r)
        next(s for s in secs if s["key"] == "sources")["blocks"] = []
        with self.assertRaises(Invalid):
            self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs)

    def test_input_validation(self):
        for bad in ([{"key": "inconnue", "blocks": []}], "pas une liste", [{"key": "object", "blocks": [{"id": "z", "kind": "nope", "text": "x"}]}],
                    [{"key": "object", "blocks": [{"id": "z", "kind": "estimate", "text": "x" * 20_001}]}],
                    [{"key": "object", "blocks": [{"id": "z", "kind": "estimate", "text": "x", "refs": ["1"]}]}]):
            with self.assertRaises(Invalid, msg=str(bad)[:40]):
                self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=bad)

    def test_custom_section_and_meta(self):
        secs = fill(self.r)
        secs.insert(1, {"key": "custom:ab12", "title": "Annexe", "blocks": [{"id": "c1", "kind": "hypothesis", "text": "Piste à explorer"}]})
        out = self.reports.update(self.r["id"], "awa", "analyst", expected_version=1, sections=secs, meta={"classification": "CONFIDENTIEL"})
        self.assertEqual(out["meta"]["classification"], "CONFIDENTIEL")
        self.assertEqual(out["sections"][1]["title"], "Annexe")
        with self.assertRaises(Invalid):
            self.reports.update(self.r["id"], "awa", "analyst", expected_version=2, meta={"classification": "SECRET"})

    def test_role_visibility(self):
        with self.assertRaises(Forbidden):
            self.reports.get(self.r["id"], "executive")  # un décideur ne voit pas les brouillons
        self.assertFalse(self.reports.list("executive"))
        self.ready()
        self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        self.reports.transition(self.r["id"], "validate", "bob", "validator")
        self.assertEqual(self.reports.get(self.r["id"], "executive")["status"], "validated")
        with self.assertRaises(Forbidden):
            self.reports.get(self.r["id"], "partner")
        with self.assertRaises(Forbidden):
            self.reports.create(self.built, "x", "executive")
        with self.assertRaises(NotFound):
            self.reports.get("inconnu", "admin")

    def test_duplicate_and_archive(self):
        copy_ = self.reports.duplicate(self.r["id"], "carl", "analyst")
        self.assertEqual((copy_["status"], copy_["version"], copy_["created_by"]), ("draft", 1, "carl"))
        self.assertTrue(copy_["title"].endswith("(copie)"))
        self.assertEqual(self.reports.transition(self.r["id"], "archive", "awa", "analyst")["status"], "archived")
        with self.assertRaises(Conflict):
            self.reports.transition(self.r["id"], "archive", "awa", "analyst")

    def test_versions_and_diff(self):
        self.ready()
        out = self.reports.versions(self.r["id"], "analyst")
        self.assertEqual([v["version"] for v in out], [1, 2])
        changes = self.reports.diff(self.r["id"], 1, 2, "analyst")
        self.assertTrue(changes)
        self.assertTrue(any("Texte de l'analyste." in line for c in changes for line in c["lines"]))
        with self.assertRaises(NotFound):
            self.reports.diff(self.r["id"], 1, 9, "analyst")

    def test_audit_trail(self):
        self.ready()
        self.reports.transition(self.r["id"], "submit", "awa", "analyst")
        self.reports.record_export(self.r["id"], "awa", "analyst", "pdf")
        actions = [a["action"] for a in self.reports.get(self.r["id"], "analyst")["audit"]]
        self.assertEqual(actions, ["create", "edit", "submit", "export.pdf"])

    def test_persistence_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "reports.json"
            a = ReportStore(path)
            created = a.create(self.built, "awa", "analyst")
            b = ReportStore(path)
            self.assertEqual(b.get(created["id"], "analyst")["title"], created["title"])
            if os.name == "posix":
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            path.write_text("{pas du json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                ReportStore(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{pas du json")  # rien d'écrasé

    def test_check_report_warns_on_social_only_and_demo(self):
        built = build_report(self.store, params(), NOW, demo=True)
        warnings = [i["message"] for i in check_report(built) if i["severity"] == "warning"]
        self.assertTrue(any("réseaux sociaux" in w for w in warnings))
        self.assertTrue(any("fictives" in w.lower() for w in warnings))


if __name__ == "__main__":
    unittest.main()


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.report = build_report(self.store, params(), NOW)
        self.report.update(id="x", status="draft", version=1, validated_by=None, validated_at=None)

    def hostile(self):
        r = copy.deepcopy(self.report)
        r["title"] = "<script>alert(1)</script> & Titre"
        r["sources"][0]["url"] = "javascript:alert(1)"
        for s in r["sections"]:
            if s["key"] == "sources":
                s["blocks"][0]["rows"][0][5] = "javascript:alert(1)"
                s["blocks"][0]["rows"][1][4] = "Titre \x07 avec contrôle, arabe مرحبا et ɛɔŋ"
        return r

    def test_html_escapes_and_refuses_unsafe_links(self):
        from nyansa.deliverables.render import render_html

        out = render_html(self.hostile())
        self.assertNotIn("<script>alert", out)
        self.assertNotIn('href="javascript', out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('href="https://m2.test/1"', out)  # lien légitime conservé
        self.assertIn("javascript:alert(1)</td>", out)  # lien dangereux affiché en texte brut, jamais cliquable
        self.assertIn('href="#src-1"', out)

    def test_docx_is_valid_zip_of_wellformed_xml(self):
        import io
        import xml.etree.ElementTree as ET
        import zipfile

        from nyansa.deliverables.render import render_docx

        data = render_docx(self.hostile())
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            names = z.namelist()
            for name in names:
                ET.fromstring(z.read(name))  # lève si le XML est mal formé (ex. caractère de contrôle)
            self.assertIn("word/document.xml", names)
            self.assertNotIn(b"javascript:", z.read("word/_rels/document.xml.rels"))
            self.assertIn("BROUILLON".encode(), z.read("word/header1.xml"))

    def test_docx_opens_with_python_docx_when_available(self):
        try:
            import docx
        except ImportError:
            self.skipTest("python-docx absent")
        import io

        from nyansa.deliverables.render import render_docx

        document = docx.Document(io.BytesIO(render_docx(self.report)))
        self.assertTrue(any("Synthèse exécutive" in p.text for p in document.paragraphs))
        self.assertTrue(document.tables)

    def test_executive_brief_cover_and_section_guidance_are_consistent_across_exports(self):
        from nyansa.deliverables.builder import build_report
        from nyansa.deliverables.render import render_docx, render_html
        import io
        import zipfile

        report = build_report(self.store, params(template_id="executive_brief"), NOW, demo=True)
        report.update(id="exec", status="draft", version=2, validated_by=None, validated_at=None)
        html = render_html(report)
        self.assertIn("NYANSA · Renseignement Afrique · Sources ouvertes", html)
        self.assertIn("Méthodologie et limites", html)
        self.assertIn("Limites et biais de couverture", html)
        docx = render_docx(report)
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("RENSEIGNEMENT AFRIQUE", document_xml)
        self.assertIn("Méthodologie et limites", document_xml)
        try:
            from nyansa.deliverables.pdf import render_pdf
            import pypdf
        except ImportError:
            self.skipTest("reportlab ou pypdf absent")
        pdf_text = "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(render_pdf(report))).pages)
        self.assertIn("RENSEIGNEMENT AFRIQUE", pdf_text)
        self.assertIn("Méthodologie et limites", pdf_text)

    def test_watermark_rules(self):
        from nyansa.deliverables.render import watermark

        r = copy.deepcopy(self.report)
        self.assertIn("BROUILLON", watermark(r))
        r["status"] = "validated"
        self.assertIsNone(watermark(r))
        r["meta"]["demo"] = True
        self.assertIn("FICTIVES", watermark(r))  # un document validé mais fictif garde son filigrane

    def test_pdf_is_a4_with_pages_watermark_and_links(self):
        try:
            import pypdf
            from nyansa.deliverables.pdf import render_pdf
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab ou pypdf absent")
        import io

        data = render_pdf(self.hostile())
        self.assertTrue(data.startswith(b"%PDF"))
        reader = pypdf.PdfReader(io.BytesIO(data))
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertEqual([round(float(x)) for x in reader.pages[0].mediabox[2:]], [595, 842])
        text = "\n".join(p.extract_text() for p in reader.pages)
        self.assertIn("Page 2 /", text)
        self.assertIn("BROUILLON", text)
        uris = {a.get_object()["/A"]["/URI"] for p in reader.pages for a in (p.get("/Annots") or []) if "/URI" in a.get_object().get("/A", {})}
        self.assertIn("https://m2.test/1", uris)
        self.assertFalse([u for u in uris if u.startswith("javascript")])

    def test_pdf_validated_real_document_has_no_watermark(self):
        try:
            import pypdf
            from nyansa.deliverables.pdf import render_pdf
        except ImportError:
            self.skipTest("reportlab ou pypdf absent")
        import io

        r = copy.deepcopy(self.report)
        r["status"] = "validated"
        text = "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(render_pdf(r))).pages)
        self.assertNotIn("BROUILLON", text)
        self.assertNotIn("FICTIVES", text)

    def test_pdf_unavailable_message(self):
        import sys
        from unittest import mock

        from nyansa.deliverables.pdf import PdfUnavailable, render_pdf

        with mock.patch.dict(sys.modules, {"reportlab": None, "reportlab.lib": None}):
            with self.assertRaises(PdfUnavailable):
                render_pdf(self.report)


class DivergentFiguresTest(unittest.TestCase):
    def test_fact_line_flags_diverging_casualty_figures(self):
        s = InMemoryStore()
        s.add_source(Source("a", "Média A", "rss", url="https://a.test/f", default_reliability="B"))
        s.add_source(Source("b", "Média B", "rss", url="https://b.test/f", default_reliability="B"))
        pages = {
            "https://a.test/f": feed(("More than 40 civilians killed in Mali attack blamed on Islamic State", "https://a.test/1", pub(1))),
            "https://b.test/f": feed(("Mali: at least 48 civilians killed in Islamic State attack", "https://b.test/1", pub(1))),
        }
        refresh(s, lambda url: pages[url], NOW)
        r = build_report(s, params(template_id="flash_alert", countries=["MLI"]), NOW)
        facts = [b["text"] for sec in r["sections"] for b in sec["blocks"] if b["kind"] == "fact"]
        self.assertEqual(len(facts), 1, facts)
        self.assertIn("Bilans divergents selon les sources : 40 ; 48", facts[0])
