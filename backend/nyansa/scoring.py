"""Score de risque pays par règles pondérées, explicable.

Chaîne de calcul (paramètres initiaux, à caler avec les analystes) :
1. chaque événement pèse sa criticité (probabilité x impact, 1-25) x une décroissance
   exponentielle (demi-vie 14 jours) ;
2. par domaine : score = 100 x (1 - exp(-somme / échelle)), qui sature vers 100 ;
3. score global = 70 % du domaine le plus chaud + 30 % de la moyenne pondérée des domaines.

Explicabilité : la contribution d'un événement est la baisse de score global si on le retirait
(calcul « leave-one-out »). Ces contributions ne s'additionnent pas exactement au score,
car la saturation est non linéaire ; elles servent à classer ce qui pèse le plus.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import Event
from .taxonomy import Domain, risk_level

DEFAULT_DOMAIN_WEIGHTS: dict[Domain, float] = {
    Domain.SEC: 1.0,
    Domain.GOV: 0.8,
    Domain.SOC: 0.7,
    Domain.ECO: 0.7,
    Domain.HUM: 0.7,
    Domain.GEO: 0.5,
    Domain.CYB: 0.5,
}


@dataclass(frozen=True)
class ScoringConfig:
    window_days: int = 30
    half_life_days: float = 14.0
    scale: float = 30.0
    max_share: float = 0.7
    domain_weights: Mapping[Domain, float] = field(default_factory=lambda: dict(DEFAULT_DOMAIN_WEIGHTS))
    explain_top: int = 10
    explain_pool: int = 50  # nombre d'événements candidats au calcul des contributions


@dataclass(frozen=True)
class Contribution:
    event_id: str
    title: str
    domain: Domain
    criticality: int
    points: float


@dataclass(frozen=True)
class CountryRisk:
    iso3: str
    as_of: datetime
    score: float
    level: str
    trend_30d: float
    domain_scores: dict[str, float]
    event_count: int
    source_count: int
    language_count: int
    confidence: str  # low | medium | high
    notes: tuple[str, ...]
    contributions: tuple[Contribution, ...]


def _decay(age_days: float, half_life: float) -> float:
    return 0.5 ** (max(age_days, 0.0) / half_life)


def _in_window(events: Iterable[Event], as_of: datetime, cfg: ScoringConfig) -> list[tuple[Event, float]]:
    """Événements de la fenêtre avec leur poids (criticité x décroissance). Ignore le futur."""
    out = []
    for e in events:
        age = (as_of - e.occurred_at).total_seconds() / 86400
        if 0 <= age <= cfg.window_days:
            out.append((e, e.criticality * _decay(age, cfg.half_life_days)))
    return out


def _score_from(weighted: Iterable[tuple[Event, float]], cfg: ScoringConfig) -> tuple[float, dict[Domain, float]]:
    raw = {d: 0.0 for d in Domain}
    for e, w in weighted:
        raw[e.domain] += w
    domain_scores = {d: 100 * (1 - math.exp(-r / cfg.scale)) for d, r in raw.items()}
    total_w = sum(cfg.domain_weights[d] for d in Domain)
    mean = sum(cfg.domain_weights[d] * domain_scores[d] for d in Domain) / total_w
    score = cfg.max_share * max(domain_scores.values()) + (1 - cfg.max_share) * mean
    return score, domain_scores


def score_events(events: Iterable[Event], as_of: datetime, cfg: ScoringConfig | None = None) -> float:
    cfg = cfg or ScoringConfig()
    return _score_from(_in_window(events, as_of, cfg), cfg)[0]


def compute_country_risk(
    events: Iterable[Event],
    iso3: str,
    as_of: datetime,
    cfg: ScoringConfig | None = None,
    languages: Iterable[str] = (),
) -> CountryRisk:
    """Score d'un pays à la date `as_of`, avec tendance sur 30 jours et explication."""
    cfg = cfg or ScoringConfig()
    events = list(events)
    weighted = _in_window(events, as_of, cfg)
    score, domain_scores = _score_from(weighted, cfg)
    previous = score_events(events, as_of - timedelta(days=30), cfg)

    sources = {r.source_id for e, _ in weighted for r in e.refs}
    publishers = {
        ("publisher", r.publisher_id) if r.publisher_id
        else ("source", r.source_id)
        for e, _ in weighted
        for r in e.refs
        if not r.social
    }
    langs = {lang for lang in languages if lang and lang != "und"}
    n = len(weighted)
    if n == 0:
        confidence = "low"
    elif n < 3 or len(publishers) < 2:
        confidence = "low"
    elif n >= 10 and len(publishers) >= 4:
        confidence = "high"
    else:
        confidence = "medium"

    notes: list[str] = []
    if n == 0:
        notes.append("Aucun événement sur la fenêtre : l'absence de données n'est pas l'absence de risque.")
    elif len(publishers) < 2:
        notes.append("Une seule source distincte : score fragile, risque de sous-déclaration.")
    if n and len(langs) <= 1:
        notes.append("Une seule langue de source : biais de couverture possible.")

    pool = sorted(weighted, key=lambda t: t[1], reverse=True)[: cfg.explain_pool]
    contributions = []
    for e, _ in pool:
        without = score_events((x for x, _ in weighted if x is not e), as_of, cfg)
        contributions.append(
            Contribution(e.id, e.title, e.domain, e.criticality, round(score - without, 2))
        )
    contributions.sort(key=lambda c: c.points, reverse=True)

    return CountryRisk(
        iso3=iso3,
        as_of=as_of,
        score=round(score, 1),
        level=risk_level(score),
        trend_30d=round(score - previous, 1),
        domain_scores={d.value: round(s, 1) for d, s in domain_scores.items()},
        event_count=n,
        source_count=len(sources),
        language_count=len(langs),
        confidence=confidence,
        notes=tuple(notes),
        contributions=tuple(contributions[: cfg.explain_top]),
    )
