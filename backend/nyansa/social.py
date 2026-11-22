"""Surveillance des réseaux sociaux : veilles (hashtag, compte public, mot-clé) et vue de suivi des publications.

Ce qui est réellement possible sans clé payante : lire des flux RSS publics (hashtags et comptes Mastodon, comptes Bluesky,
chaînes YouTube, subreddits Reddit). Une veille « hashtag », « compte » ou « chaîne » devient donc une source de collecte
(canal « social ») ; une veille « mot-clé » ne collecte rien : elle filtre les publications déjà collectées. Les hôtes de
Bluesky, YouTube et Reddit sont fixes (seul l'identifiant validé entre dans l'adresse) : pas de requête vers une adresse choisie. Les publications passent par le pipeline commun
(pays, thème, événement) puis sont qualifiées ici :
- corroborated : l'événement est aussi rapporté par au moins une source MÉDIA (les réseaux ne se corroborent pas entre eux) ;
- unconfirmed  : événement issu de publications sociales seules : NON CONFIRMÉ ;
- to_qualify   : pays ou thème non reconnu, aucun événement créé.
Limites : la corroboration repose sur le regroupement des événements (même pays, même thème, titres proches, 48 h) ;
une dépêche formulée très différemment ne sera pas rapprochée. Les contraintes pays/thèmes d'une veille filtrent
l'affichage, pas la collecte. Pas de collecte pour X (API payante), Facebook, Instagram, TikTok, WhatsApp (pas d'accès public autorisé) ni Telegram
(conditions d'utilisation de la lecture de canaux publics non établies).
Vie privée : @pseudonymes et e-mails sont retirés avant stockage (runner.anonymize) ; seuls des comptes PUBLICS
d'institutions ou de médias devraient être suivis.
"""

from __future__ import annotations

import hashlib
import json
import ipaddress
import re
import socket
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from .countries import COUNTRIES_BY_ISO
from .models import Source
from .persistence import atomic_write_json
from .services import safe_url
from .store import InMemoryStore
from .taxonomy import DOMAIN_LABELS_FR, LEVEL_LABELS_FR, Domain
from .text import fold

KINDS = ("hashtag", "account", "channel", "keyword")
COLLECTABLE = ("hashtag", "account", "channel")
# plateforme -> types de veille possibles ; « keyword » ne dépend d'aucune plateforme
PLATFORMS = {"mastodon": ("hashtag", "account"), "bluesky": ("account",), "youtube": ("channel",), "reddit": ("channel",)}
PLATFORM_LABELS = {"mastodon": "Mastodon", "bluesky": "Bluesky", "youtube": "YouTube", "reddit": "Reddit"}
_BSKY_HANDLE = re.compile(r"^(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
_BSKY_DID = re.compile(r"^did:(?:plc|web):[A-Za-z0-9._:-]{1,100}$")
_YT_CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}")
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{3,21}$")
DEFAULT_INSTANCE = "mastodon.social"
WATCH_ROLES = frozenset({"analyst", "manager", "admin"})
MAX_WATCHES = 100
STATUS_LABELS_FR = {
    "corroborated": "Corroboré par au moins une source média",
    "unconfirmed": "Non confirmé : publications sociales seules",
    "to_qualify": "À qualifier : pays ou thème non reconnu",
}
_HOST = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet", ".arpa")
_USER = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class WatchError(ValueError):
    pass


@dataclass(frozen=True)
class Watch:
    id: str
    kind: str
    value: str
    instance: str
    name: str
    countries: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    active: bool = True
    created_by: str = ""
    created_at: str = ""
    platform: str = "mastodon"  # « » pour un mot-clé

    @property
    def collectable(self) -> bool:
        return self.kind in COLLECTABLE

    @property
    def source_id(self) -> str:
        return f"watch-{self.id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(countries=list(self.countries), categories=list(self.categories), collectable=self.collectable,
                 platform_label=PLATFORM_LABELS.get(self.platform, ""),
                 source_id=self.source_id if self.collectable else None)
        return d


def _instance(raw: object) -> str:
    host = str(raw or DEFAULT_INSTANCE).strip().lower()
    if not _HOST.match(host) or host.endswith(_PRIVATE_SUFFIXES) or host.replace(".", "").isdigit():
        raise WatchError(f"Instance invalide : {host!r} (nom de domaine public attendu, pas d'adresse IP ni de nom local)")
    return host


