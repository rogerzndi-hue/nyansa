"""Taxonomie de base : domaines, régions et niveaux de criticité."""

from __future__ import annotations

from enum import Enum


class Domain(str, Enum):
    GEO = "GEO"
    ECO = "ECO"
    SEC = "SEC"
    SOC = "SOC"
    GOV = "GOV"
    CYB = "CYB"
    HUM = "HUM"


DOMAIN_LABELS_FR: dict[Domain, str] = {
    Domain.GEO: "Géopolitique et diplomatie",
    Domain.ECO: "Économie et finance",
    Domain.SEC: "Sécurité",
    Domain.SOC: "Mouvements sociaux",
    Domain.GOV: "Élections et gouvernance",
    Domain.CYB: "Cyber",
    Domain.HUM: "Humanitaire, santé et climat",
}


class Region(str, Enum):
    NORTH = "NORTH"
    WEST = "WEST"
    CENTRAL = "CENTRAL"
    EAST = "EAST"
    SOUTHERN = "SOUTHERN"


# Découpage de l'Union africaine (la Mauritanie est en Afrique du Nord).
REGION_LABELS_FR: dict[Region, str] = {
    Region.NORTH: "Afrique du Nord",
    Region.WEST: "Afrique de l'Ouest",
    Region.CENTRAL: "Afrique centrale",
    Region.EAST: "Afrique de l'Est",
    Region.SOUTHERN: "Afrique australe",
}

LEVEL_LABELS_FR = {
    "low": "Faible",
    "moderate": "Modéré",
    "high": "Élevé",
    "critical": "Critique",
}


def criticality_level(score: int) -> str:
    """Niveau d'un événement : score = probabilité (1-5) x impact (1-5)."""
    if score >= 15:
        return "critical"
    if score >= 10:
        return "high"
    if score >= 5:
        return "moderate"
    return "low"


def risk_level(score: float) -> str:
    """Niveau d'un pays : score sur 100."""
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 35:
        return "moderate"
    return "low"
