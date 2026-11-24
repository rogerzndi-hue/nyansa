"""Serveur de dÃ©veloppement local (bibliothÃ¨que standard uniquement).

Sert le front statique (web/) et expose en lecture seule les mÃªmes donnÃ©es que l'API FastAPI,
issues du jeu FICTIF de dÃ©monstration, ou (--live) de flux RSS rÃ©els rafraÃ®chis pÃ©riodiquement. Ã‰coute uniquement sur 127.0.0.1 : poste local, sans
authentification, jamais en production.

    cd backend && python3 -m nyansa.devserver [--port 8000] [--live ../config/sources.json] [--interval 15]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import mimetypes
import re
import threading
import time
from datetime import datetime, timezone
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import services
from .deliverables.builder import build_report, parse_params
from .deliverables.catalog import catalog as deliverables_catalog
from .deliverables.pdf import PdfUnavailable, render_pdf
from .deliverables.render import render_docx, render_html
from .deliverables.report import ROLE_EDIT, ROLE_READ_FINAL, ROLE_VALIDATE, Forbidden, ReportError, ReportStore, check_report
from .countries import COUNTRIES_BY_ISO
from .demo import load_demo
from .ingest import fetch, load_sources
from .alerts import AlertCenter, RuleError, load_rules
from .archive import DocumentArchive
from .runner import SourceReport, refresh
from .social import (STATUS_LABELS_FR, WATCH_ROLES, WatchBook, WatchError, assert_public_host, load_demo_social, load_seed,
                     social_view, sync_sources)
from .store import InMemoryStore
from .runtime import create_runtime
from .taxonomy import DOMAIN_LABELS_FR, REGION_LABELS_FR, Domain, Region

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
CACHE_TTL_S = 30.0  # les scores dÃ©pendent de l'heure (dÃ©croissance) : on recalcule au plus tard aprÃ¨s 30 s
CACHE_MAX = 64
GZIP_MIN = 1_024
MAX_BODY = 1_000_000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
ROLES = ROLE_EDIT | ROLE_VALIDATE | ROLE_READ_FINAL
# Export HTML d'un livrable : aucun script, styles en ligne uniquement, pas d'images externes.
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
EXPORTS = {
    "html": ("text/html; charset=utf-8", "html"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": ("application/pdf", "pdf"),
}


def _hostname(header: str) -> str:
    host = header.strip().lower()
    if host.startswith("["):  # IPv6 : [::1]:8000
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Non sÃ©rialisable : {type(value)!r}")


class LiveState:
    """Derniers rapports de collecte, partagÃ©s entre le thread de collecte et les requÃªtes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: dict[str, SourceReport] = {}

    def update(self, reports: list[SourceReport]) -> None:
        with self._lock:
            for r in reports:
                self._reports[r.source_id] = r

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [r.__dict__.copy() for r in self._reports.values()]


