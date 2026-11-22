"""Modèles du domaine (indépendants du stockage et de l'API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from .admiralty import probability as _probability
from .taxonomy import Domain, criticality_level


@dataclass
class Source:
    id: str
    name: str
    kind: str  # rss | api | dataset | cti | manual
    default_reliability: str  # A..F (échelle Admiralty)
    url: str | None = None
    language: str | None = None
    country_iso3: str | None = None
    license_note: str | None = None
    active: bool = True
    channel: str = "media"  # media | social
    publisher_id: str | None = None  # Identit? ?ditoriale commune ? plusieurs flux


@dataclass
class RawDocument:
    source_id: str
    title: str
    text: str
    fetched_at: datetime
    url: str | None = None
    published_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    language: str | None = None
    content_hash: str | None = None
    routing_status: str = "pending"  # pending | routed | unrouted
    reject_reason: str | None = None  # no_country | ambiguous_country | foreign_focus | stale | no_domain
    country_iso3: str | None = None


@dataclass(frozen=True)
class SourceRef:
    document_id: str
    source_id: str
    reliability: str
    social: bool = False  # publication de réseau social : ne corrobore pas
    publisher_id: str | None = None


@dataclass
class Event:
    occurred_at: datetime
    country_iso3: str
    domain: Domain
    event_type: str
    impact: int  # 1-5, corrigeable par un analyste
    title: str
    summary: str
    language: str
    refs: list[SourceRef]
    secondary_countries: tuple[str, ...] = ()
    secondary_domains: tuple[Domain, ...] = ()
    evidence: tuple[str, ...] = ()
    probability_override: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def info_credibility(self) -> int:
        """Crédibilité Admiralty : 3 par défaut, 2 avec 2 sources médias distinctes, 1 avec 3 ou plus.
        Les publications de réseaux sociaux ne comptent pas : un même message relayé par deux flux n'est pas une corroboration."""
        publishers = {
            ("publisher", ref.publisher_id) if ref.publisher_id
            else ("source", ref.source_id)
            for ref in self.refs
            if not ref.social
        }
        n = len(publishers)
        return 1 if n >= 3 else 2 if n == 2 else 3

    @property
    def best_reliability(self) -> str:
        return min(r.reliability for r in self.refs)

    @property
    def admiralty(self) -> str:
        return f"{self.best_reliability}{self.info_credibility}"

    @property
    def probability(self) -> int:
        if self.probability_override is not None:
            return self.probability_override
        return _probability(self.best_reliability, self.info_credibility)

    @property
    def criticality(self) -> int:
        return self.probability * self.impact

    @property
    def level(self) -> str:
        return criticality_level(self.criticality)

    @property
    def source_count(self) -> int:
        return len({r.source_id for r in self.refs})
