"""Construction d'un brouillon de livrable à partir des événements de la base.

Principes (voir README) :
- aucune phrase factuelle sans source : chaque fait reprend un événement et renvoie à des sources numérotées [n] ;
- les nombres sont calculés (étiquette « calcul »), jamais rédigés à la main ;
- les sections d'analyse restent vides pour l'analyste : aucun modèle de langage n'est branché dans cette version ;
- une publication issue des seuls réseaux sociaux reste étiquetée « non confirmée ».
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..countries import COUNTRIES_BY_ISO
from ..models import Event
from ..services import coverage_report, risk_map
from ..store import InMemoryStore
from ..taxonomy import DOMAIN_LABELS_FR, LEVEL_LABELS_FR, REGION_LABELS_FR, Domain, Region
from .catalog import CLASSIFICATIONS, DETAIL_LEVELS, LABELS, LANGUAGES, SECTIONS, TEMPLATES

DOMAIN_LABELS_EN = {
    "GEO": "Geopolitics and diplomacy", "ECO": "Economy and finance", "SEC": "Security", "SOC": "Social movements",
    "GOV": "Elections and governance", "CYB": "Cyber", "HUM": "Humanitarian, health and climate",
}
REGION_LABELS_EN = {"NORTH": "North Africa", "WEST": "West Africa", "CENTRAL": "Central Africa", "EAST": "East Africa", "SOUTHERN": "Southern Africa"}
LEVEL_LABELS_EN = {"low": "Low", "moderate": "Moderate", "high": "High", "critical": "Critical"}
MAX_TEXT = 2_000


@dataclass
class BuildParams:
    template_id: str
    language: str = "fr"
    countries: tuple[str, ...] = ()
    region: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    domains: tuple[str, ...] = ()
    detail: str = "standard"
    sections: tuple[str, ...] | None = None
    recipient: str = ""
    analyst: str = ""
    organization: str = ""
    classification: str = "INTERNE"
    instructions: str = ""


def _dt(value: object, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} : date invalide ({value!r}), format attendu AAAA-MM-JJ") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(raw: dict, key: str, limit: int = 200) -> str:
    value = raw.get(key) or ""
    if not isinstance(value, str):
        raise ValueError(f"{key} : texte attendu")
    return value.strip()[:limit]


def parse_params(raw: dict) -> BuildParams:
    """Valide les paramètres reçus de l'interface ; lève ValueError avec un message lisible."""
    if raw.get("template_id") not in TEMPLATES:
        raise ValueError("Type de document inconnu")
    template = TEMPLATES[raw["template_id"]]
    language = raw.get("language", "fr")
    if language not in LANGUAGES:
        raise ValueError(f"Langue non prise en charge : {language!r} (fr, en)")
    countries = tuple(str(c).upper() for c in raw.get("countries") or [])
    unknown = [c for c in countries if c not in COUNTRIES_BY_ISO]
    if unknown:
        raise ValueError(f"Pays inconnu(s) : {', '.join(unknown)}")
    region = raw.get("region") or None
    if region and region not in Region.__members__:
        raise ValueError(f"Région inconnue : {region!r}")
    domains = tuple(raw.get("domains") or ())
    if any(d not in Domain.__members__ for d in domains):
        raise ValueError("Thème inconnu")
    detail = raw.get("detail", "standard")
    if detail not in DETAIL_LEVELS:
        raise ValueError("Niveau de détail inconnu (brief, standard, full)")
    classification = raw.get("classification", "INTERNE")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"Confidentialité inconnue : {classification!r}")
    sections = raw.get("sections")
    if sections is not None:
        sections = tuple(sections)
        if any(s not in template.sections for s in sections):
            raise ValueError("Section absente de ce modèle")
    if template.scope == "country" and not countries:
        raise ValueError("Ce modèle demande au moins un pays")
    if template.scope == "region" and not (region or countries):
        raise ValueError("Ce modèle demande une région (ou des pays)")
    start, end = _dt(raw.get("start"), "start"), _dt(raw.get("end"), "end")
    if start and end and start > end:
        raise ValueError("La date de début est postérieure à la date de fin")
    return BuildParams(
        template_id=template.id, language=language, countries=countries, region=region, start=start, end=end,
        domains=domains, detail=detail, sections=sections, recipient=_text(raw, "recipient"),
        analyst=_text(raw, "analyst"), organization=_text(raw, "organization"), classification=classification,
        instructions=_text(raw, "instructions", MAX_TEXT),
    )


