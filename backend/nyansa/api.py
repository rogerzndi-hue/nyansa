
"""API HTTP FastAPI de NYANSA."""

from __future__ import annotations
from fastapi.responses import JSONResponse
import importlib.util
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from . import services
from .alerts import AlertCenter
from .countries import COUNTRIES_BY_ISO, RATED_COUNTRIES
from .demo import load_demo
from .models import Source
from .social import STATUS_LABELS_FR, WATCH_ROLES, WatchBook, WatchError, social_view, sync_sources
from .store import InMemoryStore
from .store_protocol import StoreProtocol
from .taxonomy import (
    DOMAIN_LABELS_FR,
    REGION_LABELS_FR,
    Domain,
    Region,
)

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


def _add_manual_source(store: StoreProtocol) -> None:
    if store.get_source("manual") is None:
        store.add_source(
            Source("manual", "Saisie manuelle", "manual", default_reliability="C")
        )

def _report_error(exc) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "detail": str(exc),
            "issues": getattr(exc, "issues", []),
        },
    )

def create_app(store: StoreProtocol | None = None) -> FastAPI:
    # Magasin de secours : les tests sans lifespan peuvent utiliser l'API.
    data = store or InMemoryStore()
    _add_manual_source(data)

    from .alerts import AlertCenter
    from .deliverables.report import ReportStore

    fallback_reports = ReportStore()
    fallback_alerts = AlertCenter(external=False)
    fallback_watches = WatchBook()
    fallback_lock = threading.RLock()

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
            _add_manual_source(data)

            app.state.runtime = runtime
            app.state.store = data
            app.state.reports = runtime.reports
            app.state.alerts = runtime.alerts
            app.state.watches = runtime.watches
            app.state.lock = runtime.lock
            app.state.demo = not runtime.live_mode
            app.state.live = None

            if runtime.live_mode:
                from .devserver import LiveState, refresh_loop

                interval = float(os.getenv("NYANSA_COLLECT_INTERVAL", "900"))
                if interval <= 0:
                    raise RuntimeError(
                        "NYANSA_COLLECT_INTERVAL doit être supérieur à zéro."
                    )

                if runtime.archive is None:
                    raise RuntimeError("Archive absente en mode réel.")

                live_state = LiveState()
                app.state.live = live_state

                runtime.start_collector(
                    target=refresh_loop,
                    args=(
                        runtime.store,
                        live_state,
                        runtime.lock,
                        interval,
                        runtime.stop_event,
                        runtime.alerts,
                        runtime.wake_event,
                        runtime.archive,
                    ),
                )

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

    # Services accessibles même lorsque le lifespan n'est pas lancé.
    app.state.store = data
    app.state.reports = fallback_reports
    app.state.alerts = fallback_alerts
    app.state.watches = fallback_watches
    app.state.lock = fallback_lock
    app.state.demo = True
    app.state.live = None
    app.state.runtime = None

    def current_role(
        x_dev_role: Annotated[str | None, Header()] = None,
    ) -> str:
        auth_mode = os.getenv("NYANSA_AUTH_MODE", "oidc").strip().lower()

        # En public, aucun header client ne peut élever les permissions.
        if auth_mode == "public":
            return "analyst"

        if auth_mode != "dev":
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

    def current_actor(
        request: Request,
        x_dev_user: Annotated[str | None, Header()] = None,
    ) -> str:
        # En mode public, ne pas faire passer un header libre pour une
        # identité vérifiée dans les journaux.
        if os.getenv("NYANSA_AUTH_MODE", "oidc").strip().lower() == "public":
            return "public"

        actor = unquote(x_dev_user or "").strip()
        return actor[:120] if actor else "analyste"

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

    def components(request: Request):
        state = request.app.state
        return (
            state.store,
            state.reports,
            state.alerts,
            state.watches,
            state.lock,
            state.live,
            state.demo,
        )

    # ------------------------------------------------------------------
    # Routes historiques
    # ------------------------------------------------------------------

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
    request: Request,
    country: str | None = None,
    region: Region | None = None,
    domain: Domain | None = None,
    min_criticality: Annotated[int | None, Query(ge=1, le=25)] = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    role: str = reader,
) -> list[dict]:
        active_store = request.app.state.store
        code = country_or_404(country) if country else None
        found = active_store.events(
            country=code,
            region=region,
            domain=domain,
            min_criticality=min_criticality,
            since=since,
            limit=limit,
        )
        return [services.event_to_dict(e, active_store) for e in found]

    @app.post("/ingest/manual", status_code=201)
    def ingest_manual(
        body: ManualIngest,
        request: Request,
        role: str = writer,
        actor: str = Depends(current_actor),
    ) -> dict:
        active_store = request.app.state.store

        if active_store.get_source(body.source_id) is None:
            raise HTTPException(
                status_code=422,
                detail=f"Source inconnue : {body.source_id}",
            )

        outcome = services.ingest_manual(
            active_store,
            title=body.title,
            text=body.text,
            source_id=body.source_id,
            url=str(body.url) if body.url else None,
            published_at=body.published_at,
        )
        active_store.audit(actor, "ingest.manual", outcome.document_id)

        return {
            "status": outcome.status,
            "document_id": outcome.document_id,
            "event_id": outcome.event_id,
            "event_created": outcome.created,
            "reason": outcome.reason,
        }

    @app.get("/risk/countries")
    def risk_countries(
        request: Request,
        role: str = reader,
    ) -> list[dict]:
        return [
            services.risk_to_dict(r)
            for r in services.risk_map(request.app.state.store)
        ]

    @app.get("/risk/countries/{iso3}")
    def risk_country(
        iso3: str,
        request: Request,
        role: str = reader,
    ) -> dict:
        code = country_or_404(iso3)
        if not COUNTRIES_BY_ISO[code].rated:
            raise HTTPException(
                status_code=422,
                detail=f"{code} n'est pas évalué (statut non réglé)",
            )
        return services.risk_to_dict(
            services.risk_for_country(request.app.state.store, code)
        )

    @app.get("/coverage")
    def coverage(
        request: Request,
        window_days: Annotated[int, Query(ge=1, le=365)] = 30,
        role: str = reader,
    ) -> dict:
        return services.coverage_report(
            request.app.state.store,
            window_days=window_days,
        )

    @app.get("/audit")
    def audit(
        request: Request,
        role: str = admin,
    ) -> list[dict]:
        return [
            {
                "at": a.at,
                "actor": a.actor,
                "action": a.action,
                "object_id": a.object_id,
            }
            for a in request.app.state.store.audit_log()
        ]

    # ------------------------------------------------------------------
    # API utilisée par le frontend
    # ------------------------------------------------------------------

    @app.get("/api/health")
    def api_health(request: Request, role: str = reader) -> dict:
        return {"status": "ok", "demo": request.app.state.demo}

    @app.get("/api/meta")
    def api_meta(request: Request, role: str = reader) -> dict:
        demo = request.app.state.demo
        return {
            "demo": demo,
            "mode": "demo" if demo else "live",
            "domains": {d.value: DOMAIN_LABELS_FR[d] for d in Domain},
            "regions": {r.value: REGION_LABELS_FR[r] for r in Region},
        }

    @app.get("/api/risk/countries")
    def api_risk_countries(
        request: Request,
        role: str = reader,
    ) -> list[dict]:
        return [
            services.risk_to_dict(r)
            for r in services.risk_map(request.app.state.store)
        ]

    @app.get("/api/events")
    def api_events(
        request: Request,
        country: str | None = None,
        region: Region | None = None,
        domain: Domain | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        role: str = reader,
    ) -> list[dict]:
        active_store = request.app.state.store
        code = country_or_404(country) if country else None
        found = active_store.events(
            country=code,
            region=region,
            domain=domain,
            limit=limit,
        )
        return [services.event_to_dict(e, active_store) for e in found]

    @app.get("/api/sources")
    def api_sources(request: Request, role: str = reader):
        active_store = request.app.state.store
        live = request.app.state.live

        rows = [
            {
                "id": s.id,
                "name": s.name,
                "kind": s.kind,
                "channel": s.channel,
                "active": s.active,
                "reliability": s.default_reliability,
            }
            for s in active_store.sources()
        ]

        return rows if live is None else {
            "sources": rows,
            "reports": live.snapshot(),
        }

    @app.get("/api/unrouted")
    def api_unrouted(request: Request, role: str = reader) -> list[dict]:
        active_store = request.app.state.store
        docs = []
        per_source = {}

        for doc in reversed(active_store.unrouted()):
            if per_source.get(doc.source_id, 0) < 10 and len(docs) < 200:
                per_source[doc.source_id] = per_source.get(doc.source_id, 0) + 1
                docs.append(doc)

        docs.sort(key=lambda doc: doc.source_id)

        return [
            {
                "source_id": doc.source_id,
                "title": doc.title,
                "reason": doc.reject_reason,
                "url": doc.url,
            }
            for doc in docs
        ]

    @app.get("/api/alerts")
    def api_alerts(
        request: Request,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        role: str = reader,
    ) -> dict:
        if status not in (None, "new", "ack"):
            raise HTTPException(status_code=422, detail="status : new ou ack")

        alerts = request.app.state.alerts
        return {
            "alerts": alerts.list(status, limit),
            "external": alerts.external,
        }

    @app.get("/api/alerts/rules")
    def api_alert_rules(request: Request, role: str = reader):
        return request.app.state.alerts.describe_rules()

    @app.post("/api/alerts/{alert_id}/ack")
    def api_ack_alert(
        alert_id: str,
        request: Request,
        role: str = Depends(require(*READ_ROLES)),
        actor: str = Depends(current_actor),
    ):
        from .deliverables.report import ROLE_EDIT, ROLE_VALIDATE, Forbidden

        if role not in ROLE_EDIT | ROLE_VALIDATE:
            raise HTTPException(status_code=403, detail="Rôle insuffisant")

        try:
            return request.app.state.alerts.acknowledge(alert_id, actor)
        except KeyError:
            raise HTTPException(status_code=404, detail="Alerte introuvable")

    @app.get("/api/social")
    def api_social(
        request: Request,
        status: str | None = None,
        watch: str | None = None,
        country: str | None = None,
        limit: Annotated[int, Query(ge=1, le=300)] = 100,
        role: str = reader,
    ):
        if status not in (None, "relevant", *STATUS_LABELS_FR):
            raise HTTPException(
                status_code=422,
                detail=f"status : relevant, {', '.join(STATUS_LABELS_FR)}",
            )

        active_store = request.app.state.store
        live = request.app.state.live

        return social_view(
            active_store,
            request.app.state.watches,
            live.snapshot() if live else None,
            demo=request.app.state.demo,
            watch=watch,
            status=status,
            country=(country or "").upper() or None,
            limit=limit,
        )

    @app.post("/api/social/watches", status_code=201)
    @app.post("/api/social/watches/{watch_id}/toggle")
    @app.post("/api/social/watches/{watch_id}/delete")
    def api_watch_write(
        request: Request,
        payload: dict = Body(...),
        watch_id: str | None = None,
        role: str = Depends(require(*READ_ROLES)),
        actor: str = Depends(current_actor),
    ):
        if role not in WATCH_ROLES:
            raise HTTPException(status_code=403, detail="Rôle insuffisant")

        from .deliverables.report import ReportError

        active_store = request.app.state.store
        watches = request.app.state.watches
        lock = request.app.state.lock

        path = request.url.path.rstrip("/")
        try:
            with lock:
                if path == "/api/social/watches":
                    watch = watches.add(payload, actor)
                    active_store.audit(actor, "watch.add", watch.id)
                    result = watch.to_dict()
                elif path.endswith("/toggle"):
                    if not isinstance(payload.get("active"), bool):
                        raise HTTPException(
                            status_code=422,
                            detail="active (booléen) obligatoire",
                        )
                    result = watches.set_active(
                        watch_id, payload["active"]
                    ).to_dict()
                    active_store.audit(actor, "watch.toggle", watch_id)
                elif path.endswith("/delete"):
                    result = watches.remove(watch_id).to_dict()
                    active_store.audit(actor, "watch.delete", watch_id)
                else:
                    raise HTTPException(status_code=404, detail="Introuvable")

                sync_sources(active_store, watches)

            runtime = request.app.state.runtime
            if runtime is not None:
                runtime.wake_event.set()

            return result

        except WatchError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="Veille introuvable")
        except ReportError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))

    @app.get("/api/coverage")
    def api_coverage(request: Request, role: str = reader):
        return services.coverage_report(request.app.state.store)

    # ------------------------------------------------------------------
    # Livrables : catalogue, rapports, versions, contrôles et exports
    # ------------------------------------------------------------------

    @app.get("/api/deliverables/catalog")
    def deliverables_catalog_route(
        lang: str = "fr",
        role: str = reader,
    ):
        from .deliverables.catalog import catalog

        return catalog(lang)

    @app.get("/api/deliverables/capabilities")
    def deliverables_capabilities(
        request: Request,
        role: str = reader,
        actor: str = Depends(current_actor),
    ):
        reports = request.app.state.reports
        from .deliverables.report import ROLE_EDIT, ROLE_READ_FINAL, ROLE_VALIDATE

        roles = ROLE_EDIT | ROLE_VALIDATE | ROLE_READ_FINAL
        return {
            "pdf": importlib.util.find_spec("reportlab") is not None,
            "docx": True,
            "html": True,
            "llm": False,
            "demo": request.app.state.demo,
            "role": role,
            "actor": actor,
            "roles": sorted(roles),
            "persistent": reports.persistent,
        }

    @app.get("/api/deliverables")
    def deliverables_list(
        request: Request,
        status: str | None = None,
        template: str | None = None,
        q: str | None = None,
        role: str = reader,
    ):
        return request.app.state.reports.list(
            role,
            status=status,
            template_id=template,
            q=q,
        )

    @app.post("/api/deliverables", status_code=201)
    def deliverables_create(
    request: Request,
    payload: dict = Body(...),
    role: str = writer,
    actor: str = Depends(current_actor),
    ):
        from .deliverables.builder import build_report, parse_params

        try:
            params = parse_params(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        with request.app.state.lock:
            built = build_report(
                request.app.state.store,
                params,
                demo=request.app.state.demo,
            )

        from .deliverables.report import ReportError
        try:
            return request.app.state.reports.create(built, actor, role)
        except ReportError as exc:
            return _report_error(exc)

    @app.get("/api/deliverables/{report_id}/versions")
    def deliverables_versions(
        report_id: str,
        request: Request,
        role: str = reader,
    ):
        from .deliverables.report import ReportError
        try:
            return request.app.state.reports.versions(report_id, role)
        except ReportError as exc:
           return _report_error(exc)

    @app.get("/api/deliverables/{report_id}/check")
    def deliverables_check(
        report_id: str,
        request: Request,
        role: str = reader,
    ):
        from .deliverables.report import ReportError, check_report
        try:
            report = request.app.state.reports.get(report_id, role)
            return {"issues": check_report(report)}
        except ReportError as exc:
           return _report_error(exc)

    @app.get("/api/deliverables/{report_id}/diff")
    def deliverables_diff(
        report_id: str,
        request: Request,
        a: int | None = None,
        b: int | None = None,
        role: str = reader,
    ):
        from .deliverables.report import ReportError
        if a is None or b is None:
            raise HTTPException(
                status_code=422,
                detail="Paramètres a et b (versions) obligatoires",
            )
        try:
            return request.app.state.reports.diff(report_id, a, b, role)
        except ReportError as exc:
           return _report_error(exc)

    @app.get("/api/deliverables/{report_id}/export.{fmt}")
    def deliverables_export(
        report_id: str,
        fmt: str,
        request: Request,
        role: str = reader,
        actor: str = Depends(current_actor),
    ):
        from .deliverables.pdf import PdfUnavailable, render_pdf
        from .deliverables.render import render_docx, render_html
        from .deliverables.report import ReportError

        exports = {
            "html": ("text/html; charset=utf-8", "html"),
            "docx": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "docx",
            ),
            "pdf": ("application/pdf", "pdf"),
        }
        if fmt not in exports:
            raise HTTPException(
                status_code=404,
                detail="Format inconnu (html, docx, pdf)",
            )

        try:
            report = request.app.state.reports.record_export(
                report_id, actor, role, fmt
            )
            if fmt == "html":
                body = render_html(report).encode("utf-8")
            elif fmt == "docx":
                body = render_docx(report)
            else:
                body = render_pdf(report)
        except PdfUnavailable as exc:
            raise HTTPException(
                status_code=501,
                detail=(
                    f"{exc}. Repli : ouvrir l'export HTML puis "
                    "Imprimer > Enregistrer en PDF."
                ),
            )
        except ReportError as exc:
           return _report_error(exc)

        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            report["template_id"].lower(),
        ).strip("-")
        name = f"{slug}-v{report['version']}-{report['status']}.{exports[fmt][1]}"

        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{name}"',
        }
        if fmt == "html":
            headers.update({
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "img-src data:; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'"
                ),
                "X-Frame-Options": "DENY",
                "Content-Disposition": f'inline; filename="{name}"',
            })

        return Response(
            content=body,
            media_type=exports[fmt][0],
            headers=headers,
        )

    @app.get("/api/deliverables/{report_id}")
    def deliverables_get(
        report_id: str,
        request: Request,
        role: str = reader,
    ):
        from .deliverables.report import ReportError
        try:
            return Response(
                content=__import__("json").dumps(
                    request.app.state.reports.get(report_id, role),
                    ensure_ascii=False,
                    default=str,
                ),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except ReportError as exc:
           return _report_error(exc)

    @app.put("/api/deliverables/{report_id}")
    def deliverables_update(
    report_id: str,
    request: Request,
    payload: dict = Body(...),
    role: str = writer,
    actor: str = Depends(current_actor),
    ):
        from .deliverables.report import ReportError

        version = payload.get("expected_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise HTTPException(
                status_code=422,
                detail="expected_version (entier) obligatoire",
            )

        note = payload.get("note", "")
        try:
            return request.app.state.reports.update(
                report_id,
                actor,
                role,
                expected_version=version,
                title=payload.get("title"),
                meta=payload.get("meta"),
                sections=payload.get("sections"),
                note=note if isinstance(note, str) else "",
            )
        except ReportError as exc:
           return _report_error(exc)

    @app.post("/api/deliverables/{report_id}/{action}")
    def deliverables_action(
    report_id: str,
    action: str,
    request: Request,
    payload: dict = Body(default={}),
    role: str = Depends(require(*READ_ROLES)),
    actor: str = Depends(current_actor),
):
        from .deliverables.report import ReportError

        try:
            if action == "duplicate":
                return Response(
                    content=__import__("json").dumps(
                        request.app.state.reports.duplicate(
                            report_id, actor, role
                        ),
                        ensure_ascii=False,
                        default=str,
                    ),
                    status_code=201,
                    media_type="application/json",
                )

            comment = payload.get("comment", "")
            return request.app.state.reports.transition(
                report_id,
                action,
                actor,
                role,
                comment if isinstance(comment, str) else "",
            )
        except ReportError as exc:
           return _report_error(exc)

    # Le montage statique doit impérativement rester le dernier.
    if WEB_ROOT.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=WEB_ROOT, html=True),
            name="web",
        )

    return app


app = create_app()

__all__ = ["app", "create_app", "RATED_COUNTRIES"]
