"""Pipeline de normalisation : document brut -> événement qualifié (ou file « à qualifier »).

Étapes : nettoyage, déduplication, langue, pays, domaine et type, événement (avec fusion
des événements équivalents). Un document sans pays ou sans domaine fiable n'est jamais
deviné : il part en file « unrouted » avec sa raison, pour qualification par un analyste.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .classify import classify
from .geo import extract_countries
from .lang import detect_language
from .models import Event, RawDocument
from .store_protocol import StoreProtocol
from .text import fold, strip_html

SUMMARY_CHARS = 280


@dataclass(frozen=True)
class Outcome:
    status: str  # duplicate | unrouted | routed
    document_id: str
    event_id: str | None = None
    created: bool = False  # True si un nouvel événement a été créé (False : fusionné)
    reason: str | None = None


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{fold(title)}|{fold(text)[:2000]}".encode()).hexdigest()


def _summary(text: str, title: str) -> str:
    base = text or title
    if len(base) <= SUMMARY_CHARS:
        return base
    cut = base[:SUMMARY_CHARS].rsplit(" ", 1)[0]
    return cut + "…"


STALE_AFTER = timedelta(days=45)
_URL_DATE = re.compile(r"/((?:19|20)\d\d)[/-](0[1-9]|1[0-2])(?:[/-](0[1-9]|[12]\d|3[01]))?(?=[/\-_.]|$)")


def url_date(url: str | None) -> datetime | None:
    """Date lisible dans l'adresse (/2026/09/…, /2026-09-12-…) ; None si absente. Sans jour, on prend la fin du mois (borne la plus récente)."""
    if not url:
        return None
    m = _URL_DATE.search(url)
    if m is None:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if day:
            return datetime(year, month, int(day), tzinfo=timezone.utc)
        nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
        return nxt - timedelta(days=1)
    except ValueError:
        return None



def process_document(store: StoreProtocol, doc: RawDocument) -> Outcome:
    source = store.get_source(doc.source_id)
    if source is None:
        raise ValueError(f"Source inconnue : {doc.source_id!r}")

    # 1. Normalisation et préparation complète du document.
    doc.title = strip_html(doc.title)
    doc.text = strip_html(doc.text)
    doc.content_hash = content_hash(doc.title, doc.text)

    language, _ = detect_language(f"{doc.title}. {doc.text}")
    doc.language = language if language != "und" else (source.language or "und")

    # 2. Classification sans persistance prématurée.
    candidate = None
    published = doc.published_at or doc.fetched_at
    written = url_date(doc.url)

    if written is not None and published - written > STALE_AFTER:
        doc.routing_status = "unrouted"
        doc.reject_reason = "stale"
        doc.country_iso3 = None
    else:
        extraction = extract_countries(doc.title, doc.text)

        if extraction.primary is None:
            doc.routing_status = "unrouted"
            doc.reject_reason = (
                "ambiguous_country" if extraction.ambiguous else "no_country"
            )
            doc.country_iso3 = None

        elif extraction.foreign_focus:
            doc.routing_status = "unrouted"
            doc.reject_reason = "foreign_focus"
            doc.country_iso3 = None

        else:
            classification = classify(doc.title, doc.text)

            if classification is None:
                doc.routing_status = "unrouted"
                doc.reject_reason = "no_domain"
                doc.country_iso3 = None
            else:
                occurred = published
                now = datetime.now(timezone.utc)
                if occurred > now:
                    occurred = now

                doc.routing_status = "routed"
                doc.reject_reason = None
                doc.country_iso3 = extraction.primary

                candidate = Event(
                    occurred_at=occurred,
                    country_iso3=extraction.primary,
                    secondary_countries=extraction.secondary(),
                    domain=classification.domain,
                    secondary_domains=classification.secondary_domains,
                    event_type=classification.event_type,
                    impact=classification.impact,
                    title=doc.title,
                    summary=_summary(doc.text, doc.title),
                    language=doc.language,
                    refs=[store.source_ref(doc)],
                    evidence=classification.evidence,
                )

    # 3. Persistance atomique : document et événement ensemble.
    with store.transaction():
        if not store.add_document(doc):
            return Outcome("duplicate", doc.id)

        if candidate is None:
            return Outcome(
                "unrouted",
                doc.id,
                reason=doc.reject_reason,
            )

        event, created = store.register_event(candidate)

        return Outcome(
            "routed",
            doc.id,
            event_id=event.id,
            created=created,
        )

