"""Échelle Admiralty (NATO) : fiabilité de la source (A-F) x crédibilité de l'information (1-6)."""

from __future__ import annotations

import re

RELIABILITY_LABELS_FR = {
    "A": "Fiable",
    "B": "Habituellement fiable",
    "C": "Assez fiable",
    "D": "Habituellement peu fiable",
    "E": "Non fiable",
    "F": "Non évaluable",
}

CREDIBILITY_LABELS_FR = {
    1: "Confirmée",
    2: "Probablement vraie",
    3: "Possiblement vraie",
    4: "Douteuse",
    5: "Improbable",
    6: "Non évaluable",
}

_RATING = re.compile(r"^([A-F])([1-6])$")

# Hypothèse de départ, à caler avec les analystes : probabilité (1-5) déduite de la crédibilité.
_PROBABILITY_BY_CREDIBILITY = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 2}


def parse_rating(rating: str) -> tuple[str, int]:
    """Découpe "B2" en ("B", 2). Lève ValueError si le code est invalide."""
    match = _RATING.match(rating.strip().upper())
    if not match:
        raise ValueError(f"Code Admiralty invalide : {rating!r} (attendu : lettre A-F + chiffre 1-6)")
    return match.group(1), int(match.group(2))


def probability(reliability: str, credibility: int) -> int:
    """Probabilité 1-5 d'un événement. Une source D ou E retire un point (minimum 1)."""
    if reliability not in RELIABILITY_LABELS_FR:
        raise ValueError(f"Fiabilité inconnue : {reliability!r}")
    if credibility not in CREDIBILITY_LABELS_FR:
        raise ValueError(f"Crédibilité inconnue : {credibility!r}")
    base = _PROBABILITY_BY_CREDIBILITY[credibility]
    if reliability in ("D", "E"):
        base -= 1
    return max(1, base)