class _Sources:
    """Numérote les sources (une par document) dans l'ordre de première citation."""

    def __init__(self, store: InMemoryStore, lang: str) -> None:
        self.store, self.lang = store, lang
        self.items: list[dict] = []
        self._by_doc: dict[str, int] = {}
        self._by_url: dict[str, int] = {}  # un même message reçu par deux flux ne figure qu'une fois dans la liste

    def refs(self, event: Event) -> list[int]:
        out = []
        for ref in event.refs:
            doc = self.store.get_document(ref.document_id)
            if doc is None:
                continue
            url = doc.url if doc.url and doc.url.startswith(("http://", "https://")) else None
            if doc.id not in self._by_doc and url in self._by_url:
                self._by_doc[doc.id] = self._by_url[url]
            if doc.id not in self._by_doc:
                source = self.store.get_source(ref.source_id)
                self.items.append({
                    "n": len(self.items) + 1, "document_id": doc.id, "source_id": ref.source_id,
                    "source_name": source.name if source else ref.source_id,
                    "channel": source.channel if source else "media", "reliability": ref.reliability,
                    "title": doc.title[:300], "url": doc.url if doc.url and doc.url.startswith(("http://", "https://")) else None,
                    "published_at": doc.published_at.isoformat() if doc.published_at else None,
                    "collected_at": doc.fetched_at.isoformat(),
                })
                self._by_doc[doc.id] = len(self.items)
                if url:
                    self._by_url[url] = len(self.items)
            out.append(self._by_doc[doc.id])
        return sorted(set(out))


_FIGURE_AFTER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ .,]\d{3})+|\d+)\s+(?:\w+\s+){0,3}?(?:killed|dead|deaths?|tu[eé]s?|morts?|victimes?)\b", re.I)
_FIGURE_BEFORE = re.compile(r"\b(?:kill(?:ed|s|ing)|tu[eé]s?)\s+(?:at least |plus de |au moins |over )?(\d{1,3}(?:[ .,]\d{3})+|\d+)\b", re.I)


def casualty_figures(titles: list[str]) -> list[int]:
    """Bilans chiffrés cités dans les titres (« 40 civilians killed », « 48 morts »), valeurs distinctes triées."""
    found: set[int] = set()
    for title in titles:
        for pattern in (_FIGURE_AFTER, _FIGURE_BEFORE):
            for m in pattern.finditer(title):
                digits = re.sub(r"[ .,]", "", m.group(1))
                if digits.isdigit() and 0 < int(digits) < 10_000_000:
                    found.add(int(digits))
    return sorted(found)


def event_status(event: Event, store: InMemoryStore) -> str:
    """Confirmé = au moins deux sources médias distinctes ; les réseaux sociaux ne corroborent pas."""
    media = set()
    for ref in event.refs:
        source = store.get_source(ref.source_id)
        if source is None or source.channel != "social":
            media.add(ref.source_id)
    if not media:
        return "unconfirmed_social"
    return "confirmed" if len(media) >= 2 else "single_source"


def _bid() -> str:
    return uuid.uuid4().hex[:10]


def _block(kind: str, text: str, refs: list[int] | None = None, **extra: object) -> dict:
    return {"id": _bid(), "kind": kind, "text": text, "refs": refs or [], "auto": True, **extra}


