"""Cas d'usage appelés par l'API (testables sans serveur HTTP)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .classify import EVENT_TYPE_LABELS_FR
from .countries import COUNTRIES_BY_ISO, RATED_COUNTRIES
from .models import Event, RawDocument
from .pipeline import Outcome, process_document
from .scoring import CountryRisk, ScoringConfig, compute_country_risk
from .store_protocol import StoreProtocol
from .taxonomy import LEVEL_LABELS_FR


def safe_url(url: str | None) -> str | None:
    """Les liens viennent de flux externes : seuls http(s) sont exposés (jamais javascript:, data:…)."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    return url.strip() if parsed.scheme in ("http", "https") and parsed.netloc else None


def event_sources(e: Event, store: StoreProtocol | None) -> list[dict]:
    """Références de l'événement avec le lien vers l'article d'origine (si disponible)."""
    out = []
    for ref in e.refs:
        doc = store.get_document(ref.document_id) if store is not None else None
        source = store.get_source(ref.source_id) if store is not None else None
        out.append({
            "source_id": ref.source_id,
            "source_name": source.name if source else ref.source_id,
            "channel": source.channel if source else "media",
            "reliability": ref.reliability,
            "url": safe_url(doc.url) if doc else None,
        })
    return out


def event_to_dict(e: Event, store: StoreProtocol | None = None) -> dict:
    return {
        "id": e.id,
        "occurred_at": e.occurred_at,
        "country": e.country_iso3,
        "region": COUNTRIES_BY_ISO[e.country_iso3].region.value,
        "secondary_countries": list(e.secondary_countries),
        "domain": e.domain.value,
        "secondary_domains": [d.value for d in e.secondary_domains],
        "event_type": e.event_type,
        "event_type_label": EVENT_TYPE_LABELS_FR.get(e.event_type, e.event_type),
        "title": e.title,
        "summary": e.summary,
        "language": e.language,
        "admiralty": e.admiralty,
        "probability": e.probability,
        "impact": e.impact,
        "criticality": e.criticality,
        "level": e.level,
        "level_label": LEVEL_LABELS_FR[e.level],
        "source_count": e.source_count,
        "evidence": list(e.evidence),
        "sources": event_sources(e, store),
    }


def ingest_manual(
    store: StoreProtocol,
    *,
    title: str,
    text: str,
    source_id: str,
    url: str | None = None,
    published_at: datetime | None = None,
) -> Outcome:
    doc = RawDocument(
        source_id=source_id, title=title, text=text, url=url,
        published_at=published_at, fetched_at=datetime.now(timezone.utc),
    )
    return process_document(store, doc)


def risk_for_country(
    store: StoreProtocol, iso3: str, as_of: datetime | None = None, cfg: ScoringConfig | None = None
) -> CountryRisk:
    as_of = as_of or datetime.now(timezone.utc)
    events = store.events(country=iso3)
    languages = [e.language for e in events]
    return compute_country_risk(events, iso3, as_of, cfg, languages)


def risk_map(
    store: StoreProtocol, as_of: datetime | None = None, cfg: ScoringConfig | None = None
) -> list[CountryRisk]:
    """Score des 54 pays, du plus risqué au moins risqué."""
    as_of = as_of or datetime.now(timezone.utc)
    return sorted(
        (risk_for_country(store, c.iso3, as_of, cfg) for c in RATED_COUNTRIES),
        key=lambda r: r.score,
        reverse=True,
    )


def risk_to_dict(r: CountryRisk) -> dict:
    d = asdict(r)
    d["level_label"] = LEVEL_LABELS_FR[r.level]
    d["country_name"] = COUNTRIES_BY_ISO[r.iso3].name_fr
    d["region"] = COUNTRIES_BY_ISO[r.iso3].region.value
    return d


def coverage_report(store: StoreProtocol, as_of: datetime | None = None, window_days: int = 30) -> dict:
    """Indicateurs de biais de couverture : langues, routage, pays sans données ou à source unique."""
    as_of = as_of or datetime.now(timezone.utc)
    start = as_of - timedelta(days=window_days)
    docs = [d for d in store.documents() if d.fetched_at >= start]
    by_language = Counter(d.language or "und" for d in docs)
    total = len(docs)
    routed = sum(1 for d in docs if d.routing_status == "routed")

    sources_by_country: dict[str, set[str]] = {c.iso3: set() for c in RATED_COUNTRIES}
    events_by_region: Counter[str] = Counter()
    for e in store.events(since=start):
        if e.country_iso3 in sources_by_country:
            sources_by_country[e.country_iso3].update(r.source_id for r in e.refs)
        events_by_region[COUNTRIES_BY_ISO[e.country_iso3].region.value] += 1

    return {
        "window_days": window_days,
        "documents": total,
        "routing_rate": round(routed / total, 3) if total else None,
        "language_share": {k: round(v / total, 3) for k, v in by_language.most_common()} if total else {},
        "countries_without_events": sorted(i for i, s in sources_by_country.items() if not s),
        "countries_single_source": sorted(i for i, s in sources_by_country.items() if len(s) == 1),
        "events_by_region": dict(events_by_region),
        "note": "Un pays sans événement n'est pas nécessairement sans risque : c'est d'abord un signal de couverture.",
    }
