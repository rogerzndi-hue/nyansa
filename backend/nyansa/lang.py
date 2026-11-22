"""Détection de langue légère (FR, EN, PT, AR) sans dépendance externe.

Heuristique : proportion de caractères arabes, sinon comptage de mots vides
discriminants. Retourne "und" (indéterminée) en cas de doute : une langue
inconnue vaut mieux qu'une langue fausse pour la mesure du biais de couverture.
"""

from __future__ import annotations

from .text import fold

# Mots vides sous forme "foldée" (sans accents) ; les mots partagés entre langues sont exclus.
_STOPWORDS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        "le les des du une est dans pour sur que qui aux avec cette ses ont sont ete apres pas "
        "par au un et la en il elle ce".split()
    ),
    "en": frozenset(
        "the of and to is for that with from are was this it be has have its by on at in an".split()
    ),
    "pt": frozenset(
        "os das dos da uma nao para com nas foi foram sao como pelo pela tambem ao aos em um seu sua".split()
    ),
}


def detect_language(text: str) -> tuple[str, float]:
    """Retourne (code langue, confiance 0-1). Code "und" si indéterminé."""
    sample = text[:3000]
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return "und", 0.0

    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ")
    ratio = arabic / len(letters)
    if ratio > 0.3:
        return "ar", round(ratio, 2)

    words = fold(sample).split()
    hits = {lang: sum(1 for w in words if w in stop) for lang, stop in _STOPWORDS.items()}
    total = sum(hits.values())
    best = max(hits, key=lambda k: hits[k])
    if total == 0 or hits[best] < 3:
        return "und", 0.0
    confidence = hits[best] / total
    if confidence < 0.5:
        return "und", round(confidence, 2)
    return best, round(confidence, 2)