def validate_watch(raw: object, *, actor: str = "", now: datetime | None = None) -> Watch:
    if not isinstance(raw, dict):
        raise WatchError("Objet attendu")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise WatchError(f"kind invalide : {kind!r} ; attendu {', '.join(KINDS)}")
    value = " ".join(str(raw.get("value") or "").split())
    instance = DEFAULT_INSTANCE
    platform = str(raw.get("platform") or "mastodon").lower() if kind != "keyword" else ""
    if kind != "keyword":
        if platform not in PLATFORMS:
            raise WatchError(f"Plateforme invalide : {platform!r} ; attendu {', '.join(PLATFORMS)}")
        if kind not in PLATFORMS[platform]:
            raise WatchError(f"{PLATFORM_LABELS[platform]} : type possible : {', '.join(PLATFORMS[platform])}")
    if kind == "channel" and platform == "youtube":
        found = _YT_CHANNEL.search(value)  # accepte l'identifiant seul ou une adresse youtube.com/channel/UC…
        if not found:
            raise WatchError("Chaîne YouTube : identifiant de 24 caractères commençant par UC (adresse youtube.com/channel/UC…)")
        value, instance = found.group(0), ""
    elif kind == "channel":  # reddit
        value = value.removeprefix("/").removeprefix("r/")
        if not _SUBREDDIT.match(value):
            raise WatchError("Subreddit : 3 à 21 lettres, chiffres ou _ (ex. Nigeria)")
        instance = ""
    elif kind == "account" and platform == "bluesky":
        value = value.lstrip("@")
        if not (_BSKY_DID.match(value) or _BSKY_HANDLE.match(value)):
            raise WatchError("Compte Bluesky : nom complet (ex. media.bsky.social) ou identifiant did:plc:…")
        instance = ""
    elif kind == "hashtag":
        value = value.lstrip("#")
        if not 2 <= len(value) <= 50 or not value.replace("_", "").isalnum():
            raise WatchError("Hashtag : 2 à 50 lettres, chiffres ou _ (sans espace)")
        instance = _instance(raw.get("instance"))
    elif kind == "account":
        value = value.lstrip("@")
        if "@" in value:  # utilisateur@instance
            value, host = value.split("@", 1)
            instance = _instance(host)
        else:
            instance = _instance(raw.get("instance"))
        if not _USER.match(value):
            raise WatchError("Compte : 1 à 40 caractères (lettres, chiffres, _ . -)")
    else:
        if not 2 <= len(value) <= 60 or any(ord(c) < 32 for c in value):
            raise WatchError("Mot-clé : 2 à 60 caractères")
        instance = ""
    countries = tuple(dict.fromkeys(str(c).upper() for c in raw.get("countries") or []))
    for c in countries:
        if c not in COUNTRIES_BY_ISO:
            raise WatchError(f"Pays inconnu : {c!r} (code ISO3 attendu)")
    categories = tuple(dict.fromkeys(raw.get("categories") or []))
    for d in categories:
        if d not in {x.value for x in Domain}:
            raise WatchError(f"Catégorie inconnue : {d!r}")
    ident = f"{kind}|{value.casefold()}|{instance}" if platform in ("", "mastodon") else f"{platform}|{kind}|{value.casefold()}"
    wid = hashlib.sha1(ident.encode()).hexdigest()[:10]  # noqa: S324 : identifiant stable (inchangé pour les veilles Mastodon existantes)
    label = {"hashtag": f"#{value}", "account": f"@{value}@{instance}" if platform == "mastodon" else f"@{value} (Bluesky)",
             "channel": f"YouTube {value}" if platform == "youtube" else f"r/{value}", "keyword": f"« {value} »"}[kind]
    name = " ".join(str(raw.get("name") or label).split())[:80] or label
    return Watch(id=wid, kind=kind, value=value, instance=instance, name=name, countries=countries, categories=categories,
                 active=bool(raw.get("active", True)), created_by=actor[:80], platform=platform,
                 created_at=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds"))


def watch_source(w: Watch) -> Source | None:
    """Source de collecte d'une veille hashtag, compte ou chaîne (flux RSS public) ; None pour un mot-clé."""
    if w.kind == "channel" and w.platform == "youtube":
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={w.value}"
    elif w.kind == "channel":
        url = f"https://www.reddit.com/r/{quote(w.value)}/.rss"
    elif w.kind == "account" and w.platform == "bluesky":
        url = f"https://bsky.app/profile/{quote(w.value, safe=':')}/rss"
    elif w.kind == "hashtag":
        url = f"https://{w.instance}/tags/{quote(w.value)}.rss"
    elif w.kind == "account":
        url = f"https://{w.instance}/@{quote(w.value)}.rss"
    else:
        return None
    where = "de l'instance" if w.platform == "mastodon" else f"de {PLATFORM_LABELS.get(w.platform, w.platform)}"
    return Source(id=w.source_id, name=f"Veille : {w.name}", kind="rss", default_reliability="E", url=url, active=w.active, channel="social",
                  license_note=f"Flux RSS public ; CGU {where} à lire avant exploitation durable. NON VÉRIFIÉ en réel depuis l'environnement de développement. "
                               "Fiabilité E : contenu individuel non vérifié. @pseudonymes et e-mails retirés avant stockage, texte tronqué.")


class WatchBook:
    """Veilles de l'analyste ; enregistrées dans un fichier JSON (écriture atomique) si un chemin est donné."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        self._watches: dict[str, Watch] = {}
        if self._path and self._path.exists():
            try:
                for raw in json.loads(self._path.read_text(encoding="utf-8")):
                    w = validate_watch(raw, actor=raw.get("created_by", ""))
                    self._watches[w.id] = Watch(**{**asdict(w), "created_at": raw.get("created_at", ""), "active": bool(raw.get("active", True))})
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(f"Fichier des veilles illisible ({self._path}) : {exc}. Corrigez ou déplacez-le ; rien n'a été écrasé.") from exc

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            [w.to_dict() for w in self._watches.values()],
        )

    def _save_or_restore(self, previous: dict[str, Watch]) -> None:
        try:
            self._save()
        except BaseException:
            self._watches = previous
            raise

    def list(self) -> list[Watch]:
        with self._lock:
            return list(self._watches.values())

    def get(self, wid: str) -> Watch | None:
        with self._lock:
            return self._watches.get(wid)

    def add(self, raw: object, actor: str = "", now: datetime | None = None) -> Watch:
        w = validate_watch(raw, actor=actor, now=now)
        with self._lock:
            if w.id in self._watches:
                raise WatchError("Cette veille existe déjà")
            if len(self._watches) >= MAX_WATCHES:
                raise WatchError(f"Limite de {MAX_WATCHES} veilles atteinte")
            previous = self._watches.copy()
            self._watches[w.id] = w
            self._save_or_restore(previous)
        return w

    def remove(self, wid: str) -> Watch:
        with self._lock:
            previous = self._watches.copy()
            w = self._watches.pop(wid)  # KeyError si inconnue
            self._save_or_restore(previous)
        return w

    def set_active(self, wid: str, active: bool) -> Watch:
        with self._lock:
            old = self._watches[wid]
            previous = self._watches.copy()
            w = Watch(**{**asdict(old), "active": active})
            self._watches[wid] = w
            self._save_or_restore(previous)
        return w


def load_seed(path: str | Path | None) -> list[dict]:
    """Veilles initiales d'un fichier de configuration (facultatif) ; entrées invalides refusées avec le nom du fichier."""
    if path is None or not Path(path).exists():
        return []
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise WatchError("liste attendue")
        for e in entries:
            validate_watch(e)
        return entries
    except ValueError as exc:
        raise WatchError(f"{path} : {exc}") from exc


# --- vue de suivi ----------------------------------------------------------------------------------------------------
def _when(doc) -> datetime:
    return doc.published_at or doc.fetched_at


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


REPEAT_JACCARD = 0.7


def _post_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in fold(title).split() if len(w) >= 4)