def build_report(store: InMemoryStore, params: BuildParams, now: datetime | None = None, demo: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    template, lang = TEMPLATES[params.template_id], params.language
    L = LABELS[lang]
    end = params.end or now
    start = params.start or end - timedelta(days=template.days)
    cap = DETAIL_LEVELS[params.detail]
    sources = _Sources(store, lang)
    dom_label = (lambda d: DOMAIN_LABELS_FR[Domain(d)]) if lang == "fr" else (lambda d: DOMAIN_LABELS_EN[d])
    reg_label = (lambda r: REGION_LABELS_FR[Region(r)]) if lang == "fr" else (lambda r: REGION_LABELS_EN[r])
    lvl_label = LEVEL_LABELS_FR if lang == "fr" else LEVEL_LABELS_EN
    cname = lambda iso: getattr(COUNTRIES_BY_ISO[iso], f"name_{lang}")  # noqa: E731

    wanted_domains = set(params.domains)
    in_scope_countries = set(params.countries)
    events: list[Event] = []
    for e in store.events(region=Region(params.region) if params.region else None, since=start):
        if e.occurred_at > end or (in_scope_countries and e.country_iso3 not in in_scope_countries):
            continue
        if wanted_domains and not ({e.domain.value, *(d.value for d in e.secondary_domains)} & wanted_domains):
            continue
        events.append(e)
    by_crit = sorted(events, key=lambda e: (e.criticality, e.occurred_at), reverse=True)
    status = {e.id: event_status(e, store) for e in events}

    def fact(e: Event) -> dict:
        text = f"{e.occurred_at:%Y-%m-%d} — {cname(e.country_iso3)}. {e.title.strip()} [{dom_label(e.domain.value)} · Admiralty {e.admiralty} · {lvl_label[e.level]}]"
        docs = [store.get_document(r.document_id) for r in e.refs]
        figures = casualty_figures([d.title for d in docs if d is not None])
        if len(figures) > 1:  # sources en désaccord : on ne choisit pas, on le dit
            text += " Bilans divergents selon les sources : " + " ; ".join(f"{n:,}".replace(",", " ") for n in figures) + "."
        return _block("fact", text[:MAX_TEXT], sources.refs(e), status=status[e.id], event_id=e.id)

    def heading(text: str) -> dict:
        return _block("heading", text)

    def empty() -> list[dict]:
        msg = ("Aucun événement enregistré dans la base pour ce périmètre et cette période. Cela reflète l'état de la collecte, pas l'absence d'incidents."
               if lang == "fr" else "No event recorded in the database for this scope and period. This reflects the state of collection, not the absence of incidents.")
        return [_block("computed", msg)]

    def ref_str(refs: list[int]) -> str:
        return "".join(f"[{n}]" for n in refs)

    def table(head: list[str], rows: list[list[str]]) -> dict:
        return _block("table", "", head=head, rows=rows)

    auto: dict[str, list[dict]] = {}

    # --- synthèse (calculée) ---
    def summary() -> list[dict]:
        if not events:
            return empty()
        n_high = sum(e.level in ("high", "critical") for e in events)
        n_soc = sum(s == "unconfirmed_social" for s in status.values())
        countries = Counter(e.country_iso3 for e in events)
        doms = Counter(e.domain.value for e in events)
        weight: Counter[str] = Counter()
        for e in events:
            weight[e.country_iso3] += e.criticality
        top = ", ".join(f"{cname(i)} ({v})" for i, v in weight.most_common(3))
        top_d = ", ".join(f"{dom_label(d)} ({v})" for d, v in doms.most_common(3))
        if lang == "fr":
            t1 = (f"Du {start:%Y-%m-%d} au {end:%Y-%m-%d} : {len(events)} événement(s) enregistré(s) dans {len(countries)} pays, "
                  f"dont {n_high} de niveau élevé ou critique ; {n_soc} reposent uniquement sur des réseaux sociaux (non confirmés).")
            return [_block("computed", t1), _block("computed", f"Thèmes les plus fréquents : {top_d}."),
                    _block("computed", f"Pays les plus touchés (somme des criticités) : {top}.")]
        t1 = (f"From {start:%Y-%m-%d} to {end:%Y-%m-%d}: {len(events)} event(s) recorded in {len(countries)} countries, "
              f"of which {n_high} high or critical; {n_soc} rely on social media only (unconfirmed).")
        return [_block("computed", t1), _block("computed", f"Most frequent themes: {top_d}."),
                _block("computed", f"Most affected countries (sum of criticalities): {top}.")]

    def grouped(key, label, per_group: int, max_groups: int) -> list[dict]:
        groups: dict[str, list[Event]] = defaultdict(list)
        for e in by_crit:
            groups[key(e)].append(e)
        if not groups:
            return empty()
        out: list[dict] = []
        ranked = sorted(groups.items(), key=lambda kv: max(e.criticality for e in kv[1]), reverse=True)[:max_groups]
        for k, evs in ranked:
            out.append(heading(label(k)))
            out.extend(fact(e) for e in evs[:per_group])
        return out

    def trends() -> list[dict]:
        if not events:
            return empty()
        span = end - start
        step = timedelta(days=1) if span <= timedelta(days=8) else timedelta(days=7)
        n = max(1, min(10, int(span / step) + 1))
        step = max(step, span / n) if n == 10 else step
        edges = [start + step * i for i in range(n + 1)]
        head = [L["theme"], *[f"{edges[i]:%m-%d}" for i in range(n)], "Total"]
        rows = []
        for d in Domain:
            counts = [sum(1 for e in events if e.domain == d and edges[i] <= e.occurred_at < edges[i + 1] + (timedelta(seconds=1) if i == n - 1 else timedelta(0))) for i in range(n)]
            if any(counts):
                rows.append([dom_label(d.value), *map(str, counts), str(sum(counts))])
        note = (f"Colonnes : début de chaque intervalle de {step.days} jour(s). Nombre d'événements enregistrés par thème." if lang == "fr"
                else f"Columns: start of each {step.days}-day interval. Number of events recorded per theme.")
        return [table(head, rows), _block("computed", note)]

    def scores() -> list[dict]:
        ranking = risk_map(store, now)
        if in_scope_countries:
            ranking = [r for r in ranking if r.iso3 in in_scope_countries]
        elif params.region:
            ranking = [r for r in ranking if COUNTRIES_BY_ISO[r.iso3].region.value == params.region]
        else:
            ranking = ranking[:10]
        conf = {"low": "faible" if lang == "fr" else "low", "medium": "moyenne" if lang == "fr" else "medium", "high": "élevée" if lang == "fr" else "high"}
        rows = [[cname(r.iso3), f"{r.score:.0f}", lvl_label[r.level], f"{r.trend_30d:+.0f}", conf.get(r.confidence, r.confidence), str(r.source_count)] for r in ranking]
        note = ("Scores calculés sur l'ensemble des sources et de la fenêtre d'analyse de la plateforme, indépendamment de la période de ce document. Méthode et pondérations : hypothèses à calibrer."
                if lang == "fr" else "Scores computed over all platform sources and the platform analysis window, independently of this document's period. Method and weights are assumptions to be calibrated.")
        return [table([L["country"], L["score"], L["level"], L["trend"], L["confidence"], L["n_sources"]], rows), _block("computed", note)]

    def risk_rows() -> list[dict]:
        if not events:
            return empty()
        rows = [[f"{e.occurred_at:%Y-%m-%d}", cname(e.country_iso3), e.title.strip()[:160], str(e.probability), str(e.impact),
                 str(e.criticality), lvl_label[e.level], e.admiralty, ref_str(sources.refs(e))] for e in by_crit[: cap * 2]]
        head = [L["date"], L["country"], L["event"], L["probability"], L["impact"], L["criticality"], L["level"], L["admiralty"], L["refs"]]
        return [table(head, rows)]

    def coverage() -> list[dict]:
        cov = coverage_report(store, now)
        out: list[dict] = []
        langs = ", ".join(f"{k} {v:.0%}" for k, v in cov["language_share"].items()) or "–"
        rate = f"{cov['routing_rate']:.0%}" if cov["routing_rate"] is not None else "–"
        regions = ", ".join(f"{reg_label(k)} {v}" for k, v in sorted(cov["events_by_region"].items(), key=lambda kv: -kv[1])) or "–"
        distinct = {r.source_id for e in events for r in e.refs}
        single = sum(1 for e in events if e.source_count == 1)
        if lang == "fr":
            out.append(_block("computed", f"Base de la plateforme sur {cov['window_days']} jours : {cov['documents']} documents collectés, {rate} rattachés à un pays et un thème ; langues : {langs}."))
            out.append(_block("computed", f"Répartition des événements par région sur cette fenêtre : {regions}. Une répartition très inégale signale un biais de couverture."))
            out.append(_block("computed", f"Dans ce document : {len(distinct)} source(s) distincte(s) ; {single} événement(s) sur {len(events)} ne reposent que sur une source."))
        else:
            out.append(_block("computed", f"Platform data over {cov['window_days']} days: {cov['documents']} documents collected, {rate} routed to a country and theme; languages: {langs}."))
            out.append(_block("computed", f"Events by region over this window: {regions}. A very uneven split indicates a coverage bias."))
            out.append(_block("computed", f"In this document: {len(distinct)} distinct source(s); {single} of {len(events)} event(s) rely on a single source."))
        scope = in_scope_countries or ({c.iso3 for c in COUNTRIES_BY_ISO.values() if c.rated and params.region and c.region.value == params.region})
        if scope:
            missing = sorted(cname(i) for i in scope if i not in {e.country_iso3 for e in events})
            if missing:
                shown = ", ".join(missing[:20]) + ("…" if len(missing) > 20 else "")
                out.append(_block("computed", (f"Pays du périmètre sans événement sur la période : {shown}." if lang == "fr" else f"Countries in scope with no event in the period: {shown}.")))
        out.append(_block("computed", cov["note"] if lang == "fr" else "A country with no event is not necessarily free of incidents: first of all it is a coverage signal."))
        out.append(_block("computed", L["events_lang"]))
        return out

    builders = {
        "summary": summary,
        "key_facts": lambda: [fact(e) for e in by_crit[:cap]] or empty(),
        "facts_region": lambda: grouped(lambda e: COUNTRIES_BY_ISO[e.country_iso3].region.value, reg_label, max(2, cap // 2), 5),
        "facts_country": lambda: grouped(lambda e: e.country_iso3, cname, max(2, cap // 3), 15),
        "priority_alerts": lambda: [fact(e) for e in by_crit if e.level in ("high", "critical")][:cap] or empty(),
        "chronology": lambda: [fact(e) for e in sorted(events, key=lambda e: e.occurred_at)[: int(cap * 2.5)]] or empty(),
        "risk_table": risk_rows,
        "country_scores": scores,
        "trends": trends,
    }

    wanted = params.sections if params.sections is not None else template.sections
    sections = []
    for key in template.sections:
        if key not in wanted and key not in ("sources", "coverage"):
            continue
        meta = SECTIONS[key]
        if key in ("sources", "coverage"):
            continue  # construites après les autres : la numérotation des sources doit être complète
        if meta["mode"] == "auto":
            blocks = builders[key]()
        else:
            blocks = [{"id": _bid(), "kind": "estimate", "text": "", "refs": [], "auto": False}]
        sections.append({"key": key, "title": meta[lang], "mode": meta["mode"], "guide": meta[f"guide_{lang}"], "na": False, "blocks": blocks})

    if "coverage" in template.sections:
        sections.append({"key": "coverage", "title": SECTIONS["coverage"][lang], "mode": "auto", "guide": "", "na": False, "blocks": coverage()})
    src_rows = []
    for s in sources.items:
        name = s["source_name"] + (f" ({L['social_note']})" if s["channel"] == "social" else "")
        src_rows.append([str(s["n"]), name, s["reliability"], (s["published_at"] or "")[:10], s["title"][:140], s["url"] or "–"])
    sections.append({
        "key": "sources", "title": SECTIONS["sources"][lang], "mode": "auto", "guide": "", "na": False,
        "blocks": [table(["n", L["source"], L["reliability"], L["published"], L["title"], L["link"]], src_rows)] if src_rows
        else [_block("computed", "Aucune source citée." if lang == "fr" else "No source cited.")],
    })

    warnings = []
    if demo:
        warnings.append("DONNÉES FICTIVES DE DÉMONSTRATION : ce document ne décrit aucun événement réel." if lang == "fr"
                        else "FICTITIOUS DEMONSTRATION DATA: this document describes no real event.")
    if not events:
        warnings.append("Aucun événement dans le périmètre : ne pas diffuser sans vérifier l'état de la collecte." if lang == "fr"
                        else "No event in scope: do not circulate without checking the state of collection.")
    if params.instructions:
        warnings.append("Consignes enregistrées mais non interprétées : aucun modèle de langage n'est branché dans cette version." if lang == "fr"
                        else "Instructions saved but not interpreted: no language model is connected in this version.")

    scope_label = (", ".join(cname(c) for c in params.countries) if params.countries
                   else reg_label(params.region) if params.region else ("Afrique" if lang == "fr" else "Africa"))
    return {
        "template_id": template.id,
        "title": f"{getattr(template, lang)} — {scope_label} — {start:%Y-%m-%d} – {end:%Y-%m-%d}",
        "language": lang,
        "params": {"countries": list(params.countries), "region": params.region, "domains": list(params.domains), "detail": params.detail,
                   "start": start.isoformat(), "end": end.isoformat()},
        "meta": {"recipient": params.recipient, "analyst": params.analyst, "organization": params.organization,
                 "classification": params.classification, "instructions": params.instructions,
                 "built_at": now.isoformat(), "drafter": "template", "demo": demo},
        "sections": sections,
        "sources": sources.items,
        "warnings": warnings,
        "event_ids": [e.id for e in events],
    }
