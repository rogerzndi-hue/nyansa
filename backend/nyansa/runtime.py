"""Initialisation commune des composants applicatifs NYANSA."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .alerts import AlertCenter, Rule, RuleError, load_rules
from .archive import DocumentArchive
from .deliverables.report import ReportStore
from .ingest import load_sources
from .social import (
    WatchBook,
    WatchError,
    load_demo_social,
    load_seed,
    sync_sources,
)
from .store import InMemoryStore


@dataclass
class NyansaRuntime:
    """Composants initialisés, prêts à être utilisés par un serveur."""

    store: InMemoryStore
    rules: list[Rule]
    watches: WatchBook
    alerts: AlertCenter
    reports: ReportStore
    archive: DocumentArchive | None
    lock: threading.RLock
    live_mode: bool
    active_rss_count: int
    restored_documents: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    wake_event: threading.Event = field(default_factory=threading.Event)
    collector_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _collector_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start_collector(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
    ) -> None:
        """Démarre au plus une fois le collecteur associé à ce runtime."""
        with self._collector_lock:
            if not self.live_mode:
                raise RuntimeError("Le collecteur ne peut pas démarrer en mode démonstration.")
            if self.collector_thread is not None:
                raise RuntimeError("Le collecteur de ce runtime a déjà été démarré.")

            thread = threading.Thread(
                target=target,
                args=args,
                name="nyansa-collector",
                daemon=True,
            )
            self.collector_thread = thread
            try:
                thread.start()
            except BaseException:
                self.collector_thread = None
                raise

    def wake_collector(self) -> None:
        """Demande un cycle de collecte anticipé."""
        if self.collector_thread is not None and self.collector_thread.is_alive():
            self.wake_event.set()

    def stop_collector(self, timeout: float | None = 30.0) -> None:
        """Arrête le collecteur et attend sa terminaison."""
        self.stop_event.set()
        self.wake_event.set()

        thread = self.collector_thread
        if thread is None:
            return

        if thread is threading.current_thread():
            raise RuntimeError("Le collecteur ne peut pas attendre sa propre terminaison.")

        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TimeoutError(
                "Le collecteur NYANSA ne s'est pas arrêté dans le délai imparti."
            )


def create_runtime(
    *,
    live_sources_path: str | Path | None,
    data_dir: str | Path,
    alert_rules_path: str | Path | None,
    watches_path: str | Path | None,
    demo_store_factory: Callable[[], InMemoryStore],
) -> NyansaRuntime:
    """Construit et restaure les composants, sans démarrer de serveur ni de thread."""

    live_mode = bool(live_sources_path)
    data_path = Path(data_dir)
    lock = threading.RLock()

    try:
        rules = load_rules(alert_rules_path)
    except RuleError as exc:
        raise SystemExit(f"Règles d'alerte invalides : {exc}") from exc

    try:
        watches = WatchBook(data_path / "watches.json" if live_mode else None)
        seeds = load_seed(watches_path) if live_mode else []
    except (WatchError, RuntimeError) as exc:
        raise SystemExit(f"Veilles invalides : {exc}") from exc

    for seed in seeds:
        try:
            watches.add(seed, actor="configuration")
        except WatchError:
            pass

    alerts = AlertCenter(
        rules,
        data_path / "alerts.json" if live_mode else None,
        external=live_mode,
    )

    archive: DocumentArchive | None = None
    restored_documents = 0
    active_rss_count = 0

    if live_mode:
        store = InMemoryStore()

        for source in load_sources(live_sources_path):
            store.add_source(source)

        if store.get_source("manual") is None:
            store.add_source(
                Source(
                    "manual",
                    "Saisie manuelle",
                    "manual",
                    default_reliability="C",
                )
            )

        sync_sources(store, watches)

        archive = DocumentArchive(data_path / "documents.json")
        restored_documents = archive.load(store)

        active_rss_count = sum(
            1 for source in store.sources()
            if source.active and source.kind == "rss"
        )
    else:
        store = demo_store_factory()
        load_demo_social(store, watches)
        alerts.collect(store)

    reports = ReportStore(
        data_path / "reports.json" if live_mode else None
    )

    return NyansaRuntime(
        store=store,
        rules=rules,
        watches=watches,
        alerts=alerts,
        reports=reports,
        archive=archive,
        lock=lock,
        live_mode=live_mode,
        active_rss_count=active_rss_count,
        restored_documents=restored_documents,
    )