def collapse_repeats(posts: list[dict]) -> list[dict]:
    """Un même compte qui republie le même texte (bots d'agrégation) : on garde le plus récent et on compte les répétitions.

    Comparaison dans un même flux seulement (Jaccard des mots de 4 lettres et plus >= 0,7) ; `posts` est trié du plus récent au plus ancien.
    """
    kept: list[tuple[dict, frozenset[str]]] = []
    for p in posts:
        tokens = _post_tokens(p["title"])
        twin = None
        if len(tokens) >= 4:
            for k, kt in kept:
                if k["source_id"] == p["source_id"] and len(tokens & kt) / len(tokens | kt) >= REPEAT_JACCARD:
                    twin = k
                    break
        if twin is None:
            p["repeats"] = 0
            kept.append((p, tokens))
        else:
            twin["repeats"] += 1
            twin["watches"] = list(dict.fromkeys(twin["watches"] + p["watches"]))
    return [k for k, _ in kept]


def social_view(store: InMemoryStore, book: WatchBook, reports: list[dict] | None = None, now: datetime | None = None, *,
                demo: bool = False, watch: str | None = None, status: str | None = None, country: str | None = None,
                limit: int = 100) -> dict:
    now = now or datetime.now(timezone.utc)
    social_ids = {s.id for s in store.sources() if s.channel == "social"}
    by_doc = {}
    for e in store.events():
        for ref in e.refs:
            by_doc[ref.document_id] = e
    watches = book.list()
    posts = []
    for d in store.documents():
        if d.source_id not in social_ids:
            continue
        e = by_doc.get(d.id)
        matched = []
        hay = fold(f"{d.title} {d.text}")
        for w in watches:
            if not w.active:
                continue
            if w.kind == "keyword":
                if fold(w.value) not in hay:
                    continue
            elif d.source_id != w.source_id:
                continue
            if w.countries and (e is None or e.country_iso3 not in w.countries):
                continue
            if w.categories and (e is None or e.domain.value not in w.categories):
                continue
            matched.append(w.id)
        corro, st = [], "to_qualify"
        if e is not None:
            for ref in e.refs:
                src = store.get_source(ref.source_id)
                if src is not None and src.channel != "social":
                    rd = store.get_document(ref.document_id)
                    corro.append({"source_id": src.id, "name": src.name, "reliability": ref.reliability, "url": safe_url(rd.url) if rd else None})
            st = "corroborated" if corro else "unconfirmed"
        src = store.get_source(d.source_id)
        c = COUNTRIES_BY_ISO[e.country_iso3] if e else None
        posts.append({
            "id": d.id, "title": d.title, "summary": d.text[:280], "url": safe_url(d.url), "source_id": d.source_id,
            "source_name": src.name if src else d.source_id, "published_at": _iso(_when(d)), "language": d.language,
            "watches": matched, "status": st, "status_label": STATUS_LABELS_FR[st],
            "country": c.iso3 if c else None, "country_name": c.name_fr if c else None,
            "domain": e.domain.value if e else None, "domain_label": DOMAIN_LABELS_FR[e.domain] if e else None,
            "level": e.level if e else None, "level_label": LEVEL_LABELS_FR[e.level] if e else None,
            "event_id": e.id if e else None, "media_sources": corro, "reason": d.reject_reason if st == "to_qualify" else None,
        })
    # Une même publication peut arriver par plusieurs flux (#Mali, #Sahel, #BurkinaFaso) : on la montre une fois.
    merged: dict[str, dict] = {}
    for p in posts:
        key = f"{p['url']}|{fold(p['title'])[:120]}"  # même adresse ET même texte : deux messages distincts ne fusionnent jamais
        first = merged.get(key)
        if first is None:
            p["also_in"] = []
            merged[key] = p
        else:
            first["watches"] = list(dict.fromkeys(first["watches"] + p["watches"]))
            if p["source_name"] != first["source_name"] and p["source_name"] not in first["also_in"]:
                first["also_in"].append(p["source_name"])
    posts = list(merged.values())
    posts.sort(key=lambda p: p["published_at"], reverse=True)
    posts = collapse_repeats(posts)

    day, week = _iso(now - timedelta(hours=24)), _iso(now - timedelta(days=7))
    report = {r["source_id"]: r for r in reports or []}
    stats = []
    for w in watches:
        mine = [p for p in posts if w.id in p["watches"]]
        if not w.active:
            conn = {"status": "inactive"}
        elif not w.collectable:
            conn = {"status": "filter"}
        elif demo:
            conn = {"status": "demo"}
        elif (r := report.get(w.source_id)) is None:
            conn = {"status": "pending"}
        else:
            conn = {"status": "ok" if r["ok"] else "error", "at": _iso(r["at"]) if isinstance(r["at"], datetime) else r["at"],
                    "error": r.get("error"), "documents": r.get("documents"),
                    "skipped": r.get("skipped"), "total": r.get("total")}
        stats.append({**w.to_dict(), "posts_24h": sum(p["published_at"] >= day for p in mine), "posts_7d": sum(p["published_at"] >= week for p in mine),
                      "last_post_at": mine[0]["published_at"] if mine else None, "connector": conn})

    recent = [p for p in posts if p["published_at"] >= day]
    pool = [p for p in posts if (not watch or watch in p["watches"]) and (not country or p["country"] == country)]
    by_status = {k: sum(p["status"] == k for p in pool) for k in STATUS_LABELS_FR}
    # « relevant » : tout sauf « à qualifier » (hors sujet, ou pays/thème non reconnu) ; masqué par défaut dans l'interface
    shown = [p for p in pool if not status or (p["status"] != "to_qualify" if status == "relevant" else p["status"] == status)]
    return {
        "watches": stats, "posts": shown[:limit], "total": len(shown), "by_status": by_status,
        "summary": {"posts_24h": len(recent), **{k: sum(p["status"] == k for p in recent) for k in STATUS_LABELS_FR}},
        "demo": demo,
        "limits": "Collecte limitée aux flux RSS publics : Mastodon (hashtags, comptes), Bluesky (comptes), YouTube (chaînes), Reddit (subreddits). "
                  "X, Facebook, Instagram, TikTok, WhatsApp et Telegram ne sont pas branchés. "
                  "La corroboration suppose des titres proches (même pays, même thème, 48 h).",
    }


