"""Alertes configurables : règles -> alertes dédupliquées -> notifications (application, webhook, e-mail).

Principes
- Une alerte naît d'un événement qui satisfait une règle (niveau minimal, pays, région, thèmes, mots-clés).
- Pas de notification répétée : un même incident ne déclenche qu'UNE alerte par règle, sauf aggravation
  (niveau de criticité supérieur) qui produit une alerte « escalade ». L'empreinte de l'incident est stable
  d'un redémarrage à l'autre (pays, thème, mots du titre), pas l'identifiant interne de l'événement.
- Une publication sociale isolée ne déclenche rien sauf si la règle l'accepte explicitement (include_social) ;
  l'alerte porte alors le statut « non confirmé ».
- Notifications externes : plage de silence (sauf niveau critique), limite par heure et par règle, 3 tentatives.
  Jamais de notification externe en mode démonstration. Les secrets (SMTP) viennent de l'environnement, jamais du code.
- Le réseau est appelé HORS verrou du magasin d'événements : collect() lit le magasin, deliver_pending() envoie.
Limites : règles lues dans un fichier JSON (pas d'éditeur dans l'interface) ; localisation = pays seulement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import ssl
import threading
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urlparse

from .countries import COUNTRIES_BY_ISO
from .deliverables.builder import event_status
from .ingest import FeedError, _PublicRedirectHandler, _assert_public_url
from .models import Event
from .persistence import atomic_write_json
from .services import safe_url
from .store_protocol import StoreProtocol
from .taxonomy import DOMAIN_LABELS_FR, LEVEL_LABELS_FR, REGION_LABELS_FR, Domain, Region
from .text import fold

LEVEL_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
VERIFICATION_LABELS_FR = {
    "confirmed": "Confirmé (au moins 2 sources médias distinctes)",
    "single_source": "Source média unique : non recoupé",
    "unconfirmed_social": "Réseaux sociaux seuls : NON CONFIRMÉ",
}
MAX_ALERTS = 500
MAX_ATTEMPTS = 3
WEBHOOK_TIMEOUT_S = 10.0
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    min_level: str = "high"
    countries: tuple[str, ...] = ()
    region: str | None = None
    domains: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    include_social: bool = False
    webhook_url: str | None = None
    emails: tuple[str, ...] = ()
    quiet_start: str | None = None  # « HH:MM » UTC : pas de notification externe dans la plage (sauf critique)
    quiet_end: str | None = None
    max_external_per_hour: int = 10
    active: bool = True


DEFAULT_RULES = (Rule(id="default-high", name="Événements élevés ou critiques (tous pays, médias)", min_level="high"),)


def parse_rules(entries: object) -> list[Rule]:
    """Valide strictement les règles JSON sans conversion implicite dangereuse."""
    if not isinstance(entries, list):
        raise RuleError("Le fichier de règles doit contenir une liste")

    rules, seen = [], set()

    def string_value(value, field, where, *, allow_none=False):
        if value is None and allow_none:
            return None
        if not isinstance(value, str):
            raise RuleError(f"{where} : {field} doit être une chaîne")
        return value.strip()

    def string_list(value, field, where):
        if value is None:
            return []
        if not isinstance(value, list):
            raise RuleError(f"{where} : {field} doit être une liste")
        if len(value) > 100:
            raise RuleError(f"{where} : {field} contient trop d'éléments")
        result = []
        for item in value:
            if not isinstance(item, str):
                raise RuleError(f"{where} : chaque élément de {field} doit être une chaîne")
            item = item.strip()
            if not item:
                raise RuleError(f"{where} : {field} ne peut pas contenir de valeur vide")
            result.append(item)
        return tuple(dict.fromkeys(result))

    for i, e in enumerate(entries):
        where = f"règle n°{i + 1}"
        if not isinstance(e, dict):
            raise RuleError(f"{where} : objet attendu")

        rid = string_value(e.get("id"), "id", where)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", rid):
            raise RuleError(f"{where} : id obligatoire (lettres, chiffres, . _ -)")
        if rid in seen:
            raise RuleError(f"{where} : id en double ({rid})")
        seen.add(rid)
        where = f"règle {rid}"

        name = string_value(e.get("name", rid), "name", where)
        if not name:
            raise RuleError(f"{where} : name ne peut pas être vide")

        level = string_value(e.get("min_level", "high"), "min_level", where)
        if level not in LEVEL_RANK:
            raise RuleError(f"{where} : min_level invalide ({level!r}) ; attendu {', '.join(LEVEL_RANK)}")

        countries = string_list(e.get("countries"), "countries", where)
        countries = tuple(c.upper() for c in countries)
        for c in countries:
            if c not in COUNTRIES_BY_ISO:
                raise RuleError(f"{where} : pays inconnu {c!r} (code ISO3 attendu)")

        region = string_value(e.get("region"), "region", where, allow_none=True)
        if region == "":
            region = None
        if region is not None and region not in {r.value for r in Region}:
            raise RuleError(f"{where} : région inconnue {region!r}")

        domains = string_list(e.get("domains"), "domains", where)
        for d in domains:
            if d not in {x.value for x in Domain}:
                raise RuleError(f"{where} : thème inconnu {d!r}")

        keywords = string_list(e.get("keywords"), "keywords", where)
        if any(len(k) > 200 for k in keywords):
            raise RuleError(f"{where} : chaque mot-clé doit faire 200 caractères au maximum")

        webhook = string_value(e.get("webhook_url"), "webhook_url", where, allow_none=True)
        if webhook == "":
            webhook = None
        if webhook is not None:
            if len(webhook) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in webhook) or "\\" in webhook:
                raise RuleError(f"{where} : webhook_url invalide")
            try:
                u = urlparse(webhook)
                port = u.port
                hostname = u.hostname
            except ValueError as exc:
                raise RuleError(f"{where} : webhook_url invalide") from exc
            if (
                u.scheme not in ("http", "https")
                or not u.netloc
                or not hostname
                or u.username is not None
                or u.password is not None
                or u.fragment
                or (port is not None and port not in (80, 443))
            ):
                raise RuleError(f"{where} : webhook_url doit être une URL http(s) publique, sans identifiants ni port non autorisé")

        emails = string_list(e.get("emails"), "emails", where)
        for address in emails:
            if len(address) > 254 or not _EMAIL.fullmatch(address):
                raise RuleError(f"{where} : adresse e-mail invalide {address!r}")

        qs = string_value(e.get("quiet_start"), "quiet_start", where, allow_none=True)
        qe = string_value(e.get("quiet_end"), "quiet_end", where, allow_none=True)
        if (
            (qs is None) != (qe is None)
            or any(v is not None and not _HHMM.fullmatch(v) for v in (qs, qe))
        ):
            raise RuleError(f"{where} : quiet_start et quiet_end vont ensemble, au format HH:MM")

        cap = e.get("max_external_per_hour", 10)
        if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 1000:
            raise RuleError(f"{where} : max_external_per_hour entre 1 et 1000")

        include_social = e.get("include_social", False)
        active = e.get("active", True)
        if not isinstance(include_social, bool):
            raise RuleError(f"{where} : include_social doit être un booléen")
        if not isinstance(active, bool):
            raise RuleError(f"{where} : active doit être un booléen")

        rules.append(Rule(
            id=rid,
            name=name[:120],
            min_level=level,
            countries=countries,
            region=region,
            domains=domains,
            keywords=keywords,
            include_social=include_social,
            webhook_url=webhook,
            emails=emails,
            quiet_start=qs,
            quiet_end=qe,
            max_external_per_hour=cap,
            active=active,
        ))

    return rules


def load_rules(path: str | Path | None) -> list[Rule]:
    """Règles du fichier JSON ; sans fichier, une règle prudente (élevé ou critique, médias, application seulement)."""
    if path is None or not Path(path).exists():
        return list(DEFAULT_RULES)
    try:
        return parse_rules(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:  # lecture impossible, JSON invalide ou RuleError
        raise RuleError(f"{path} : {exc}") from exc


def fingerprint(event: Event) -> str:
    """Empreinte stable d'un incident : pays, thème et mots significatifs du titre (indépendante des identifiants internes)."""
    words = sorted({w for w in fold(event.title).split() if len(w) >= 4})
    return hashlib.sha1(f"{event.country_iso3}|{event.domain.value}|{' '.join(words)}".encode()).hexdigest()[:16]  # noqa: S324 : identifiant


