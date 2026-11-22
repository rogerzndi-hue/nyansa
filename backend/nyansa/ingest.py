"""Ingestion : lecture de flux RSS 2.0 et Atom, chargement de la liste des sources.

Sécurité des flux non fiables :
- schémas http(s) uniquement, taille maximale de réponse, délai maximal ;
- les documents XML contenant une déclaration DOCTYPE ou ENTITY sont refusés
  (protection contre les « billion laughs » et les entités externes).
Légalité : ne brancher une source qu'après vérification de ses CGU et de sa licence de réutilisation
(champ license_note de la source).
"""

from __future__ import annotations

import json
import ipaddress
import socket
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from .models import RawDocument, Source
from .text import strip_html

MAX_BYTES = 5_000_000
USER_AGENT = "NyansaBot/0.1 (veille ouverte; contact: a renseigner)"

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_MEDIA = "{http://search.yahoo.com/mrss/}"


class FeedError(ValueError):
    pass


def _assert_public_url(url: str) -> None:
    """Refuse les URL malform?es et les destinations r?seau non publiques."""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FeedError("URL invalide") from exc

    if scheme not in ("http", "https") or not host:
        raise FeedError("Seules les URL HTTP(S) avec h?te sont autoris?es")

    if parsed.username is not None or parsed.password is not None:
        raise FeedError("Les identifiants int?gr?s aux URL sont interdits")

    expected_port = 443 if scheme == "https" else 80
    if port not in (None, expected_port):
        raise FeedError("Port r?seau non autoris?")

    try:
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            infos = socket.getaddrinfo(
                host, expected_port, type=socket.SOCK_STREAM
            )
            addresses = [
                ipaddress.ip_address(info[4][0])
                for info in infos
            ]
    except (OSError, ValueError) as exc:
        raise FeedError(f"R?solution DNS impossible pour {host}") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise FeedError(f"Destination non publique refus?e : {host}")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Contr?le chaque destination avant de suivre une redirection."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(req.full_url).scheme == "https" and urlparse(newurl).scheme != "https":
            raise FeedError("Redirection HTTPS vers HTTP interdite")
        _assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, timeout: float = 20.0, max_bytes: int = MAX_BYTES) -> bytes:
    _assert_public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_PublicRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
    except FeedError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise FeedError(f"?chec de collecte du flux : {exc}") from exc

    if len(data) > max_bytes:
        raise FeedError(f"R?ponse trop volumineuse (> {max_bytes} octets)")
    return data


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


TITLE_FALLBACK_CHARS = 140


def _fallback_title(text: str) -> str:
    """Titre de secours : début du texte. Les flux de réseaux sociaux (Mastodon) n'ont souvent pas de <title>."""
    text = " ".join(text.split())
    if len(text) <= TITLE_FALLBACK_CHARS:
        return text
    return text[:TITLE_FALLBACK_CHARS].rsplit(" ", 1)[0] + "…"


def parse_feed(xml_bytes: bytes, source_id: str, fetched_at: datetime | None = None) -> list[RawDocument]:
    """Transforme un flux RSS 2.0 ou Atom en documents bruts (titre et texte sans HTML)."""
    return parse_feed_stats(xml_bytes, source_id, fetched_at)[0]


def parse_feed_stats(xml_bytes: bytes, source_id: str, fetched_at: datetime | None = None) -> tuple[list[RawDocument], int]:
    """Comme parse_feed, et retourne aussi le nombre d'éléments ignorés (ni titre ni texte) : un flux « lu » mais vide se voit."""
    lowered = xml_bytes.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise FeedError("Flux refusé : déclaration DOCTYPE/ENTITY interdite")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedError(f"XML invalide : {exc}") from exc

    fetched_at = fetched_at or datetime.now(timezone.utc)
    docs: list[RawDocument] = []

    if root.tag == f"{_ATOM}feed":
        for entry in root.findall(f"{_ATOM}entry"):
            link = entry.find(f"{_ATOM}link")
            body = (_text(entry.find(f"{_ATOM}content")) or _text(entry.find(f"{_ATOM}summary"))
                    or _text(entry.find(f"{_MEDIA}group/{_MEDIA}description")))  # flux YouTube : description de la vidéo
            docs.append(RawDocument(
                source_id=source_id,
                title=strip_html(_text(entry.find(f"{_ATOM}title"))),
                text=strip_html(body),
                url=link.get("href") if link is not None else None,
                published_at=_parse_date(_text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated"))),
                fetched_at=fetched_at,
            ))
    elif root.tag == "rss":
        for item in root.iterfind("./channel/item"):
            body = _text(item.find(f"{_CONTENT}encoded")) or _text(item.find("description"))
            docs.append(RawDocument(
                source_id=source_id,
                title=strip_html(_text(item.find("title"))),
                text=strip_html(body),
                url=_text(item.find("link")) or None,
                published_at=_parse_date(_text(item.find("pubDate"))),
                fetched_at=fetched_at,
            ))
    else:
        raise FeedError(f"Format de flux non reconnu : <{root.tag}>")

    for d in docs:
        if not d.title and d.text:
            d.title = _fallback_title(d.text)
    kept = [d for d in docs if d.title]
    return kept, len(docs) - len(kept)


CHANNELS = ("media", "social")


def _channel(entry: dict) -> str:
    channel = entry.get("channel", "media")
    if channel not in CHANNELS:
        raise ValueError(f"Source {entry.get('id')!r} : channel invalide {channel!r} (attendu : {', '.join(CHANNELS)})")
    return channel


def load_sources(path: str | Path) -> list[Source]:
    """Charge config/sources.json. Les entrées sans URL (modèles) sont ignorées."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = []
    for e in entries:
        if not e.get("url") and e.get("kind") != "manual":
            continue
        sources.append(Source(
            id=e["id"], name=e["name"], kind=e["kind"], default_reliability=e["default_reliability"],
            url=e.get("url"), language=e.get("language"), country_iso3=e.get("country_iso3"),
            license_note=e.get("license_note"), active=e.get("active", True),
            channel=_channel(e),
            publisher_id=e.get("publisher_id"),
        ))
    return sources
