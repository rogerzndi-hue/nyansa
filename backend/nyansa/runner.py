"""Collecte planifiée : lit les sources RSS actives et passe chaque document dans le pipeline.

Une source en échec n'arrête jamais les autres ; l'erreur est consignée dans le rapport.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .ingest import FeedError, fetch, parse_feed_stats
from .pipeline import process_document
from .store import InMemoryStore

MAX_TEXT = 1_000  # on garde titre, résumé court et lien : jamais l'article complet
MAX_TEXT_SOCIAL = 400
# Vie privée : on ne conserve ni les @pseudonymes ni les adresses e-mail des publications sociales.
_HANDLE = re.compile(r"(?<![\w.])@[\w.-]+(?:@[\w.-]+)?")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


_LOWER_UPPER = re.compile(r"[a-zà-öø-ÿ][A-ZÀ-ÖØ-Þ]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-zà-öø-ÿ])(?=[A-ZÀ-ÖØ-Þ])")


def expand_hashtags(text: str) -> str:
    """« #BurkinaFaso » -> « Burkina Faso » : sans cela le pays n'est pas reconnu dans un hashtag en CamelCase.
    Les hashtags en minuscules ne sont pas touchés (le « # » ne gêne pas la reconnaissance)."""
    return re.sub(r"#(\w+)", lambda m: _CAMEL_BOUNDARY.sub(" ", m.group(1)) if _LOWER_UPPER.search(m.group(1)) else m.group(0), text)


def anonymize(text: str) -> str:
    # pseudonymes d'abord : « @user@instance.tld » ressemble à une adresse e-mail et serait sinon mal remplacé
    return _EMAIL.sub("[e-mail]", _HANDLE.sub("@…", text))


@dataclass
class SourceReport:
    source_id: str
    ok: bool
    at: datetime
    documents: int = 0
    routed: int = 0
    duplicates: int = 0
    unrouted: int = 0
    reasons: dict[str, int] = field(default_factory=dict)  # no_country | ambiguous_country | foreign_focus | stale | no_domain
    error: str | None = None
    skipped: int = 0  # éléments du flux sans titre ni texte, ignorés
    # Cumul en base pour la source : les cycles suivants voient tout en « doublon », le dernier cycle seul serait trompeur.
    total: int = 0
    total_routed: int = 0
    total_unrouted: int = 0


def refresh(
    store: InMemoryStore, fetcher: Callable[[str], bytes] = fetch, now: datetime | None = None
) -> list[SourceReport]:
    now = now or datetime.now(timezone.utc)
    reports: list[SourceReport] = []
    for source in store.sources():
        if not (source.active and source.kind == "rss" and source.url):
            continue
        report = SourceReport(source.id, ok=False, at=now)
        try:
            docs, report.skipped = parse_feed_stats(fetcher(source.url), source.id, fetched_at=now)
            for doc in docs:
                if source.channel == "social":
                    doc.title, doc.text = anonymize(expand_hashtags(doc.title)), anonymize(expand_hashtags(doc.text))[:MAX_TEXT_SOCIAL]
                else:
                    doc.text = doc.text[:MAX_TEXT]
                outcome = process_document(store, doc)
                report.documents += 1
                report.routed += outcome.status == "routed"
                report.duplicates += outcome.status == "duplicate"
                report.unrouted += outcome.status == "unrouted"
                if outcome.reason:
                    report.reasons[outcome.reason] = report.reasons.get(outcome.reason, 0) + 1
            report.ok = True
        except (FeedError, OSError, ValueError) as exc:  # réseau, XML invalide, schéma refusé
            report.error = f"{type(exc).__name__}: {exc}"[:300]
        reports.append(report)
    by_source = {r.source_id: r for r in reports}
    for d in store.documents():  # un seul passage pour toutes les sources
        r = by_source.get(d.source_id)
        if r is not None:
            r.total += 1
            r.total_routed += d.routing_status == "routed"
            r.total_unrouted += d.routing_status == "unrouted"
    return reports