def in_quiet_hours(rule: Rule, now: datetime) -> bool:
    if not rule.quiet_start or not rule.quiet_end:
        return False
    cur = now.astimezone(timezone.utc).strftime("%H:%M")
    s, e = rule.quiet_start, rule.quiet_end
    return (s <= cur < e) if s <= e else (cur >= s or cur < e)  # la plage peut passer minuit


def matches(rule: Rule, event: Event, status: str) -> bool:
    if not rule.active or LEVEL_RANK[event.level] < LEVEL_RANK[rule.min_level]:
        return False
    if rule.countries and event.country_iso3 not in rule.countries:
        return False
    if rule.region and COUNTRIES_BY_ISO[event.country_iso3].region.value != rule.region:
        return False
    if rule.domains and not ({event.domain.value} | {d.value for d in event.secondary_domains}) & set(rule.domains):
        return False
    if rule.keywords:
        hay = fold(f"{event.title} {event.summary}")
        if not any(fold(k) in hay for k in rule.keywords):
            return False
    return not (status == "unconfirmed_social" and not rule.include_social)


# --- transports (remplaçables dans les tests) ------------------------------------------------------------------------
def post_webhook(url: str, payload: dict) -> None:
    _assert_public_url(url)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "NyansaAlerts/0.1",
        },
    )
    opener = urllib.request.build_opener(_PublicRedirectHandler())
    with opener.open(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
        if resp.status >= 300:
            raise OSError(f"HTTP {resp.status}")


def build_email(alert: dict, recipients: tuple[str, ...], sender: str) -> EmailMessage:
    one_line = lambda s: " ".join(str(s).split())  # noqa: E731 : pas de saut de ligne dans les en-têtes (injection)
    msg = EmailMessage()
    msg["Subject"] = one_line(f"[Nyansa] {alert['level_label'].upper()} — {alert['country_name']} — {alert['title']}")[:200]
    msg["From"], msg["To"] = sender, ", ".join(recipients)
    lines = [alert["title"], "", f"Pays : {alert['country_name']} ({alert['region_label']})", f"Thème : {alert['domain_label']}",
             f"Niveau : {alert['level_label']} (criticité {alert['criticality']})", f"Vérification : {alert['verification_label']}",
             f"Événement : {alert['event_time']}", f"Détectée : {alert['created_at']}", ""]
    lines += ["Sources :"] + [f"- {s['name']} (fiabilité {s['reliability']}) {s['url'] or ''}".rstrip() for s in alert["sources"]]
    lines += ["", f"Carte (recherche du pays, localisation approximative) : {alert['map_url']}", "",
              "Message automatique de Nyansa. Information issue de sources ouvertes, à vérifier avant toute décision."]
    msg.set_content("\n".join(lines))
    return msg


def send_email(alert: dict, recipients: tuple[str, ...]) -> None:
    host = os.environ.get("NYANSA_SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP non configuré (variables NYANSA_SMTP_HOST, _PORT, _USER, _PASSWORD, _FROM)")
    port = int(os.environ.get("NYANSA_SMTP_PORT", "587"))
    sender = os.environ.get("NYANSA_SMTP_FROM") or os.environ.get("NYANSA_SMTP_USER") or "nyansa@localhost"
    msg = build_email(alert, recipients, sender)
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if os.environ.get("NYANSA_SMTP_USER"):
            smtp.login(os.environ["NYANSA_SMTP_USER"], os.environ.get("NYANSA_SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class AlertCenter:
    def __init__(self, rules: list[Rule] | None = None, path: str | Path | None = None, *, webhook=post_webhook,
                 email=send_email, external: bool = True) -> None:
        self._lock = threading.RLock()
        self.rules = list(rules if rules is not None else DEFAULT_RULES)
        self._path = Path(path) if path else None
        self._webhook, self._email = webhook, email
        self.external = external  # faux en démonstration : jamais de notification externe
        self._alerts: list[dict] = []
        self._seen: dict[str, int] = {}  # "règle|empreinte" -> rang de niveau déjà notifié
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._alerts, self._seen = data["alerts"], data["seen"]
            except (OSError, ValueError, KeyError) as exc:
                raise RuntimeError(f"Fichier des alertes illisible ({self._path}) : {exc}. Corrigez ou déplacez-le ; rien n'a été écrasé.") from exc

        recovered = False
        for alert in self._alerts:
            for delivery in alert.get("deliveries", []):
                if delivery.get("status") == "sending":
                    delivery.update(
                        status="failed",
                        error="Envoi interrompu : résultat externe inconnu",
                    )
                    recovered = True
        if recovered:
            self._save()

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def _save(self) -> None:
        if not self._path:
            return
        self._alerts = self._alerts[-MAX_ALERTS:]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {"alerts": self._alerts, "seen": self._seen},
        )

    # --- création -------------------------------------------------------------------------------------------------
    def _make(self, rule: Rule, event: Event, store: StoreProtocol, status: str, reason: str, now: datetime) -> dict:
        c = COUNTRIES_BY_ISO[event.country_iso3]
        sources = []
        for ref in event.refs:
            doc, src = store.get_document(ref.document_id), store.get_source(ref.source_id)
            sources.append({"name": src.name if src else ref.source_id, "channel": src.channel if src else "media",
                            "reliability": ref.reliability, "url": safe_url(doc.url) if doc else None})
        externals = ([{"channel": "webhook", "status": "pending", "attempts": 0}] if rule.webhook_url else []) \
            + ([{"channel": "email", "status": "pending", "attempts": 0}] if rule.emails else [])
        return {
            "id": uuid.uuid4().hex, "rule_id": rule.id, "rule_name": rule.name, "reason": reason,
            "created_at": _iso(now), "event_time": _iso(event.occurred_at), "event_id": event.id,
            "level": event.level, "level_label": LEVEL_LABELS_FR[event.level], "criticality": event.criticality,
            "title": event.title, "country": c.iso3, "country_name": c.name_fr, "region": c.region.value,
            "region_label": REGION_LABELS_FR[c.region], "domain": event.domain.value, "domain_label": DOMAIN_LABELS_FR[event.domain],
            "verification": status, "verification_label": VERIFICATION_LABELS_FR[status], "sources": sources,
            "map_url": f"https://www.openstreetmap.org/search?query={quote(c.name_en)}",  # pays seulement : localisation approximative
            "status": "new", "acked_by": None, "acked_at": None,
            "deliveries": [{"channel": "app", "status": "sent", "attempts": 1}] + (externals if self.external else
                          [{**d, "status": "skipped", "error": "mode démonstration : aucune notification externe"} for d in externals]),
        }

    def collect(self, store: StoreProtocol, now: datetime | None = None) -> list[dict]:
        """Évalue les événements du magasin (à appeler sous le verrou du magasin) ; retourne les nouvelles alertes."""
        now = now or datetime.now(timezone.utc)
        created: list[dict] = []
        with self._lock:
            for event in store.events():
                status = event_status(event, store)
                fp = fingerprint(event)
                for rule in self.rules:
                    if not matches(rule, event, status):
                        continue
                    key, rank = f"{rule.id}|{fp}", LEVEL_RANK[event.level]
                    previous = self._seen.get(key)
                    if previous is not None and rank <= previous:
                        continue  # déjà signalé à ce niveau (ou plus haut) : pas de répétition
                    self._seen[key] = rank
                    created.append(self._make(rule, event, store, status, "new" if previous is None else "escalation", now))
            if created:
                self._alerts.extend(created)
                self._save()
        return created

    # --- envoi ---------------------------------------------------------------------------------------------------
    def _recent_external(self, rule_id: str, now: datetime) -> int:
        since = _iso(now - timedelta(hours=1))
        return sum(
            1
            for alert in self._alerts
            if alert["rule_id"] == rule_id
            for delivery in alert["deliveries"]
            if delivery["channel"] != "app"
            and delivery.get("at", "") >= since
            and delivery["status"] in ("sent", "sending")
        )

    def deliver_pending(self, now: datetime | None = None) -> int:
        """Réserve les notifications sous verrou, puis les envoie hors verrou."""
        now = now or datetime.now(timezone.utc)
        rules = {rule.id: rule for rule in self.rules}
        todo = []
        changed = False

        with self._lock:
            # Instantan° complet avant toute mutation des livraisons.
            delivery_snapshots = [
                (delivery, dict(delivery))
                for alert in self._alerts
                for delivery in alert["deliveries"]
            ]
            budget = {}
            for alert in self._alerts:
                rule = rules.get(alert["rule_id"])
                for delivery in alert["deliveries"]:
                    if (
                        rule is None
                        or delivery["channel"] == "app"
                        or delivery["status"] not in ("pending", "deferred", "failed")
                        or delivery["attempts"] >= MAX_ATTEMPTS
                    ):
                        continue

                    if alert["level"] != "critical" and in_quiet_hours(rule, now):
                        delivery["status"] = "deferred"
                        changed = True
                        continue

                    if rule.id not in budget:
                        budget[rule.id] = max(
                            0,
                            rule.max_external_per_hour - self._recent_external(rule.id, now),
                        )

                    if budget[rule.id] <= 0:
                        delivery.update(
                            status="skipped",
                            error="limite horaire atteinte pour cette règle",
                        )
                        changed = True
                        continue

                    budget[rule.id] -= 1
                    previous = (delivery["status"], delivery.get("at"), delivery["attempts"])
                    delivery.update(
                        status="sending",
                        at=_iso(now),
                        attempts=delivery["attempts"] + 1,
                    )
                    todo.append((dict(alert), delivery, rule, previous))
                    changed = True

            if changed:
                try:
                    self._save()
                except Exception:
                    # L'écriture a échoué : aucune notification réseau n'a commencé.
                    # Restaurer aussi les états deferred et skipped.
                    for delivery, snapshot in delivery_snapshots:
                        delivery.clear()
                        delivery.update(snapshot)
                    raise

        sent = 0
        for alert, delivery, rule, _ in todo:
            try:
                payload = {key: value for key, value in alert.items() if key != "deliveries"}
                if delivery["channel"] == "webhook":
                    self._webhook(rule.webhook_url, payload)
                else:
                    self._email(alert, rule.emails)
                outcome = {"status": "sent"}
            except Exception as exc:
                outcome = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }

            with self._lock:
                delivery.update(outcome, at=_iso(now))
                if outcome["status"] == "sent":
                    delivery.pop("error", None)
                    sent += 1
                self._save()

        return sent

    # --- consultation ---------------------------------------------------------------------------------------------
    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            items = [a for a in reversed(self._alerts) if status in (None, a["status"])]
            return json.loads(json.dumps(items[:limit]))

    def acknowledge(self, alert_id: str, actor: str, now: datetime | None = None) -> dict:
        with self._lock:
            for a in self._alerts:
                if a["id"] == alert_id:
                    if a["status"] == "new":
                        a.update(status="ack", acked_by=actor, acked_at=_iso(now or datetime.now(timezone.utc)))
                        self._save()
                    return json.loads(json.dumps(a))
        raise KeyError(alert_id)

    def describe_rules(self) -> list[dict]:
        """Règles pour l'affichage : l'URL du webhook est réduite à son hôte (elle peut contenir un jeton)."""
        return [{"id": r.id, "name": r.name, "min_level": r.min_level, "countries": list(r.countries), "region": r.region,
                 "domains": list(r.domains), "keywords": list(r.keywords), "include_social": r.include_social,
                 "webhook_host": urlparse(r.webhook_url).netloc if r.webhook_url else None, "emails": len(r.emails),
                 "quiet_hours": f"{r.quiet_start}-{r.quiet_end} UTC" if r.quiet_start else None,
                 "max_external_per_hour": r.max_external_per_hour, "active": r.active} for r in self.rules]
