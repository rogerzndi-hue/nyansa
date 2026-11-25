
"""Contrat commun des magasins de données NYANSA.

InMemoryStore et PostgresStore doivent respecter cette interface.
Ce module définit uniquement les signatures, pas la logique métier.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import Event, RawDocument, Source, SourceRef
from .taxonomy import Domain, Region


class AuditEntryProtocol(Protocol):
    """Structure minimale d'une entrée du journal d'audit."""

    at: datetime
    actor: str
    action: str
    object_id: str | None


@runtime_checkable
class StoreProtocol(Protocol):
    """Interface commune des magasins de données NYANSA."""

    @property
    def version(self) -> int:
        """Version logique utilisée pour invalider les caches."""
        ...

    def transaction(self) -> AbstractContextManager[StoreProtocol]:
        """Garantit le rollback complet si le bloc échoue."""
        ...

    # Sources

    def add_source(self, source: Source) -> None:
        ...

    def get_source(self, source_id: str) -> Source | None:
        ...

    def sources(self) -> list[Source]:
        ...

    # Documents

    def add_document(self, doc: RawDocument) -> bool:
        """Retourne False si le document existe déjà."""
        ...

    def get_document(self, document_id: str) -> RawDocument | None:
        ...

    def documents(self) -> list[RawDocument]:
        ...

    def unrouted(self) -> list[RawDocument]:
        ...

    # Événements

    def register_event(self, candidate: Event) -> tuple[Event, bool]:
        """Retourne l'événement retenu et indique s'il a été créé."""
        ...

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
        ...

    # Audit

    def audit(
        self,
        actor: str,
        action: str,
        object_id: str | None = None,
    ) -> None:
        ...

    def audit_log(self) -> list[AuditEntryProtocol]:
        ...

    # Références documentaires

    def source_ref(self, doc: RawDocument) -> SourceRef:
        ...
