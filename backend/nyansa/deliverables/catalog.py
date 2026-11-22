"""Catalogue des 25 livrables et dictionnaire des sections (FR/EN).

Une section est soit « auto » (calculée à partir des événements de la base, jamais réécrite : chaque fait y porte
ses sources), soit « analyste » (à rédiger ; l'analyste étiquette chaque paragraphe fait, estimation ou hypothèse).
"""

from __future__ import annotations

from dataclasses import dataclass

LANGUAGES = ("fr", "en")
DETAIL_LEVELS = {"brief": 5, "standard": 10, "full": 25}  # nombre maximal d'éléments par liste
CLASSIFICATIONS = ("PUBLIC", "INTERNE", "CONFIDENTIEL", "RESTREINT")

# (clé) -> (mode, FR, EN, consigne FR, consigne EN)
_S = {
    # --- sections calculées -----------------------------------------------------------------------------------
    "summary": ("auto", "Synthèse exécutive", "Executive summary", "", ""),
    "key_facts": ("auto", "Faits majeurs", "Key facts", "", ""),
    "facts_region": ("auto", "Faits majeurs par région", "Key facts by region", "", ""),
    "facts_country": ("auto", "Faits majeurs par pays", "Key facts by country", "", ""),
    "priority_alerts": ("auto", "Alertes prioritaires", "Priority alerts", "", ""),
    "chronology": ("auto", "Chronologie", "Chronology", "", ""),
    "risk_table": ("auto", "Tableau des risques (probabilité × impact)", "Risk table (likelihood × impact)", "", ""),
    "country_scores": ("auto", "Scores de risque par pays", "Country risk scores", "", ""),
    "trends": ("auto", "Évolution des événements", "Event trends", "", ""),
    "sources": ("auto", "Sources", "Sources", "", ""),
    "coverage": ("auto", "Limites et biais de couverture", "Limits and coverage bias", "", ""),
    # --- sections rédigées par l'analyste ---------------------------------------------------------------------
    "object": ("analyst", "Objet", "Subject", "Une phrase : de quoi traite ce document et pour qui.", "One sentence: what this document covers and for whom."),
    "objective": ("analyst", "Objectif", "Objective", "Ce que l'évaluation doit permettre de décider.", "What the assessment must help decide."),
    "scope": ("analyst", "Périmètre", "Scope", "Zones, populations, actifs, itinéraire ou période couverts, et exclusions.", "Areas, people, assets, route or period covered, and exclusions."),
    "methodology": ("analyst", "Méthodologie et limites", "Methodology and limitations", "Préciser les sources réellement consultées, les critères de sélection, la période, les règles de corroboration et les limites de couverture. Ne pas déduire l'absence de risque d'une absence de données.", "Specify the sources actually consulted, selection criteria, period, corroboration rules and coverage limits. Do not infer absence of risk from absence of data."),
    "context": ("analyst", "Contexte", "Context", "Éléments de fond nécessaires pour comprendre les faits ; chaque fait cité renvoie à une source [n].", "Background needed to understand the facts; each fact cited must carry a source [n]."),
    "actors": ("analyst", "Acteurs", "Actors", "Acteurs étatiques et non étatiques, rôle, intentions connues (fait) ou supposées (hypothèse).", "State and non-state actors, role, known (fact) or assumed (hypothesis) intentions."),
    "areas": ("analyst", "Zones affectées", "Affected areas", "Localités, axes, infrastructures ; précision de la localisation (exacte, approximative).", "Localities, routes, infrastructure; location precision (exact, approximate)."),
    "threat_analysis": ("analyst", "Analyse des menaces", "Threat analysis", "Nature, capacité, intention, tendance. Étiqueter chaque paragraphe : fait, estimation ou hypothèse.", "Nature, capability, intent, trend. Label each paragraph: fact, estimate or hypothesis."),
    "vulnerabilities": ("analyst", "Vulnérabilités", "Vulnerabilities", "Points faibles de la cible ou de la zone exposés aux menaces identifiées.", "Weaknesses of the target or area exposed to the identified threats."),
    "impacts": ("analyst", "Impacts opérationnels", "Operational impacts", "Conséquences sur les personnes, activités, déplacements, continuité.", "Consequences for people, activities, movements, continuity."),
    "mitigation": ("analyst", "Mesures d'atténuation", "Mitigation measures", "Mesures existantes et proposées, responsable, délai.", "Existing and proposed measures, owner, timeline."),
    "residual_risk": ("analyst", "Risque résiduel", "Residual risk", "Niveau de risque après mesures, et acceptation par le décideur.", "Risk level after measures, and acceptance by the decision-maker."),
    "outlook": ("analyst", "Perspectives", "Outlook", "Évolution probable à court terme ; signaler les incertitudes et les signaux à surveiller.", "Likely short-term evolution; flag uncertainties and signals to watch."),
    "scenarios": ("analyst", "Scénarios", "Scenarios", "Tendanciel, favorable, défavorable, rupture ; probabilité estimée et signaux d'alerte de chacun.", "Baseline, favourable, adverse, disruption; estimated likelihood and warning signs for each."),
    "evolution": ("analyst", "Évolution attendue", "Expected evolution", "À 24-72 h : ce qui est probable, ce qui est possible, ce qui est surveillé.", "At 24-72 h: what is likely, what is possible, what is being monitored."),
    "recommendations": ("analyst", "Recommandations", "Recommendations", "Actions concrètes, priorisées, avec responsable. Une recommandation est une estimation, pas un fait.", "Concrete, prioritised actions with an owner. A recommendation is an estimate, not a fact."),
    "immediate_recs": ("analyst", "Recommandations immédiates", "Immediate recommendations", "Actions à engager dans les heures qui viennent.", "Actions to take within the next hours."),
    "conclusion": ("analyst", "Conclusion", "Conclusion", "Réponse à l'objectif, niveau de confiance global.", "Answer to the objective, overall confidence level."),
    "strategic_summary": ("analyst", "Synthèse stratégique", "Strategic summary", "Message principal en quelques lignes pour le décideur.", "Main message in a few lines for the decision-maker."),
    "interests": ("analyst", "Intérêts et enjeux", "Interests and stakes", "Ce que chaque acteur cherche à obtenir ou protéger.", "What each actor seeks to gain or protect."),
    "national_dynamics": ("analyst", "Dynamiques nationales", "National dynamics", "Équilibres politiques, économiques et sécuritaires internes.", "Internal political, economic and security balances."),
    "regional_impl": ("analyst", "Implications régionales", "Regional implications", "Effets sur les pays voisins et les organisations régionales.", "Effects on neighbouring countries and regional organisations."),
    "international_impl": ("analyst", "Implications internationales", "International implications", "Effets sur les partenaires, marchés et organisations internationales.", "Effects on partners, markets and international organisations."),
}

