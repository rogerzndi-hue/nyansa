"""Normalisation de texte multilingue (FR, EN, PT, AR) pour la recherche de motifs."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ـ": ""})
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Minuscules, sans accents ni diacritiques arabes, ponctuation remplacée par des espaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.translate(_AR_MAP).casefold()
    return _SPACES.sub(" ", _PUNCT.sub(" ", lowered)).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(raw: str) -> str:
    """Retire les balises HTML (et scripts/styles) et condense les espaces."""
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return _SPACES.sub(" ", "".join(parser.parts)).strip()
