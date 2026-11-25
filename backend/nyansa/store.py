"""Stockage en mémoire (développement et tests).

L'interface publique est celle que reprendra l'implémentation PostgreSQL/PostGIS
(db/schema.sql) : add_source, add_document, register_event, events, documents, audit.
Les données sont perdues à l'arrêt du processus.
"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .countries import COUNTRIES_BY_ISO
from .models import Event, RawDocument, Source, SourceRef
from .taxonomy import Domain, Region
from .text import fold

CLUSTER_WINDOW = timedelta(hours=48)
CLUSTER_JACCARD = 0.5


# Ordinaux FR -> EN : « huitième cas » et « eighth case » désignent le même fait.
_ORDINALS = {"premier": "first", "deuxieme": "second", "troisieme": "third", "quatrieme": "fourth", "cinquieme": "fifth",
             "sixieme": "sixth", "septieme": "seventh", "huitieme": "eighth", "neuvieme": "ninth", "dixieme": "tenth", "cas": "case",
             # synonymes courants des titres de presse (mêmes faits, mots différents)
             "crimes": "abuses", "abus": "abuses", "atrocities": "abuses", "exactions": "abuses", "violations": "abuses",
             "verdict": "ruling", "rule": "ruling", "sentence": "ruling",
             "civiles": "civilians", "muertos": "killed", "muertes": "killed", "islamico": "islamic", "estado": "state", "civils": "civilians", "civil": "civilians", "tues": "killed", "tue": "killed", "tues.": "killed"}
_FILLER = frozenset({"dans", "plus", "than", "more", "with", "from", "that", "this", "over", "apres", "pour", "avec", "sont",
                     "about", "after", "says", "said", "selon", "contre", "against"})
MIN_SHARED_DISTINCTIVE = 3
MIN_OVERLAP = 0.35  # trois mots distinctifs communs sur un titre court de 8 mots (même pays, même domaine, 48 h)


def _title_tokens(title: str) -> frozenset[str]:
    title = re.sub(r"\[[^\]]*\]", " ", title)  # étiquettes de flux « [Africanews Today] » : pas des mots du fait
    words = (_ORDINALS.get(w, w) for w in fold(title).split())
    return frozenset(w for w in words if (len(w) >= 4 and w not in _FILLER) or w.isdigit())


def _country_words(iso3: str) -> frozenset[str]:
    c = COUNTRIES_BY_ISO.get(iso3)
    if c is None:
        return frozenset()
    words = set()
    for name in (c.name_fr, c.name_en, c.name_pt, *c.aliases):
        words.update(fold(name).split())
    words.update({fold(c.name_en) + "n", fold(c.name_en) + "an"})  # kenyan, nigerian... (approximatif)
    return frozenset(words)


def _same_story(a: frozenset[str], b: frozenset[str], ignore: frozenset[str]) -> bool:
    """Titres proches : Jaccard >= 0.5, ou au moins 3 mots distinctifs communs couvrant la moitié du plus court (hors nom du pays)."""
    if _jaccard(a, b) >= CLUSTER_JACCARD:
        return True
    a2, b2 = a - ignore, b - ignore
    shared = a2 & b2
    if any(w.isdigit() and len(w) >= 3 for w in shared) and len(shared) >= 2:  # même chiffre précis (« 191 ») + un mot commun
        return True
    return len(shared) >= MIN_SHARED_DISTINCTIVE and len(shared) / min(len(a2), len(b2)) >= MIN_OVERLAP


def _same_domain(a: Domain, b: Domain) -> bool:
    """Sécurité et gouvernance se recoupent (exactions de l'armée : droits humains ou attaque ?) : un même fait ne doit pas se scinder."""
    return a == b or {a, b} == {Domain.SEC, Domain.GOV}


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    action: str
    object_id: str | None


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[str, Source] = {}
        self._documents: list[RawDocument] = []
        self._doc_keys: set[tuple[str, str]] = set()
        self._doc_by_id: dict[str, RawDocument] = {}
        self._extra_tokens: dict[str, set[frozenset[str]]] = {}
        self._version = 0  # incrémenté à chaque écriture : sert à invalider les caches
        self._events: dict[str, Event] = {}
        self._by_country: dict[str, list[Event]] = {}
        self._audit: list[AuditEntry] = []

    
    @contextmanager
    def transaction(self):
        """Restaure l'état interne du magasin si une opération échoue."""
        with self._lock:
            snapshot = copy.deepcopy((
                self._sources,
                self._documents,
                self._doc_keys,
                self._doc_by_id,
                self._extra_tokens,
                self._version,
                self._events,
                self._by_country,
                self._audit,
            ))
            try:
                yield self
            except BaseException:
                (
                    self._sources,
                    self._documents,
                    self._doc_keys,
                    self._doc_by_id,
                    self._extra_tokens,
                    self._version,
                    self._events,
                    self._by_country,
                    self._audit,
                ) = snapshot
                raise

    @property
    def version(self) -> int:
        return self._version

    # --- sources -----------------------------------------------------------
    def add_source(self, source: Source) -> None:
        with self._lock:
            self._sources[source.id] = source

    def get_source(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    def sources(self) -> list[Source]:
        return list(self._sources.values())

    # --- documents ---------------------------------------------------------
    def add_document(self, doc: RawDocument) -> bool:
        """Ajoute un document ; retourne False s'il existe déjà (même source, même empreinte)."""
        if doc.content_hash is None:
            raise ValueError("content_hash requis")
        key = (doc.source_id, doc.content_hash)
        with self._lock:
            if key in self._doc_keys:
                return False
            self._doc_keys.add(key)
            self._documents.append(doc)
            self._doc_by_id[doc.id] = doc
            self._version += 1
            return True

    def get_document(self, document_id: str) -> RawDocument | None:
        return self._doc_by_id.get(document_id)

    def documents(self) -> list[RawDocument]:
        return list(self._documents)

    def unrouted(self) -> list[RawDocument]:
        return [d for d in self._documents if d.routing_status == "unrouted"]

    # --- événements --------------------------------------------------------
    def _cluster_tokens(self, event: Event) -> list[frozenset[str]]:
        """Titre de l'événement + titres des variantes déjà fusionnées (évite de comparer à un seul titre de référence)."""
        return [_title_tokens(event.title), *self._extra_tokens.get(event.id, ())]

    def register_event(self, candidate: Event) -> tuple[Event, bool]:
        """Ajoute l'événement, ou le fusionne avec un événement équivalent déjà connu.

        Équivalent = même pays, même domaine, moins de 48 h d'écart, titres proches (Jaccard >= 0.5).
        Une fusion ajoute une référence de source, ce qui peut relever la crédibilité.
        Retourne (événement, créé).
        """
        tokens = _title_tokens(candidate.title)
        ignore = _country_words(candidate.country_iso3)
        with self._lock:
            for existing in self._by_country.get(candidate.country_iso3, []):
                if (
                    _same_domain(existing.domain, candidate.domain)
                    and abs(existing.occurred_at - candidate.occurred_at) <= CLUSTER_WINDOW
                    and any(_same_story(tokens, t, ignore) for t in self._cluster_tokens(existing))
                ):
                    self._extra_tokens.setdefault(existing.id, set()).add(tokens)
                    known = {r.document_id for r in existing.refs}
                    existing.refs.extend(r for r in candidate.refs if r.document_id not in known)
                    existing.impact = max(existing.impact, candidate.impact)
                    self._version += 1
                    return existing, False
            self._events[candidate.id] = candidate
            self._by_country.setdefault(candidate.country_iso3, []).append(candidate)
            self._version += 1
            return candidate, True

    def events(
        self,
        *,
        country: str | None = None,
        region: Region | None = None,
        domain: Domain | None = None,
        min_criticality: int | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        with self._lock:
            pool = list(self._by_country.get(country, [])) if country else list(self._events.values())
        out = []
        for e in pool:
            if region and COUNTRIES_BY_ISO[e.country_iso3].region != region:
                continue
            if domain and e.domain != domain:
                continue
            if min_criticality and e.criticality < min_criticality:
                continue
            if since and e.occurred_at < since:
                continue
            out.append(e)
        out.sort(key=lambda e: e.occurred_at, reverse=True)
        return out[:limit] if limit else out

    # --- audit -------------------------------------------------------------
    def audit(self, actor: str, action: str, object_id: str | None = None) -> None:
        with self._lock:
            self._audit.append(AuditEntry(datetime.now(timezone.utc), actor, action, object_id))

    def audit_log(self) -> list[AuditEntry]:
        return list(self._audit)

    def source_ref(self, doc: RawDocument) -> SourceRef:
        source = self._sources[doc.source_id]
        return SourceRef(doc.id, source.id, source.default_reliability, social=source.channel == "social", publisher_id=source.publisher_id)