SECTIONS = {k: dict(mode=v[0], fr=v[1], en=v[2], guide_fr=v[3], guide_en=v[4]) for k, v in _S.items()}


@dataclass(frozen=True)
class Template:
    id: str
    number: int
    fr: str
    en: str
    sections: tuple[str, ...]
    days: int  # période proposée par défaut
    scope: str = "any"  # any | country | region : périmètre attendu
    domains: tuple[str, ...] = ()  # thèmes proposés par défaut (vide : tous)


def _t(i, n, fr, en, sections, days, scope="any", domains=""):
    return Template(i, n, fr, en, tuple(sections.split()) + ("sources", "coverage"), days, scope, tuple(domains.split()))


_TEMPLATES = [
    _t("daily_brief", 1, "Bulletin quotidien de sécurité", "Daily Security Brief", "summary facts_region facts_country priority_alerts recommendations", 1),
    _t("weekly_report", 2, "Bulletin hebdomadaire", "Weekly Security Report", "summary key_facts facts_region priority_alerts outlook recommendations", 7),
    _t("monthly_report", 3, "Rapport mensuel", "Monthly Security Report", "summary key_facts facts_region country_scores trends outlook recommendations", 30),
    _t("flash_alert", 4, "Alerte flash", "Flash Security Alert", "key_facts impacts immediate_recs evolution", 3),
    _t("info_note", 5, "Note d'information sécuritaire", "Security Information Note", "object key_facts areas impacts", 7),
    _t("analysis_note", 6, "Note d'analyse sécuritaire", "Security Analysis Note", "object summary context key_facts actors areas threat_analysis impacts outlook recommendations", 30),
    _t("sitrep", 7, "Rapport de situation (SITREP)", "SITREP", "summary chronology areas impacts immediate_recs evolution", 3),
    _t("incident_report", 8, "Rapport d'incident", "Security Incident Report", "object chronology actors areas impacts mitigation recommendations", 7),
    _t("risk_assessment", 9, "Évaluation des risques sécuritaires", "Security Risk Assessment", "objective scope methodology threat_analysis vulnerabilities risk_table mitigation residual_risk conclusion", 90),
    _t("geopolitical", 10, "Analyse géopolitique", "Geopolitical Risk Analysis", "strategic_summary context actors interests national_dynamics regional_impl international_impl scenarios threat_analysis outlook", 90, domains="GEO GOV ECO"),
    _t("country_profile", 11, "Profil sécuritaire pays", "Country Security Profile", "summary context country_scores key_facts actors threat_analysis vulnerabilities outlook recommendations", 90, scope="country"),
    _t("threat_assessment", 12, "Évaluation de la menace", "Threat Assessment", "object actors threat_analysis vulnerabilities risk_table impacts mitigation conclusion", 90),
    _t("trend_analysis", 13, "Analyse des tendances", "Security Trend Analysis", "summary methodology trends key_facts outlook", 90),
    _t("intelligence_brief", 14, "Note de renseignement", "Intelligence Brief", "summary key_facts actors threat_analysis outlook recommendations", 7),
    _t("mission_risk", 15, "Évaluation des risques de mission", "Mission Security Risk Assessment", "objective scope methodology threat_analysis vulnerabilities risk_table mitigation residual_risk recommendations conclusion", 30),
    _t("route_assessment", 16, "Évaluation sécuritaire d'itinéraire", "Route Security Assessment", "objective scope areas key_facts threat_analysis mitigation recommendations", 30),
    _t("travel_advisory", 17, "Note de sûreté des déplacements", "Travel Security Advisory", "summary key_facts areas recommendations", 14, scope="country"),
    _t("election_monitoring", 18, "Rapport de veille électorale", "Election Security Monitoring Report", "summary context key_facts actors threat_analysis outlook recommendations", 30, domains="GOV"),
    _t("unrest_monitoring", 19, "Rapport de veille des manifestations", "Civil Unrest Monitoring Report", "summary chronology facts_country actors areas impacts outlook recommendations", 14, domains="SOC"),
    _t("armed_conflict", 20, "Rapport sur les conflits armés", "Armed Conflict Assessment", "summary context actors areas chronology threat_analysis impacts outlook recommendations", 30, domains="SEC"),
    _t("security_outlook", 21, "Analyse prospective", "Security Outlook", "summary trends scenarios outlook recommendations", 90),
    _t("early_warning", 22, "Note d'alerte précoce", "Early Warning Note", "summary key_facts threat_analysis outlook recommendations", 14),
    _t("crisis_report", 23, "Rapport de crise", "Crisis Monitoring Report", "summary context chronology actors areas impacts mitigation immediate_recs evolution", 7),
    _t("executive_brief", 24, "Note stratégique pour décideurs", "Executive Security Brief", "summary key_facts methodology impacts recommendations conclusion", 14),
    _t("regional_assessment", 25, "Rapport régional de sécurité", "Regional Security Assessment", "summary facts_country country_scores threat_analysis regional_impl outlook recommendations", 30, scope="region"),
]
TEMPLATES = {t.id: t for t in _TEMPLATES}
assert len(TEMPLATES) == 25