# --- intégration : sources de collecte et démonstration ----------------------------------------------------------------
def sync_sources(store: InMemoryStore, book: WatchBook) -> None:
    """Aligne les sources du magasin sur les veilles : ajoute ou met à jour celles des veilles hashtag/compte,
    désactive celles dont la veille a été supprimée (l'historique des documents reste consultable)."""
    wanted = {s.id: s for w in book.list() if (s := watch_source(w)) is not None}
    for s in wanted.values():
        store.add_source(s)
    for s in store.sources():
        if s.id.startswith("watch-") and s.id not in wanted and s.active:
            store.add_source(replace(s, active=False))


# (heures avant maintenant, titre, texte) : FICTIFS. Le premier recoupe un titre de la démonstration (corroboré),
# le deuxième est isolé (non confirmé), le troisième n'a ni pays ni thème (à qualifier).
DEMO_POSTS = (
    (3.0, "[DÉMO] Coup d'État en cours à Bamako selon des témoins", "Des témoins évoquent des tirs et un coup d'État en cours à Bamako, au Mali."),
    (5.0, "[DÉMO] Attaque armée signalée à Kidal, au Mali", "Des témoins signalent une attaque armée et des tirs à Kidal, au Mali ; aucune confirmation officielle."),
    (8.0, "[DÉMO] Rumeur sans localisation", "Message sans lieu précis ni thème reconnaissable."),
)


