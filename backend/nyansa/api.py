
"""API HTTP FastAPI de NYANSA."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from . import services
from .countries import COUNTRIES_BY_ISO, RATED_COUNTRIES
from .demo import load_demo
from .models import Source
from .store import InMemoryStore
from .taxonomy import REGION_LABELS_FR, Domain, Region

READ_ROLES = ("analyst", "validator", "manager", "executive", "admin")
WRITE_ROLES = ("analyst", "admin")
ADMIN_ROLES = ("admin",)
ALL_ROLES = (*READ_ROLES, "partner")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"


class ManualIngest(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    text: str = Field(default="", max_length=50_000)
    source_id: str = Field(default="manual", min_length=1, max_length=100)
    url: HttpUrl | None = None
    published_at: datetime | None = None


def _project_path(value: str | None, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _demo_store() -> InMemoryStore:
    store = InMemoryStore()
    load_demo(store)
    return store


def create_app(store: InMemoryStore | None = None) -> FastAPI:
    # Magasin provisoire : conserve lacompatibilité des tests sans lifespan.
    data = store or InMemoryStore()

    if data.get_source("manual") is None:
        data.add_source(
            Source("manual", "Saisie manuelle", "manual", default_reliability="C")
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal data
        runtime = None

        if store is None:
            mode = os.getenv("NYANSA_MODE", "demo").strip().lower()
            if mode not in {"demo", "live"}:
                raise RuntimeError("NYANSA_MODE doit valoir 'demo' ou 'live'.")

            live_mode = mode == "live"

            from .runtime import create_runtime

            runtime = create_runtime(
                live_sources_path=(
                    _project_path(
                        os.getenv("NYANSA_SOURCES"),
                        "config/sources.json",
                    )
                    if live_mode
                    else None
                ),
                data_dir=_project_path(
                    os.getenv("NYANSA_DATA_DIR"),
                    "data",
                ),
                alert_rules_path=_project_path(
                    os.getenv("NYANSA_ALERT_RULES"),
                    "config/alert_rules.json",
                ),
                watches_path=_project_path(
                    os.getenv("NYANSA_WATCHES"),
                    "config/watches.json",
                ),
                demo_store_factory=_demo_store,
            )

            data = runtime.store
            app.state.runtime = runtime

            if runtime.live_mode:
                from .devserver import LiveState, refresh_loop

                interval = float(os.getenv("NYANSA_COLLECT_INTERVAL", "900"))
                if interval <= 0:
                    raise RuntimeError(
                        "NYANSA_COLLECT_INTERVAL doit être supérieur à zéro."
                    )

                if runtime.archive is None:
                    raise RuntimeError("Archive absente en mode réel.")

                runtime.start_collector(
                    target=refresh_loop,
                    args=(
                        runtime.store,
                        LiveState(),
                        runtime.lock,
                        interval,
                        runtime.stop_event,
                        runtime.alerts,
                        runtime.wake_event,
                        runtime.archive,
                    ),
                )

        app.state.store = data

        try:
            yield
        finally:
            if runtime is not None:
                runtime.stop_collector()

    app = FastAPI(
        title="Nyansa API",
        version="0.1.0",
        lifespan=lifespan,
    )

    def current_role(x_dev_role: Annotated[str | None, Header()] = None) -> str:
        if os.getenv("NYANSA_AUTH_MODE", "oidc") != "dev":
            raise HTTPException(
                status_code=501,
                detail="Authentification OIDC non configurée",
            )
        if x_dev_role not in ALL_ROLES:
            raise HTTPException(
                status_code=401,
                detail="En-tête X-Dev-Role absent ou invalide",
            )
        return x_dev_role

    def require(*roles: str):
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
    def countries(
        region: Region | None = None,
        role: str = reader,
    ) -> list[dict]:
        return [
            {
                "iso3": c.iso3,
                "iso2": c.iso2,
                "name_fr": c.name_fr,
                "name_en": c.name_en,
                "region": c.region.value,
                "region_label": REGION_LABELS_FR[c.region],
                "rated": c.rated,
            }
            for c in COUNTRIES_BY_ISO.values()
            if region is None or c.region == region
        ]

    @app.get("/events")
    def events(
        country: str | None = None,
        region: Region | None = None,
        domain: Domain | None = None,
        min_criticality: Annotated[int | None, Query(ge=1, le=25)] = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        role: str = reader,
    ) -> list[dict]:
        code = country_or_404(country) if country else None
        found = data.events(
            country=code,
            region=region,
            domain=domain,
            min_criticality=min_criticality,
            since=since,
            limit=limit,
        )
        return [services.event_to_dict(e, data) for e in found]

    @app.post("/ingest/manual", status_code=201)
    def ingest_manual(body: ManualIngest, role: str = writer) -> dict:
        if data.get_source(body.source_id) is None:
            raise HTTPException(
                status_code=422,
                detail=f"Source inconnue : {body.source_id}",
            )

        outcome = services.ingest_manual(
            data,
            title=body.title,
            text=body.text,
            source_id=body.source_id,
            url=str(body.url) if body.url else None,
            published_at=body.published_at,
        )
        data.audit(role, "ingest.manual", outcome.document_id)

        return {
            "status": outcome.status,
            "document_id": outcome.document_id,
            "event_id": outcome.event_id,
            "event_created": outcome.created,
            "reason": outcome.reason,
        }

    @app.get("/risk/countries")
    def risk_countries(role: str = reader) -> list[dict]:
        return [services.risk_to_dict(r) for r in services.risk_map(data)]

    @app.get("/risk/countries/{iso3}")
    def risk_country(iso3: str, role: str = reader) -> dict:
        code = country_or_404(iso3)
        if not COUNTRIES_BY_ISO[code].rated:
            raise HTTPException(
                status_code=422,
                detail=f"{code} n'est pas évalué (statut non réglé)",
            )
        return services.risk_to_dict(
            services.risk_for_country(data, code)
        )

    @app.get("/coverage")
    def coverage(
        window_days: Annotated[int, Query(ge=1, le=365)] = 30,
        role: str = reader,
    ) -> dict:
        return services.coverage_report(data, window_days=window_days)

    @app.get("/audit")
    def audit(role: str = admin) -> list[dict]:
        return [
            {
                "at": a.at,
                "actor": a.actor,
                "action": a.action,
                "object_id": a.object_id,
            }
            for a in data.audit_log()
        ]

    # Les routes API sont déclarées avant le montage statique.
    if WEB_ROOT.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=WEB_ROOT, html=True),
            name="web",
        )

    return app


app = create_app()

__all__ = ["app", "create_app", "RATED_COUNTRIES"]