# Libellés d'interface et de rendu.
LABELS = {
    "fr": {
        "fact": "Fait", "computed": "Calcul", "estimate": "Estimation", "hypothesis": "Hypothèse",
        "confirmed": "Confirmé (2 sources distinctes ou plus)", "single_source": "Source unique",
        "unconfirmed_social": "Non confirmé (réseaux sociaux seuls)",
        "draft": "Brouillon", "in_review": "En validation", "validated": "Validé", "archived": "Archivé",
        "watermark": "BROUILLON — NON VALIDÉ", "page": "Page", "version": "Version", "period": "Période",
        "analyst": "Analyste", "organization": "Organisation", "recipient": "Destinataire", "classification": "Confidentialité",
        "status": "Statut", "generated": "Généré le", "to_write": "À rédiger par l'analyste", "na": "Sans objet",
        "date": "Date", "country": "Pays", "event": "Événement", "probability": "Probabilité", "impact": "Impact",
        "criticality": "Criticité", "level": "Niveau", "admiralty": "Admiralty", "refs": "Sources", "theme": "Thème",
        "score": "Score /100", "trend": "Tendance 30 j", "confidence": "Confiance", "n_sources": "Nb sources",
        "source": "Source", "reliability": "Fiabilité", "published": "Publié", "title": "Titre", "link": "Lien",
        "social_note": "publication sociale, non vérifiée",
        "events_lang": "Titres et résumés des événements : langue d'origine des sources.",
    },
    "en": {
        "fact": "Fact", "computed": "Computed", "estimate": "Estimate", "hypothesis": "Hypothesis",
        "confirmed": "Confirmed (2+ distinct sources)", "single_source": "Single source",
        "unconfirmed_social": "Unconfirmed (social media only)",
        "draft": "Draft", "in_review": "In review", "validated": "Validated", "archived": "Archived",
        "watermark": "DRAFT — NOT VALIDATED", "page": "Page", "version": "Version", "period": "Period",
        "analyst": "Analyst", "organization": "Organisation", "recipient": "Recipient", "classification": "Classification",
        "status": "Status", "generated": "Generated on", "to_write": "To be written by the analyst", "na": "Not applicable",
        "date": "Date", "country": "Country", "event": "Event", "probability": "Likelihood", "impact": "Impact",
        "criticality": "Criticality", "level": "Level", "admiralty": "Admiralty", "refs": "Sources", "theme": "Theme",
        "score": "Score /100", "trend": "30-day trend", "confidence": "Confidence", "n_sources": "No. of sources",
        "source": "Source", "reliability": "Reliability", "published": "Published", "title": "Title", "link": "Link",
        "social_note": "social media post, unverified",
        "events_lang": "Event titles and summaries are shown in the original language of the sources.",
    },
}

KINDS = ("fact", "computed", "estimate", "hypothesis")  # types de paragraphe ; « heading » et « table » sont structurels


def catalog(lang: str = "fr") -> list[dict]:
    """Catalogue pour l'interface."""
    lang = lang if lang in LANGUAGES else "fr"
    return [
        {
            "id": t.id, "number": t.number, "title": getattr(t, lang), "title_fr": t.fr, "title_en": t.en,
            "days": t.days, "scope": t.scope, "domains": list(t.domains),
            "sections": [{"key": k, "title": SECTIONS[k][lang], "mode": SECTIONS[k]["mode"]} for k in t.sections],
        }
        for t in _TEMPLATES
    ]