def make_handler(
    store: InMemoryStore,
    web_root: Path = WEB_ROOT,
    demo: bool = True,
    live: LiveState | None = None,
    lock: threading.RLock | None = None,
    reports: ReportStore | None = None,
    alerts: AlertCenter | None = None,
    watches: WatchBook | None = None,
    on_watch_change: Callable[[], None] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """`lock` protÃ¨ge le store (non thread-safe) entre requÃªtes et thread de collecte."""
    lock = lock or threading.RLock()
    reports = reports if reports is not None else ReportStore()
    alerts = alerts if alerts is not None else AlertCenter(external=False)
    watches = watches if watches is not None else WatchBook()
    cache: dict[str, tuple[int, float, bytes, str]] = {}
    static_cache: dict[Path, tuple[float, int, bytes, str]] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "NyansaDev/0.1"

        def log_message(self, fmt: str, *args: object) -> None:  # silencieux
            pass

        def _send(self, status: int, body: bytes, ctype: str, etag: str | None = None, extra: dict[str, str] | None = None) -> None:
            """RÃ©ponse avec ETag (304 si inchangÃ©) et compression gzip des corps compressibles."""
            etag = etag or '"' + hashlib.md5(body).hexdigest()[:16] + '"'  # noqa: S324 : somme de contrÃ´le, pas de sÃ©curitÃ©
            if status == 200 and self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            compressible = ctype.startswith(("text/", "application/json", "application/javascript"))
            gz = compressible and len(body) >= GZIP_MIN and "gzip" in self.headers.get("Accept-Encoding", "")
            if gz:
                body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            extra = extra or {}
            if "Cache-Control" not in extra:
                self.send_header("Cache-Control", "no-cache")  # le navigateur revalide (ETag) : rapide, jamais pÃ©rimÃ©
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Vary", "Accept-Encoding")
            if status == 200:
                self.send_header("ETag", etag)
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, status: int = 200, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra=extra)

        def _cached_json(self, key: str, build) -> None:
            """Sert une rÃ©ponse calculÃ©e, rÃ©utilisÃ©e tant que le magasin n'a pas changÃ© (et < 30 s)."""
            hit = cache.get(key)
            now = time.monotonic()
            if hit and hit[0] == store.version and now - hit[1] < CACHE_TTL_S:
                return self._send(200, hit[2], "application/json; charset=utf-8", hit[3])
            body = json.dumps(build(), default=_json_default, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'  # noqa: S324
            if len(cache) >= CACHE_MAX:
                cache.clear()
            cache[key] = (store.version, now, body, etag)
            self._send(200, body, "application/json; charset=utf-8", etag)

        def _local_host(self) -> bool:
            """DÃ©fense contre le DNS rebinding : seul un nom d'hÃ´te local est acceptÃ©."""
            return _hostname(self.headers.get("Host", "")) in LOCAL_HOSTS

        # --- livrables : Ã©critures (POST, PUT) -------------------------------------------------------------------
        def _write_payload(self) -> dict | None:
            """Valide le cadrage HTTP, consomme le corps born?, puis contr?le les en-t?tes."""

            def framing_error(message: str, status: int) -> None:
                self.close_connection = True
                self._json({"detail": message}, status, extra={"Connection": "close"})

            # Refuser les cadrages ambigus avant toute lecture du corps.
            if self.headers.get("Transfer-Encoding") is not None:
                framing_error("Transfer-Encoding non pris en charge", 400)
                return None

            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                framing_error("Content-Length requis", 411)
                return None
            if len(lengths) != 1:
                framing_error("Content-Length multiple", 400)
                return None

            try:
                length = int(lengths[0])
            except (TypeError, ValueError):
                framing_error("Longueur invalide", 400)
                return None

            if length < 0:
                framing_error("Longueur invalide", 400)
                return None
            if length > MAX_BODY:
                framing_error("Corps trop volumineux", 413)
                return None

            # Lire exactement le corps born? avant de rejeter les en-t?tes applicatifs.
            try:
                raw = self.rfile.read(length)
            except OSError:
                self.close_connection = True
                return None

            if len(raw) != length:
                framing_error("Corps incomplet", 400)
                return None

            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")

            if not self._local_host() or (
                origin is not None and origin != f"http://{host}"
            ):
                self._json({"detail": "Origine refus?e"}, 403)
                return None

            if self.headers.get("X-Nyansa") != "1" or not (
                self.headers.get("Content-Type") or ""
            ).startswith("application/json"):
                self._json({"detail": "En-t?tes requis manquants"}, 403)
                return None

            try:
                payload = json.loads(raw or b"{}")
            except (ValueError, UnicodeDecodeError):
                self._json({"detail": "JSON invalide"}, 400)
                return None

            if not isinstance(payload, dict):
                self._json({"detail": "Objet JSON attendu"}, 400)
                return None

            return payload

        def _identity(self) -> tuple[str, str]:
            """IdentitÃ© DÃ‰CLARATIVE (en-tÃªtes X-Dev-User / X-Dev-Role) : poste local sans authentification.
            Elle sert Ã  tester les droits par rÃ´le ; en production elle viendra d'OIDC."""
            role = (self.headers.get("X-Dev-Role") or "analyst").strip().lower()
            if role not in ROLES:
                raise Forbidden(f"RÃ´le inconnu : {role!r}")
            user = " ".join(unquote(self.headers.get("X-Dev-User") or "").split())[:80] or "analyste"  # envoyÃ© encodÃ© (accents)
            return user, role

        def _reports_call(self, method: str, parts: list[str], query: dict[str, list[str]], payload: dict | None) -> None:
            first = lambda k: query[k][0] if k in query else None  # noqa: E731
            try:
                actor, role = self._identity()
                if method == "GET":
                    return self._reports_get(parts, first, actor, role)
                payload = payload or {}
                if method == "POST" and not parts:
                    try:
                        params = parse_params(payload)
                    except ValueError as exc:
                        return self._json({"detail": str(exc)}, 422)
                    with lock:  # lecture du magasin d'Ã©vÃ©nements
                        built = build_report(store, params, demo=demo)
                    return self._json(reports.create(built, actor, role), 201)
                if method == "PUT" and len(parts) == 1:
                    version = payload.get("expected_version")
                    if not isinstance(version, int) or isinstance(version, bool):
                        return self._json({"detail": "expected_version (entier) obligatoire"}, 422)
                    note = payload.get("note", "")
                    return self._json(reports.update(parts[0], actor, role, expected_version=version, title=payload.get("title"),
                                                     meta=payload.get("meta"), sections=payload.get("sections"),
                                                     note=note if isinstance(note, str) else ""))
                if method == "POST" and len(parts) == 2:
                    rid, action = parts
                    if action == "duplicate":
                        return self._json(reports.duplicate(rid, actor, role), 201)
                    comment = payload.get("comment", "")
                    return self._json(reports.transition(rid, action, actor, role, comment if isinstance(comment, str) else ""))
                return self._json({"detail": "Introuvable"}, 404)
            except ReportError as exc:
                return self._json({"detail": str(exc), "issues": exc.issues}, exc.status)

        def _reports_get(self, parts: list[str], first, actor: str, role: str) -> None:
            if parts == ["catalog"]:
                return self._json(deliverables_catalog(first("lang") or "fr"))
            if parts == ["capabilities"]:
                return self._json({
                    "pdf": importlib.util.find_spec("reportlab") is not None, "docx": True, "html": True,
                    "llm": False,  # aucun modÃ¨le de langage branchÃ© : sections d'analyse Ã  rÃ©diger par l'analyste
                    "demo": demo, "role": role, "actor": actor, "roles": sorted(ROLES),
                    "persistent": reports.persistent,
                })
            if not parts:
                return self._json(reports.list(role, status=first("status"), template_id=first("template"), q=first("q")))
            rid, sub = parts[0], parts[1] if len(parts) > 1 else ""
            if not sub:
                return self._json(reports.get(rid, role), extra={"Cache-Control": "no-store"})
            if sub == "versions":
                return self._json(reports.versions(rid, role))
            if sub == "check":
                return self._json({"issues": check_report(reports.get(rid, role))})
            if sub == "diff":
                try:
                    a, b = int(first("a") or ""), int(first("b") or "")
                except ValueError:
                    return self._json({"detail": "ParamÃ¨tres a et b (versions) obligatoires"}, 422)
                return self._json(reports.diff(rid, a, b, role))
            if sub.startswith("export."):
                fmt = sub[len("export."):]
                if fmt not in EXPORTS:
                    return self._json({"detail": "Format inconnu (html, docx, pdf)"}, 404)
                report = reports.record_export(rid, actor, role, fmt)
                try:
                    body = {"html": lambda r: render_html(r).encode("utf-8"), "docx": render_docx, "pdf": render_pdf}[fmt](report)
                except PdfUnavailable as exc:
                    return self._json({"detail": f"{exc}. Repli : ouvrir l'export HTML puis Imprimer > Enregistrer en PDF."}, 501)
                slug = re.sub(r"[^a-z0-9]+", "-", report["template_id"].lower()).strip("-")
                name = f"{slug}-v{report['version']}-{report['status']}.{EXPORTS[fmt][1]}"
                extra = {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{name}"'}
                if fmt == "html":
                    extra.update({"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Content-Disposition": f'inline; filename="{name}"'})
                return self._send(200, body, EXPORTS[fmt][0], extra=extra)
            return self._json({"detail": "Introuvable"}, 404)

        def _watch_write(self, rest: list[str], payload: dict) -> None:
            """Veilles : crÃ©ation, activation/dÃ©sactivation, suppression (POST, mÃªmes protections que les autres Ã©critures)."""
            try:
                actor, role = self._identity()
                if role not in WATCH_ROLES:
                    raise Forbidden("GÃ©rer les veilles : rÃ´le analyste, responsable de veille ou admin")
                with lock:
                    if not rest:
                        w = watches.add(payload, actor)
                        store.audit(actor, "watch.add", w.id)
                        result, status = w.to_dict(), 201
                    elif len(rest) == 2 and rest[1] == "toggle":
                        if not isinstance(payload.get("active"), bool):
                            return self._json({"detail": "active (boolÃ©en) obligatoire"}, 422)
                        result, status = watches.set_active(rest[0], payload["active"]).to_dict(), 200
                        store.audit(actor, "watch.toggle", rest[0])
                    elif len(rest) == 2 and rest[1] == "delete":
                        result, status = watches.remove(rest[0]).to_dict(), 200
                        store.audit(actor, "watch.delete", rest[0])
                    else:
                        return self._json({"detail": "Introuvable"}, 404)
                    sync_sources(store, watches)
                if on_watch_change:
                    on_watch_change()  # lance une collecte sans attendre le prochain cycle
                return self._json(result, status)
            except ReportError as exc:
                return self._json({"detail": str(exc)}, exc.status)
            except WatchError as exc:
                return self._json({"detail": str(exc)}, 422)
            except KeyError:
                return self._json({"detail": "Veille introuvable"}, 404)

        def _read_body_and_call(self, method: str) -> None:
            path = urlparse(self.path).path
            payload = self._write_payload()
            if payload is None:
                return
            parts = path.split("/")
            if method == "POST" and len(parts) == 5 and parts[:3] == ["", "api", "alerts"] and parts[4] == "ack":
                try:
                    actor, role = self._identity()
                    if role not in ROLE_EDIT | ROLE_VALIDATE:
                        raise Forbidden("Acquitter une alerte : rÃ´le analyste, validateur, responsable ou admin")
                    return self._json(alerts.acknowledge(parts[3], actor))
                except ReportError as exc:
                    return self._json({"detail": str(exc)}, exc.status)
                except KeyError:
                    return self._json({"detail": "Alerte introuvable"}, 404)
            if method == "POST" and parts[:4] == ["", "api", "social", "watches"]:
                return self._watch_write(parts[4:], payload)
            if not path.startswith("/api/deliverables"):
                return self._json({"detail": "Introuvable"}, 404)
            return self._reports_call(method, path.split("/")[3:], {}, payload)

        def do_POST(self) -> None:  # noqa: N802
            self._read_body_and_call("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._read_body_and_call("PUT")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            if not self._local_host():
                return self._json({"detail": "HÃ´te refusÃ©"}, 403)
            if path.startswith("/api/deliverables"):
                return self._reports_call("GET", path.split("/")[3:], query, None)
            if path.startswith("/api/"):
                with lock:
                    return self._api(path[4:], query)
            return self._static(path)

        def _api(self, path: str, query: dict[str, list[str]]) -> None:
            first = lambda k: query[k][0] if k in query else None  # noqa: E731
            if path == "/health":
                return self._json({"status": "ok", "demo": demo})
            if path == "/meta":
                return self._json({
                    "demo": demo,
                    "mode": "demo" if demo else "live",
                    "domains": {d.value: DOMAIN_LABELS_FR[d] for d in Domain},
                    "regions": {r.value: REGION_LABELS_FR[r] for r in Region},
                })
            if path == "/risk/countries":
                return self._cached_json("risk", lambda: [services.risk_to_dict(r) for r in services.risk_map(store)])
            if path == "/events":
                try:
                    domain = Domain(first("domain")) if first("domain") else None
                    region = Region(first("region")) if first("region") else None
                    limit = min(max(int(first("limit") or 100), 1), 500)
                except ValueError:
                    return self._json({"detail": "ParamÃ¨tre invalide"}, 422)
                country = (first("country") or "").upper() or None
                if country and country not in COUNTRIES_BY_ISO:
                    return self._json({"detail": "Pays inconnu"}, 404)
                key = f"events|{country}|{region}|{domain}|{limit}"
                return self._cached_json(key, lambda: [services.event_to_dict(e, store) for e in store.events(country=country, region=region, domain=domain, limit=limit)])
            if path == "/sources":
                return self._json([
                    {"id": s.id, "name": s.name, "kind": s.kind, "channel": s.channel, "active": s.active, "reliability": s.default_reliability}
                    for s in store.sources()
                ] if live is None else {
                    "sources": [
                        {"id": s.id, "name": s.name, "kind": s.kind, "channel": s.channel, "active": s.active, "reliability": s.default_reliability}
                        for s in store.sources()
                    ],
                    "reports": live.snapshot(),
                })
            if path == "/unrouted":
                docs, per_source = [], {}
                for d in reversed(store.unrouted()):  # du plus rÃ©cent au plus ancien ; 10 par source, 200 au plus
                    if per_source.get(d.source_id, 0) < 10 and len(docs) < 200:
                        per_source[d.source_id] = per_source.get(d.source_id, 0) + 1
                        docs.append(d)
                docs.sort(key=lambda d: d.source_id)
                return self._json([
                    {"source_id": d.source_id, "title": d.title, "reason": d.reject_reason, "url": d.url}
                    for d in docs
                ])
            if path == "/alerts":
                status = first("status")
                if status not in (None, "new", "ack"):
                    return self._json({"detail": "status : new ou ack"}, 422)
                try:
                    limit = min(max(int(first("limit") or 50), 1), 200)
                except ValueError:
                    return self._json({"detail": "ParamÃ¨tre invalide"}, 422)
                return self._json({"alerts": alerts.list(status, limit), "external": alerts.external})
            if path == "/social":
                status = first("status")
                if status not in (None, "relevant", *STATUS_LABELS_FR):
                    return self._json({"detail": f"status : relevant, {', '.join(STATUS_LABELS_FR)}"}, 422)
                try:
                    limit = min(max(int(first("limit") or 100), 1), 300)
                except ValueError:
                    return self._json({"detail": "ParamÃ¨tre invalide"}, 422)
                return self._json(social_view(store, watches, live.snapshot() if live else None, demo=demo, watch=first("watch"),
                                              status=status, country=(first("country") or "").upper() or None, limit=limit))
            if path == "/alerts/rules":
                return self._json(alerts.describe_rules())
            if path == "/coverage":
                return self._cached_json("coverage", lambda: services.coverage_report(store))
            return self._json({"detail": "Introuvable"}, 404)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (web_root / rel).resolve()
            if web_root.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"Introuvable", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            stat = target.stat()
            hit = static_cache.get(target)
            if not hit or hit[0] != stat.st_mtime or hit[1] != stat.st_size:  # relu seulement si le fichier a changÃ©
                data = target.read_bytes()
                hit = (stat.st_mtime, stat.st_size, data, '"' + hashlib.md5(data).hexdigest()[:16] + '"')  # noqa: S324
                static_cache[target] = hit
            self._send(200, hit[2], ctype, hit[3])

    return Handler


def build_store() -> InMemoryStore:
    store = InMemoryStore()
    load_demo(store)
    return store


def refresh_once(
    store: InMemoryStore, live: LiveState, lock: threading.RLock, fetcher=fetch, alerts: AlertCenter | None = None,
    archive: DocumentArchive | None = None,
) -> list[SourceReport]:
    """Un cycle de collecte. Le rÃ©seau est appelÃ© HORS verrou ; seul le traitement est verrouillÃ©."""
    fetched: dict[str, bytes | Exception] = {}
    for source in list(store.sources()):
        if source.active and source.kind == "rss" and source.url:
            try:
                if source.id.startswith("watch-"):
                    assert_public_host(source.url)  # l'instance vient de l'interface : jamais d'adresse interne
                fetched[source.url] = fetcher(source.url)
            except Exception as exc:  # noqa: BLE001 : isolÃ© par source, consignÃ© dans le rapport
                fetched[source.url] = exc

    def cached(url: str) -> bytes:
        result = fetched[url]
        if isinstance(result, Exception):
            raise result
        return result

    with lock:
        reports = refresh(store, cached)
        if archive is not None:
            try:
                archive.save(store)
            except OSError as exc:  # disque plein ou dossier en lecture seule : la collecte continue, l'historique ne sera pas conservÃ©
                print(f"Archive des documents non enregistrÃ©e : {exc}")
        if alerts is not None:
            alerts.collect(store)  # lit le magasin : sous verrou
    live.update(reports)
    if alerts is not None:
        alerts.deliver_pending()  # envois rÃ©seau : hors verrou
    return reports


def refresh_loop(store: InMemoryStore, live: LiveState, lock: threading.RLock, interval_s: float, stop: threading.Event,
                 alerts: AlertCenter | None = None, wake: threading.Event | None = None, archive: DocumentArchive | None = None) -> None:
    """Collecte pÃ©riodique ; `wake` dÃ©clenche un cycle immÃ©diat (nouvelle veille)."""
    while not stop.is_set():
        refresh_once(store, live, lock, alerts=alerts, archive=archive)
        if wake is None:
            stop.wait(interval_s)
        else:
            wake.wait(interval_s)
            wake.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--live", metavar="SOURCES_JSON", help="mode rÃ©el : flux RSS du fichier, sans donnÃ©es fictives")
    parser.add_argument("--interval", type=float, default=15.0, help="minutes entre deux collectes (dÃ©faut 15)")
    parser.add_argument("--alert-rules", default="../config/alert_rules.json", help="rÃ¨gles d'alerte (JSON) ; absent : une rÃ¨gle prudente, application seulement")
    parser.add_argument("--watches", default="../config/watches.json", help="veilles rÃ©seaux sociaux initiales (JSON, facultatif) ; mode rÃ©el seulement")
    parser.add_argument("--data-dir", default="data", help="dossier des livrables en mode rÃ©el (dÃ©faut : ./data) ; mode dÃ©mo : en mÃ©moire")
    args = parser.parse_args()

    runtime = create_runtime(
        live_sources_path=args.live,
        data_dir=args.data_dir,
        alert_rules_path=args.alert_rules,
        watches_path=args.watches,
        demo_store_factory=build_store,
    )

    lock = runtime.lock
    store = runtime.store
    rules = runtime.rules
    watches = runtime.watches
    alerts = runtime.alerts
    reports = runtime.reports
    live: LiveState | None = LiveState() if runtime.live_mode else None

    if runtime.live_mode:
        archive = runtime.archive
        if archive is None:
            raise RuntimeError("Archive absente en mode réel.")

        print(f"Historique : {runtime.restored_documents} document(s) rechargé(s) depuis {archive.path} (30 jours au plus)")
        active = [s for s in store.sources() if s.active and s.kind == "rss"]
        runtime.start_collector(
            target=refresh_loop,
            args=(
                store,
                live,
                lock,
                args.interval * 60,
                runtime.stop_event,
                alerts,
                runtime.wake_event,
                archive,
            ),
        )
        print(f"Mode réel : {len(active)} source(s) RSS actives, collecte toutes les {args.interval:g} min")
    else:
        print("Mode démonstration (données fictives)")

    print(f"Veilles réseaux sociaux : {len(watches.list())}" + (" (fictives, démonstration)" if not runtime.live_mode else f" ; enregistrées dans {Path(args.data_dir) / 'watches.json'}"))
    print(f"Alertes : {sum(r.active for r in rules)} règle(s) active(s) ; notifications externes " + ("activées (webhook, e-mail selon les règles)" if runtime.live_mode else "désactivées en démonstration"))
    print("Livrables : " + (f"enregistrés dans {Path(args.data_dir) / 'reports.json'}" if runtime.live_mode else "en mémoire (perdus à l'arrêt, mode démo)")
          + " ; identité déclarative, sans authentification")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, demo=live is None, live=live, lock=lock, reports=reports, alerts=alerts, watches=watches, on_watch_change=runtime.wake_collector))
    print(f"Nyansa : http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.stop_collector()


if __name__ == "__main__":
    main()
