import http.client
import io
import json
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from unittest import mock

from nyansa.deliverables.pdf import PdfUnavailable
from nyansa.devserver import build_store, make_handler

TEMPLATE = "daily_brief"
NEW = {"template_id": TEMPLATE, "analyst": "Awa", "organization": "Cellule veille", "language": "fr"}


class DeliverablesHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(build_store()))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, payload=None, user="alice", role="analyst", headers=None):
        h = {"X-Nyansa": "1", "Content-Type": "application/json", "X-Dev-User": user, "X-Dev-Role": role, **(headers or {})}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request(method, path, body=json.dumps(payload).encode() if payload is not None else None, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        parsed = json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data
        return r.status, parsed, dict(r.getheaders())

    def create(self, **over):
        status, rec, _ = self.call("POST", "/api/deliverables", {**NEW, **over})
        self.assertEqual(status, 201, rec)
        return rec

    def fill_analyst_sections(self, rec, user="alice"):
        sections = rec["sections"]
        for s in sections:
            if s["mode"] == "analyst":
                s["blocks"] = [{"id": "new", "kind": "estimate", "text": "Estimation de l'analyste : situation à surveiller.", "refs": []}]
        return self.call("PUT", f"/api/deliverables/{rec['id']}", {"expected_version": rec["version"], "sections": sections, "note": "rédaction"}, user=user)

    # --- protections -------------------------------------------------------------------------------
    def test_write_requires_custom_header_and_json(self):
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, headers={"X-Nyansa": "0"})[0], 403)
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, headers={"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/deliverables/x", {"expected_version": 1}, headers={"X-Nyansa": "0"})[0], 403)

    def test_cross_site_and_rebinding_are_refused(self):
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/deliverables", headers={"Host": "evil.example"})[0], 403)
        same = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, headers=same)[0], 201)

    def test_user_name_is_percent_decoded(self):
        from urllib.parse import quote

        rec = self.create_as(quote("Zoé Ndiaye"))
        self.assertEqual(rec["created_by"], "Zoé Ndiaye")

    def create_as(self, user):
        status, rec, _ = self.call("POST", "/api/deliverables", NEW, user=user)
        self.assertEqual(status, 201, rec)
        return rec

    def test_invalid_http_body_lengths_are_rejected(self):
        def raw_request(extra_headers):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
            conn.putrequest("POST", "/api/deliverables")
            headers = {
                "X-Nyansa": "1",
                "Content-Type": "application/json",
                **extra_headers,
            }
            for key, value in headers.items():
                conn.putheader(key, value)
            conn.endheaders()
            response = conn.getresponse()
            status = response.status
            response.read()
            conn.close()
            return status

        self.assertEqual(raw_request({}), 411)
        self.assertEqual(raw_request({"Content-Length": "-1"}), 400)
        self.assertEqual(raw_request({"Content-Length": "0",
                                     "Transfer-Encoding": "chunked"}), 400)

    def test_unknown_role_and_oversized_body(self):
        self.assertEqual(self.call("GET", "/api/deliverables", role="root")[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)  # longueur déclarée excessive : refus avant lecture
        conn.putrequest("POST", "/api/deliverables")
        for k, v in {"X-Nyansa": "1", "Content-Type": "application/json", "Content-Length": "2000000"}.items():
            conn.putheader(k, v)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()

    # --- catalogue et création ---------------------------------------------------------------------
    def test_catalog_and_capabilities(self):
        status, cat, _ = self.call("GET", "/api/deliverables/catalog?lang=en")
        self.assertEqual((status, len(cat)), (200, 25))
        self.assertTrue(all(t["sections"] for t in cat))
        status, caps, _ = self.call("GET", "/api/deliverables/capabilities", role="validator", user="bob")
        self.assertEqual((status, caps["role"], caps["actor"], caps["llm"], caps["demo"]), (200, "validator", "bob", False, True))
        self.assertFalse(caps["persistent"])

    def test_create_validates_parameters_and_roles(self):
        self.assertEqual(self.call("POST", "/api/deliverables", {**NEW, "template_id": "inconnu"})[0], 422)
        self.assertEqual(self.call("POST", "/api/deliverables", {**NEW, "language": "ar"})[0], 422)
        self.assertEqual(self.call("POST", "/api/deliverables", {**NEW, "countries": ["XXX"]})[0], 422)
        self.assertEqual(self.call("POST", "/api/deliverables", NEW, role="executive")[0], 403)
        rec = self.create()
        self.assertEqual((rec["status"], rec["version"], rec["created_by"]), ("draft", 1, "alice"))
        self.assertTrue(rec["meta"]["demo"])

    # --- circuit complet : brouillon -> relecture -> validation -> exports ---------------------------
    def test_full_workflow_and_exports(self):
        rec = self.create()
        rid = f"/api/deliverables/{rec['id']}"
        # un brouillon est invisible d'un décideur
        self.assertEqual(self.call("GET", rid, role="executive", user="dg")[0], 403)
        # sections d'analyse vides : la soumission est bloquée avec la liste des points
        status, err, _ = self.call("POST", f"{rid}/submit", {})
        self.assertEqual(status, 422)
        self.assertTrue(any("vide" in i["message"] for i in err["issues"]))
        self.assertEqual(self.call("GET", f"{rid}/check")[1]["issues"][0]["severity"], "error")
        # édition avec contrôle de version
        status, rec2, _ = self.fill_analyst_sections(rec)
        self.assertEqual((status, rec2["version"]), (200, 2))
        status, err, _ = self.call("PUT", rid, {"expected_version": 1, "title": "Autre titre"})
        self.assertEqual(status, 409, err)
        self.assertEqual(self.call("PUT", rid, {"title": "sans version"})[0], 422)
        # un bloc calculé ne peut pas être réécrit
        sections = rec2["sections"]
        sections[0]["blocks"][0]["text"] = "Chiffre truqué"
        self.assertEqual(self.call("PUT", rid, {"expected_version": 2, "sections": sections})[0], 422)
        # soumission, puis validation par une autre personne uniquement
        self.assertEqual(self.call("POST", f"{rid}/submit", {})[1]["status"], "in_review")
        self.assertEqual(self.call("PUT", rid, {"expected_version": 2, "title": "trop tard"})[0], 409)
        self.assertEqual(self.call("POST", f"{rid}/validate", {}, role="validator", user="alice")[0], 403)  # même personne
        self.assertEqual(self.call("POST", f"{rid}/validate", {}, role="analyst", user="bob")[0], 403)  # mauvais rôle
        status, done, _ = self.call("POST", f"{rid}/validate", {"comment": "ok"}, role="validator", user="bob")
        self.assertEqual((status, done["status"], done["validated_by"]), (200, "validated", "bob"))
        # le décideur voit maintenant le document validé
        self.assertEqual(self.call("GET", rid, role="executive", user="dg")[0], 200)
        # versions et comparaison
        versions = self.call("GET", f"{rid}/versions")[1]
        self.assertEqual([v["version"] for v in versions], [1, 2])
        changes = self.call("GET", f"{rid}/diff?a=1&b=2")[1]
        self.assertTrue(any(c["change"] == "modified" for c in changes))
        self.assertEqual(self.call("GET", f"{rid}/diff?a=1")[0], 422)
        # exports : DOCX, HTML (CSP, sans script), PDF réel si reportlab est présent
        status, body, h = self.call("GET", f"{rid}/export.docx", role="executive", user="dg")
        self.assertEqual(status, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertIn("word/document.xml", zipfile.ZipFile(io.BytesIO(body)).namelist())
        status, body, h = self.call("GET", f"{rid}/export.html")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertNotIn(b"<script", body.lower())
        self.assertEqual(h["Cache-Control"], "no-store")
        try:
            import reportlab  # noqa: F401
        except ImportError:
            return
        status, body, h = self.call("GET", f"{rid}/export.pdf")
        self.assertEqual((status, body[:5], h["Content-Type"]), (200, b"%PDF-", "application/pdf"))
        self.assertEqual(self.call("GET", f"{rid}/export.zip")[0], 404)
        audit = [a["action"] for a in self.call("GET", rid)[1]["audit"]]
        self.assertTrue({"create", "edit", "submit", "validate", "export.pdf", "export.docx"} <= set(audit))

    def test_pdf_unavailable_returns_501_with_fallback(self):
        rec = self.create()
        with mock.patch("nyansa.devserver.render_pdf", side_effect=PdfUnavailable("Export PDF indisponible : installez reportlab")):
            status, data, _ = self.call("GET", f"/api/deliverables/{rec['id']}/export.pdf")
        self.assertEqual(status, 501)
        self.assertIn("Imprimer", data["detail"])

    def test_reject_requires_reason_then_duplicate_and_archive(self):
        rec = self.create()
        rid = f"/api/deliverables/{rec['id']}"
        self.fill_analyst_sections(rec)
        self.call("POST", f"{rid}/submit", {})
        self.assertEqual(self.call("POST", f"{rid}/reject", {}, role="manager", user="chef")[0], 422)
        status, back, _ = self.call("POST", f"{rid}/reject", {"comment": "Source à compléter"}, role="manager", user="chef")
        self.assertEqual((status, back["status"]), (200, "draft"))
        status, copy_, _ = self.call("POST", f"{rid}/duplicate", {})
        self.assertEqual((status, copy_["status"], copy_["title"].endswith("(copie)")), (201, "draft", True))
        self.assertEqual(self.call("POST", f"{rid}/archive", {})[1]["status"], "archived")
        self.assertEqual(self.call("POST", f"{rid}/inconnue", {})[0], 422)
        listed = self.call("GET", "/api/deliverables?status=draft&q=copie")[1]
        self.assertTrue(listed and all(r["status"] == "draft" for r in listed))
        self.assertEqual(self.call("GET", "/api/deliverables/introuvable")[0], 404)


if __name__ == "__main__":
    unittest.main()
