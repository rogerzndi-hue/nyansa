"""Extraction de pays dans un texte (gazetteer multilingue, longest-match).

Principes :
- les noms de pays pèsent 1.0, les villes 0.5 ; un mot dans le titre compte double ;
- le motif le plus long gagne ("Soudan du Sud" avant "Soudan") ;
- les expressions piégeuses ("golfe de Guinée", "bassin du Congo") sont consommées sans
  attribuer de pays ; "Congo" seul est ambigu (RDC ou Congo-Brazzaville) et signalé tel quel ;
- limite connue : l'arabe n'est reconnu que s'il est séparé par un espace
  (les préfixes collés comme « بـ » ne sont pas gérés).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .countries import COUNTRIES
from .demonyms import WEIGHT as DEMONYM_WEIGHT
from .demonyms import forms as demonym_forms
from .text import fold

_STOP = "!"
_AMBIGUOUS = "?"

_STOP_PHRASES = (
    "golfe de Guinée", "Gulf of Guinea", "golfo da Guiné", "golfo de Guinea",
    "Nouvelle-Guinée", "Papua New Guinea", "Papouasie-Nouvelle-Guinée",
    "bassin du Congo", "Congo Basin", "fleuve Congo", "Congo River",
    "fleuve Niger", "Niger River", "river Niger",
    "lac Tchad", "Lake Chad", "lago Chade",
    "خليج غينيا",
    "AES", "Alliance des États du Sahel", "Alliance of Sahel States",  # confédération Mali/Burkina/Niger
)
_AMBIGUOUS_TERMS = ("Congo", "الكونغو")

# Pays hors Afrique les plus fréquents dans l'actualité (FR/EN). Sert uniquement à repérer un titre qui parle d'un autre
# pays : liste volontairement partielle, jamais utilisée pour rattacher un événement. « US », « Georgia » : trop ambigus.
_FOREIGN_NAMES = (
    "Bangladesh", "India", "Inde", "Pakistan", "China", "Chine", "Russia", "Russie", "Ukraine", "United States", "États-Unis",
    "Etats-Unis", "United Kingdom", "Britain", "Royaume-Uni", "France", "Germany", "Allemagne", "Italy", "Italie", "Spain",
    "Espagne", "Portugal", "Belgium", "Belgique", "Netherlands", "Pays-Bas", "Switzerland", "Suisse", "Poland", "Pologne",
    "Greece", "Grèce", "Sweden", "Suède", "Norway", "Norvège", "Denmark", "Danemark", "Ireland", "Irlande", "Austria",
    "Autriche", "Romania", "Roumanie", "Hungary", "Hongrie", "Belarus", "Biélorussie", "Turkey", "Turquie", "Iran", "Iraq",
    "Irak", "Israel", "Israël", "Gaza", "Palestine", "Syria", "Syrie", "Lebanon", "Liban", "Saudi Arabia", "Arabie saoudite",
    "Qatar", "Yemen", "Yémen", "Afghanistan", "Japan", "Japon", "South Korea", "Corée du Sud", "North Korea", "Corée du Nord",
    "Indonesia", "Indonésie", "Philippines", "Vietnam", "Thailand", "Thaïlande", "Myanmar", "Birmanie", "Nepal", "Népal",
    "Sri Lanka", "Malaysia", "Malaisie", "Singapore", "Singapour", "Cambodia", "Cambodge", "Taiwan", "Taïwan", "Kazakhstan",
    "Brazil", "Brésil", "Argentina", "Argentine", "Mexico", "Mexique", "Canada", "Venezuela", "Colombia", "Colombie", "Cuba",
    "Haiti", "Haïti", "Peru", "Pérou", "Chile", "Australia", "Australie", "Kuwait", "Koweït", "Jordan", "Jordanie",
)
# Titre qui parle d'un pays hors Afrique, sans pays africain : le pays africain cité seulement dans le corps du texte doit
# l'emporter avec assez de poids (3 mentions de nom de pays) pour rattacher l'article ; sinon « à qualifier ».
FOREIGN_MIN_BODY_WEIGHT = 3.0


@dataclass(frozen=True)
class CountryHit:
    iso3: str
    mentions: int
    weight: float
    first_pos: int


@dataclass(frozen=True)
class Extraction:
    hits: tuple[CountryHit, ...]
    ambiguous: tuple[str, ...]
    title_isos: tuple[str, ...] = ()  # pays africains reconnus dans le titre
    foreign_title: tuple[str, ...] = ()  # pays hors Afrique reconnus dans le titre

    @property
    def primary(self) -> str | None:
        return self.hits[0].iso3 if self.hits else None

    @property
    def foreign_focus(self) -> bool:
        """Le titre parle d'un autre pays et le pays africain ne ressort que faiblement du corps du texte."""
        return bool(self.foreign_title and not self.title_isos and self.hits and self.hits[0].weight < FOREIGN_MIN_BODY_WEIGHT)

    def secondary(self, limit: int = 5) -> tuple[str, ...]:
        return tuple(h.iso3 for h in self.hits[1 : 1 + limit])