def load_demo_social(store: InMemoryStore, book: WatchBook, now: datetime | None = None) -> int:
    """Veilles et publications FICTIVES pour la démonstration (aucune collecte réelle) ; retourne le nombre de publications."""
    from .services import ingest_manual  # import local : services importe déjà une grande partie du paquet

    now = now or datetime.now(timezone.utc)
    hashtag = book.add({"kind": "hashtag", "value": "Mali", "name": "#Mali [DÉMO]"}, actor="démonstration", now=now)
    book.add({"kind": "keyword", "value": "coup d'État", "name": "Mot-clé « coup d'État » [DÉMO]"}, actor="démonstration", now=now)
    sync_sources(store, book)
    for hours, title, text in DEMO_POSTS:
        ingest_manual(store, title=title, text=text, source_id=hashtag.source_id, published_at=now - timedelta(hours=hours),
                      url=f"https://exemple.test/demo-post/{abs(hash(title)) % 10_000}")
    return len(DEMO_POSTS)


def assert_public_host(url: str, resolve=socket.getaddrinfo) -> None:
    """Refuse de contacter un nom qui résout vers une adresse non publique (boucle locale, réseau privé, lien local…).

    Complète la validation syntaxique de l'instance : un nom de domaine public peut pointer vers une adresse interne
    (ex. 127.0.0.1.nip.io). Limite : entre ce contrôle et la requête, une nouvelle résolution reste théoriquement possible."""
    host = urlparse(url).hostname
    if not host:
        raise WatchError("URL sans hôte")
    try:
        infos = resolve(host, None)
    except OSError as exc:
        raise WatchError(f"Résolution impossible pour {host} : {exc}") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise WatchError(f"{host} résout vers une adresse non publique : collecte refusée")
