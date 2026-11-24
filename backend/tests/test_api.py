"""Tests de l'API HTTP. Ignorés si FastAPI n'est pas installé (pip install -e .[dev])."""

from __future__ import annotations

import importlib.util
import os
import unittest

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx2") is not None


@unittest.skipUnless(HAS_FASTAPI, "fastapi et httpx2 requis")
class ApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from nyansa.api import create_app

        self._previous = os.environ.get("NYANSA_AUTH_MODE")
        os.environ["NYANSA_AUTH_MODE"] = "dev"
        self.client = TestClient(create_app())
        self.analyst = {"X-Dev-Role": "analyst"}

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("NYANSA_AUTH_MODE", None)
        else:
            os.environ["NYANSA_AUTH_MODE"] = self._previous

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_protected_routes_fail_closed(self):
        self.assertEqual(self.client.get("/events").status_code, 401)
        self.assertEqual(self.client.get("/events", headers={"X-Dev-Role": "hacker"}).status_code, 401)
        self.assertEqual(self.client.get("/events", headers={"X-Dev-Role": "partner"}).status_code, 403)
        os.environ["NYANSA_AUTH_MODE"] = "oidc"
        self.assertEqual(self.client.get("/events", headers=self.analyst).status_code, 501)

    def test_ingest_then_read_back_with_risk_and_audit(self):
        body = {"title": "Attaque armée contre un convoi près de Ouagadougou", "text": "Burkina Faso."}
        created = self.client.post("/ingest/manual", json=body, headers=self.analyst)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "routed")

        events = self.client.get("/events", params={"country": "bfa", "domain": "SEC"}, headers=self.analyst).json()
        self.assertEqual([e["country"] for e in events], ["BFA"])

        risk = self.client.get("/risk/countries/BFA", headers=self.analyst).json()
        self.assertGreater(risk["score"], 0)
        self.assertTrue(risk["contributions"])
        ranking = self.client.get("/risk/countries", headers=self.analyst).json()
        self.assertEqual((len(ranking), ranking[0]["iso3"]), (54, "BFA"))

        self.assertEqual(self.client.get("/audit", headers=self.analyst).status_code, 403)
        log = self.client.get("/audit", headers={"X-Dev-Role": "admin"}).json()
        self.assertEqual(log[0]["action"], "ingest.manual")

    def test_public_mode_allows_read_and_manual_ingest_but_not_audit(self):
        os.environ["NYANSA_AUTH_MODE"] = "public"

        self.assertEqual(self.client.get("/events").status_code, 200)
        self.assertEqual(self.client.get("/countries").status_code, 200)

        body = {
            "title": "Incident de securite signale au Burkina Faso",
            "text": "Burkina Faso."
        }
        created = self.client.post("/ingest/manual", json=body)
        self.assertEqual(created.status_code, 201, created.text)

        self.assertEqual(self.client.get("/audit").status_code, 403)
        self.assertEqual(
            self.client.get("/audit", headers={"X-Dev-Role": "admin"}).status_code,
            403
        )

    def test_validation_and_errors(self):
        self.assertEqual(self.client.post("/ingest/manual", json={"title": "x"}, headers=self.analyst).status_code, 422)
        unknown = {"title": "Attentat à Bamako", "source_id": "inconnue"}
        self.assertEqual(self.client.post("/ingest/manual", json=unknown, headers=self.analyst).status_code, 422)
        self.assertEqual(self.client.get("/events", params={"country": "XXX"}, headers=self.analyst).status_code, 404)
        self.assertEqual(self.client.get("/risk/countries/ESH", headers=self.analyst).status_code, 422)
        self.assertEqual(self.client.get("/events", params={"domain": "???"}, headers=self.analyst).status_code, 422)

    def test_countries_filter_by_region(self):
        rows = self.client.get("/countries", params={"region": "NORTH"}, headers=self.analyst).json()
        self.assertEqual({r["iso3"] for r in rows}, {"MAR", "DZA", "TUN", "LBY", "EGY", "MRT", "ESH"})


if __name__ == "__main__":
    unittest.main()