def _build() -> tuple[re.Pattern[str], dict[str, tuple[str, float]]]:
    terms: dict[str, tuple[str, float]] = {}

    def add(term: str, iso: str, weight: float) -> None:
        key = fold(term)
        if not key:
            return
        current = terms.get(key)
        if current is not None and current[0] != iso:
            raise ValueError(f"Collision de gazetteer : {term!r} -> {current[0]} et {iso}")
        terms[key] = (iso, max(weight, current[1]) if current else weight)

    for c in COUNTRIES:
        for name in (c.name_fr, c.name_en, c.name_pt, c.name_ar, *c.aliases):
            add(name, c.iso3, 1.0)
        for city in c.cities:
            add(city, c.iso3, 0.5)
        for demonym in demonym_forms(c.iso3):
            add(demonym, c.iso3, DEMONYM_WEIGHT)
    for phrase in _STOP_PHRASES:
        add(phrase, _STOP, 0.0)
    for term in _AMBIGUOUS_TERMS:
        add(term, _AMBIGUOUS, 0.0)

    keys = sorted(terms, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")
    return pattern, terms


_PATTERN, _TERMS = _build()
_FOREIGN_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted((fold(n) for n in _FOREIGN_NAMES), key=len, reverse=True)) + r")(?!\w)")


def extract_countries(title: str, body: str = "") -> Extraction:
    """Retourne les pays mentionnés, du plus probable (pays principal) au moins probable."""
    acc: dict[str, list[float]] = {}  # iso -> [mentions, weight, first_pos]
    ambiguous: set[str] = set()

    def scan(text: str, multiplier: float, offset: int) -> None:
        for m in _PATTERN.finditer(text):
            iso, weight = _TERMS[m.group(0)]
            if iso == _STOP:
                continue
            if iso == _AMBIGUOUS:
                ambiguous.add(m.group(0))
                continue
            entry = acc.setdefault(iso, [0, 0.0, offset + m.start()])
            entry[0] += 1
            entry[1] += weight * multiplier

    folded_title = fold(title)
    scan(folded_title, 2.0, 0)
    title_isos = tuple(acc)  # pays africains vus dans le titre, avant le corps du texte
    scan(fold(body), 1.0, len(folded_title) + 1)
    # pays hors Afrique cités dans le titre (le gazetteer africain et la liste étrangère ne se recoupent pas)
    foreign = tuple(dict.fromkeys(m.group(0) for m in _FOREIGN_PATTERN.finditer(folded_title)))

    hits = sorted(
        (CountryHit(iso, int(v[0]), round(v[1], 2), int(v[2])) for iso, v in acc.items()),
        key=lambda h: (-h.weight, h.first_pos),
    )
    return Extraction(tuple(hits), tuple(sorted(ambiguous)), title_isos, foreign)
