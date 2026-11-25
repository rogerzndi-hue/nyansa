"""Conservation des documents collectés entre deux lancements (mode réel).

Seuls les champs bruts sont enregistrés (titre, texte, adresse, dates, source) : le pays, le thème et les événements sont
recalculés au chargement avec les règles à jour. Les textes de réseaux sociaux sont déjà anonymisés par le runner avant d'arriver ici.
Rétention : 30 jours (date de publication, à défaut date de collecte). Fichier en écriture atomique, droits 0600.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import RawDocument
from .persistence import atomic_write_json
from .pipeline import process_document
from .store_protocol import StoreProtocol

RETENTION = timedelta(days=30)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DocumentArchive:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, store: StoreProtocol, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        limit = now - RETENTION
        rows = [
            {"source_id": d.source_id, "title": d.title, "text": d.text, "url": d.url,
             "published_at": _iso(d.published_at), "fetched_at": _iso(d.fetched_at)}
            for d in store.documents() if (d.published_at or d.fetched_at) >= limit
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, rows)
        return len(rows)

    def load(self, store: StoreProtocol, now: datetime | None = None) -> int:
        """Recharge les documents r?cents ; une archive absente est un cas normal.

        Une archive illisible ou de structure incorrecte d?clenche une erreur.
        Toute erreur du pipeline restaure int?gralement le magasin.
        """
        now = now or datetime.now(timezone.utc)
        limit = now - RETENTION

        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0

        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError(
                f"Archive invalide : une liste JSON est attendue dans {self.path}"
            )

        docs: list[RawDocument] = []
        for r in rows:
            if not isinstance(r, dict):
                continue

            source_id = r.get("source_id")
            if not isinstance(source_id, str) or store.get_source(source_id) is None:
                continue

            fetched, published = _parse(r.get("fetched_at")), _parse(r.get("published_at"))
            title = r.get("title")
            if fetched is None or not isinstance(title, str) or not title or (published or fetched) < limit:
                continue

            docs.append(RawDocument(
                source_id=source_id,
                title=title,
                text=str(r.get("text") or ""),
                fetched_at=fetched,
                url=r.get("url") if isinstance(r.get("url"), str) else None,
                published_at=published,
            ))

        docs.sort(key=lambda d: d.published_at or d.fetched_at)

        with store.transaction():
            for d in docs:
                process_document(store, d)

        return len(docs)
