"""API HTTP (FastAPI). Lancer : uvicorn nyansa.api:app --reload (depuis backend/).

Authentification : l'intégration OIDC + MFA n'est pas encore branchée. Par défaut l'API
refuse toute requête protégée (501). Pour le développement local uniquement :
NYANSA_AUTH_MODE=dev, puis l'en-tête X-Dev-Role (analyst, validator, manager, executive, admin).
Ne jamais activer ce mode hors poste de développement.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from . import services
from .countries import COUNTRIES_BY_ISO, RATED_COUNTRIES
from .ingest import load_sources
from .models import Source
from .store import InMemoryStore
from .taxonomy import REGION_LABELS_FR, Domain, Region

READ_ROLES = ("analyst", "validator", "manager", "executive", "admin")
WRITE_ROLES = ("analyst", "admin")
ADMIN_ROLES = ("admin",)
ALL_ROLES = (*READ_ROLES, "partner")  # « partner » : aucun accès aux données brutes avant la phase 4


class ManualIngest(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    text: str = Field(default="", max_length=50_000)
    source_id: str = Field(default="manual", min_length=1, max_length=100)
    url: HttpUrl | None = None
    published_at: datetime | None = None


def create_app(store: InMemoryStore | None = None) -> FastAPI:
    app = FastAPI(title="Nyansa API", version="0.1.0")
    data = store or InMemoryStore()

    if data.get_source("manual") is None:
        data.add_source(Source("manual", "Saisie manuelle", "manual", default_reliability="C"))
    sources_path = os.getenv("NYANSA_SOURCES")
    if sources_path:
        for source in load_sources(sources_path):
            data.add_source(source)

    def current_role(x_dev_role: Annotated[str | None, Header()] = None) -> str:
        if os.getenv("NYANSA_AUTH_MODE", "oidc") != "dev":
            raise HTTPException(status_code=501, detail="Authentification OIDC non configurée")
        if x_dev_role not in ALL_ROLES:
            raise HTTPException(status_code=401, detail="En-tête X-Dev-Role absent ou invalide")
        return x_dev_role

    def require(*roles: str):
        # Depends en valeur par défaut (et non dans Annotated) : avec « from __future__ import
        # annotations », une annotation référençant une variable locale ne peut pas être résolue.
        def dependency(role: str = Depends(current_role)) -> str:
            if role not in roles:
                raise HTTPException(status_code=403, detail="Rôle insuffisant")
            return role

        return dependency

    reader = Depends(require(*READ_ROLES))
    writer = Depends(require(*WRITE_ROLES))
    admin = Depends(require(*ADMIN_ROLES))

    def country_or_404(iso3: str) -> str:
        code = iso3.upper()
        if code not in COUNTRIES_BY_ISO:
            raise HTTPException(status_code=404, detail=f"Pays inconnu : {iso3}")
        return code

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/countries")
    def countries(region: Region | None = None, role: str = reader) -> list[dict]:
        return [
            {
                "iso3": c.iso3, "iso2": c.iso2, "name_fr": c.name_fr, "name_en": c.name_en,
                "region": c.region.value, "region_label": REGION_LABELS_FR[c.region], "rated": c.rated,
            }
            for c in COUNTRIES_BY_ISO.values()
            if region is None or c.region == region
        ]

    @app.get("/events")
    def events(
        role: str = reader,
        country: str | None = None,
        region: Region | None = None,
        domain: Domain | None = None,
        min_criticality: Annotated[int | None, Query(ge=1, le=25)] = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict]:
        code = country_or_404(country) if country else None
        found = data.events(
            country=code, region=region, domain=domain, min_criticality=min_criticality,
            since=since, limit=limit,
        )
        return [services.event_to_dict(e, data) for e in found]

    @app.post("/ingest/manual", status_code=201)
    def ingest_manual(body: ManualIngest, role: str = writer) -> dict:
        if data.get_source(body.source_id) is None:
            raise HTTPException(status_code=422, detail=f"Source inconnue : {body.source_id}")
        outcome = services.ingest_manual(
            data, title=body.title, text=body.text, source_id=body.source_id,
            url=str(body.url) if body.url else None, published_at=body.published_at,
        )
        data.audit(role, "ingest.manual", outcome.document_id)
        return {
            "status": outcome.status, "document_id": outcome.document_id,
            "event_id": outcome.event_id, "event_created": outcome.created, "reason": outcome.reason,
        }

    @app.get("/risk/countries")
    def risk_countries(role: str = reader) -> list[dict]:
        return [services.risk_to_dict(r) for r in services.risk_map(data)]

    @app.get("/risk/countries/{iso3}")
    def risk_country(iso3: str, role: str = reader) -> dict:
        code = country_or_404(iso3)
        if not COUNTRIES_BY_ISO[code].rated:
            raise HTTPException(status_code=422, detail=f"{code} n'est pas évalué (statut non réglé)")
        return services.risk_to_dict(services.risk_for_country(data, code))

    @app.get("/coverage")
    def coverage(role: str = reader, window_days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
        return services.coverage_report(data, window_days=window_days)

    @app.get("/audit")
    def audit(role: str = admin) -> list[dict]:
        return [
            {"at": a.at, "actor": a.actor, "action": a.action, "object_id": a.object_id}
            for a in data.audit_log()
        ]

    return app


app = create_app()

__all__ = ["app", "create_app", "RATED_COUNTRIES"]
