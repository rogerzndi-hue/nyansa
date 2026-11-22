"""Classification thématique et typologie d'événements par règles explicables.

Chaque règle = un type d'événement, un impact de base (1-5), un poids et des mots-clés
(FR, EN, PT, AR). Un mot-clé terminé par "*" accepte n'importe quelle fin de mot.
Le résultat liste les mots-clés détectés (preuves) : c'est le socle avant le modèle ML (phase 3),
qui apprendra sur les corrections des analystes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .taxonomy import Domain
from .text import fold

MIN_SCORE = 2  # un seul mot-clé dans le corps du texte ne suffit pas


@dataclass(frozen=True)
class Rule:
    event_type: str
    impact: int
    keywords: tuple[str, ...]
    weight: int = 1


RULES: dict[Domain, tuple[Rule, ...]] = {
    Domain.SEC: (
        Rule("coup_attempt", 5, (
            "coup d'État", "putsch", "tentative de coup", "mutinerie*", "mutiny", "mutinies",
            "coup attempt", "military coup", "staatsstreich", "colpo di stato", "golpe de estado", "golpe militar", "انقلاب",
        ), 2),
        Rule("terror_attack", 4, (
            "attentat*", "terroris*", "jihadis*", "kamikaze*", "suicide bomb*", "suicide attack*",
            "atentado*", "yihadis*", "anschlag", "terroranschlag", "dschihadis*", "إرهاب", "تفجير", "عملية انتحارية",
        ), 2),
        Rule("armed_attack", 4, (
            "attaque*", "attack*", "ataque*", "assaut*", "embuscade*", "ambush*", "emboscada*",
            "insurg*", "rebel*", "airstrike*", "air strike*", "frappe aérienne", "frappes aériennes",
            "affrontements armés", "armed clashes", "angriff*", "attacco", "attacchi", "milicias", "milizie", "milizen", "bewaffnete", "هجوم", "اشتباكات",
        )),
        # Affrontements et groupes armés (cas vus dans la collecte sociale réelle : « Clash between Africa Corps and JNIM »,
        # « Armed Men Kill Over 200 People »). Pas de « clash » ni de « battle » seuls : métaphores fréquentes (« battles measles »).
        Rule("armed_clash", 3, (
            "ground clash*", "clash between", "clashes between", "clashed with", "deadly clash*", "violent clash*",
            "militant*", "gunmen", "armed men", "armed group*", "drone strike*", "loitering munition*", "bandit*", "banditry", "banditisme",
            "jnim", "isis", "iswap", "islamic state", "boko haram", "al-shabaab", "al shabaab", "al-qaeda", "al qaeda",
            "état islamique", "etat islamique", "daech", "enfrentamientos mortales", "enfrentamientos", "scontri armati", "scontri mortali",
            # « affrontements » au pluriel désigne presque toujours des violences ; « affrontement » seul est trop souvent politique ou sportif
            "affrontements", "m23", "wagner", "africa corps", "forces de soutien rapide", "rapid support forces",
        )),
        Rule("kidnapping", 3, (
            "enlèvement*", "kidnap*", "abduct*", "séquestr*", "otage*", "hostage*", "refém",
            "reféns", "rapto*", "sequestro*", "اختطاف", "رهائن",
        ), 2),
        Rule("piracy", 3, ("piraterie", "pirates", "piracy", "pirataria"), 2),
        Rule("trafficking", 2, (
            "trafic de drogue", "trafic d'armes", "trafic d'êtres humains", "traite des êtres humains",
            "narcotrafic*", "drug trafficking", "arms trafficking", "human trafficking", "smuggling",
            "contrebande*", "tráfico de drogas", "tráfico de armas", "contrabando",
            "orpaillage illégal", "illegal mining",
        )),
        Rule("military_deployment", 3, (
            "troop deployment*", "troops deployed", "military deployment*",
            "troop movement*", "movement of troops",
            "military mobilization", "military mobilisation",
            "military forces mobilize*", "military forces mobilise*",
            "deploiement de troupes", "troupes deployees",
            "mouvements de troupes", "mobilisation militaire",
        )),
    ),
    Domain.SOC: (
        Rule("protest", 2, (
            "manifestation*", "manifestant*", "protest", "protests", "protested", "protester", "protesters", "protesting", "protestation*", "protestataire*", "demonstrators", "marche de protestation",
            "sit-in", "contre la vie chère", "vie chère", "cost of living protest*", "manifestações", "manifestantes", "protestas", "proteste", "demonstranten", "manifestanti", "manifestantes", "مظاهرات", "احتجاجات",
        )),
        Rule("riot", 3, ("émeute*", "riot*", "pillage*", "looting", "violences urbaines", "شغب")),
        Rule("strike", 2, (
            "grève*", "general strike", "workers strike", "strike action", "greve*", "إضراب",
            "health workers strike", "doctors strike", "nurses strike", "teachers strike", "on strike", "go on strike",
            "huelga*", "streik*", "sciopero*", "suspend strike", "suspends strike", "suspended strike", "calls off strike", "called off strike",
        )),
        Rule("cross_border_expulsion", 2, (
            "malians expelled from", "malian miners expelled",
            "expels malian miners", "expelled malian miners",
            "expulsion of malian miners", "expulsion de mineurs maliens",
            "expulse des mineurs maliens", "expulsion de ressortissants",
            "expels foreign nationals", "expelled foreign nationals",
            "deports foreign nationals", "expulsion of foreign nationals",
        ), 2),
        Rule("internet_shutdown", 3, (
            "coupure d'internet", "coupure internet", "internet shutdown*", "internet blackout*",
            "internet cut*", "bloqueio da internet", "قطع الانترنت",
            "web censorship", "internet censorship", "internet disruption*", "internet outage*", "perturbation* d'internet", "perturbation* internet",
        ), 2),
        Rule("communal_tension", 3, (
            "tension* communautaire*", "affrontements intercommunautaires", "violences intercommunautaires",
            "communal clashes", "communal violence", "ethnic clashes", "ethnic violence", "agropastora*",
        ), 2),
    ),
    Domain.GOV: (
        Rule("election", 2, (
            "élection*", "electoral*", "elector*", "électeur*", "scrutin*", "présidentielle*",
            "législative*", "référendum*", "ballot*", "eleição", "eleições", "eleitoral",
            "انتخابات",
        )),
        Rule("corruption", 2, (
            "corrupt*", "détournement de fonds", "embezzle*", "malversation*", "bribery",
            "pot-de-vin", "corrupção", "desvio de fundos", "فساد", "اختلاس",
        )),
        # Cas de la collecte réelle : « 500 jours de détention » d'un journaliste en Algérie, « détentions secrètes » au Nigeria,
        # « crimes de guerre » au Mali. Formulations précises : « détenu » seul désigne aussi des actifs détenus.
        Rule("human_rights", 3, (
            "détention arbitraire", "détention provisoire", "jours de détention", "mois de détention", "placé en détention", "détenu politique",
            "prisonnier* politique*", "secret detention*", "arbitrary detention", "detention without trial", "torture", "abuses against civilians", "crimes against civilians", "abus contre des civils", "exactions contre", "droits humains",
            "droits de l'homme", "human rights", "violations des droits", "crimes de guerre", "war crimes", "extrajudicial killing*",
            "exécutions extrajudiciaires", "disparitions forcées", "enforced disappearance*",
        )),
        # judicial_action : uniquement les actions visant des responsables publics ; un procès criminel ordinaire n'est pas un risque de gouvernance.
        # Politique intérieure (décision de l'utilisateur, 2026-10-20). Impact 2 : un titre qui la mentionne passe le seuil, le corps seul non.
        # Pas de « la majorité » seul (« la majorité des Africains ») ni de « coalition » seul (« coalition menée par les États-Unis »).
        Rule("political_tension", 2, (
            "opposition", "opposant*", "majorité présidentielle", "majorité parlementaire", "majorité au pouvoir", "parti au pouvoir",
            "ruling party", "ruling coalition", "coalition d'opposition", "opposition coalition", "gouvernement de coalition",
            "coalition government", "crise politique", "political crisis", "motion de censure", "no-confidence", "no confidence",
            "remaniement", "cabinet reshuffle", "limogeage", "dissolution de l'assemblée", "dissolution du parlement",
            "interim government", "gouvernement intérimaire", "ouster", "création du front", "création d'un front", "front commun", "front politique", "impeachment", "destitution",
        )),
        Rule("judicial_action", 2, ("former president sentenced", "former president convicted", "ex-président condamné", "opposition leader sentenced",
                                     "opposition leader jailed", "opposant condamné", "ministre inculpé", "minister indicted", "perquisition*", "mandat d'arrêt", "arrest warrant", "inculp*", "indicted", "indictment")),
        Rule("constitutional_change", 3, (
            "constitution*", "révision constitutionnelle", "troisième mandat", "third term",
            "transition politique", "transition militaire", "junte*", "junta", "charte de transition",
            "dialogue national", "دستور",
        )),
    ),
    Domain.ECO: (
        Rule("default_devaluation", 4, (
            "défaut de paiement", "défaut souverain", "sovereign default", "debt default",
            "dévalu*", "devalu*", "restructuration de la dette", "debt restructuring",
            "reestruturação da dívida",
        ), 2),
        Rule("inflation_debt", 3, (
            "inflation*", "dette*", "debt*", "endettement*", "surendettement", "inflação",
            "dívida*", "تضخم", "ديون",
        )),
        Rule("shortage", 3, (
            "pénurie*", "shortage*", "escassez", "rupture d'approvisionnement",
            "supply disruption*", "نقص",
        )),
        Rule("sanctions", 3, (
            "sanction*", "embargo*", "gel des avoirs", "asset freeze*", "sanções", "عقوبات",
        )),
        Rule("commodities", 2, (
            "pétrole", "baril*", "brent", "oil price*", "matières premières", "commodity",
            "commodities", "cacao", "cobalt", "lithium", "uranium", "petróleo",
        )),
        Rule("macro_economy", 2, (
            "croissance économique", "pib", "gdp", "fmi", "imf", "banque mondiale", "world bank",
            "taux de change", "exchange rate*", "franc cfa", "currency", "monnaie*", "budget*",
            "taux directeur", "interest rate*", "economic growth", "اقتصاد", "اقتصادي",
            "naira", "cedi", "forex", "foreign exchange",
        )),
    ),
    Domain.GEO: (
        Rule("diplomatic_break", 3, (
            "rupture des relations", "rupture diplomatique", "diplomatic rift", "severs ties",
            "tensions diplomatiques", "diplomatic tensions", "diplomatic row", "brouille diplomatique",
            "rappel de l'ambassadeur", "recalls ambassador", "ambassador recalled",
            "expulse l'ambassadeur", "expels ambassador", "expelled ambassador", "persona non grata",
            "corte de relações", "قطع العلاقات",
        ), 2),
        Rule("border_dispute", 3, (
            "différend frontalier", "conflit frontalier", "border dispute*", "border clashes",
            "fermeture de la frontière", "border closure", "border closed", "closes border",
            "disputa fronteiriça", "نزاع حدودي",
        ), 2),
        Rule("diplomacy", 1, (
            "sommet*", "summit*", "accord de défense", "defence agreement", "defense agreement",
            "accord de coopération", "cooperation agreement", "bilateral*", "bilatéral*", "ambassad*",
            "coopération en matière de défense", "coopération militaire", "military cooperation", "defence cooperation",
            "defense cooperation", "lettre d'intention", "organisation internationale de la francophonie", "oif",
            "diplomat*", "union africaine", "african union", "cedeao", "ecowas", "sadc", "igad",
            "alliance des États du Sahel", "cimeira", "cúpula", "قمة", "الاتحاد الافريقي",
        )),
    ),
    Domain.CYB: (
        Rule("ransomware", 4, ("ransomware", "rançongiciel*", "rancomware", "برامج الفدية"), 2),
        Rule("data_breach", 3, (
            "fuite de données", "data breach*", "data leak*", "violação de dados",
            "vazamento de dados", "تسريب بيانات",
        ), 2),
        Rule("vulnerability", 3, (
            # Jamais « vulnerabilit* » seul : « populations vulnérables », « situation de vulnérabilité » relèvent du social.
            "vulnérabilité informatique", "vulnérabilités informatiques", "vulnérabilité logicielle", "vulnérabilités logicielles",
            "vulnérabilité de sécurité", "vulnérabilités de sécurité", "vulnérabilité critique", "vulnérabilités critiques",
            "software vulnerabilit*", "security vulnerabilit*", "critical vulnerabilit*", "vulnerabilidade de segurança",
            "cve", "zero day", "cisa",
        )),
        Rule("cyber_attack", 3, (
            "cyber*", "ciber*", "ddos", "déni de service", "phishing", "hameçonnage", "malware*",
            "logiciel malveillant", "hackers", "hacking", "hacked", "piratage*",
            "هجوم سيبراني", "اختراق",
        )),
    ),
    Domain.HUM: (
        Rule("epidemic", 4, (
            "épidémi*", "epidemi*", "outbreak*", "choléra", "cholera", "ebola", "marburg", "mpox",
            "paludisme", "malaria", "rougeole", "measles", "lassa", "fièvre jaune", "yellow fever",
            "dengue", "pandémi*", "pandemi*", "surto", "surtos", "epidemia", "cólera", "وباء", "كوليرا",
        ), 2),
        Rule("famine", 4, (
            "famine*", "insécurité alimentaire", "food insecurity", "malnutrition", "starvation in", "starvation crisis", "mass starvation", "risk of starvation",
            "fome", "insegurança alimentar", "hunger", "مجاعة", "سوء التغذية",
        ), 2),
        Rule("flood", 3, (
            "inondation*", "flood*", "cyclone*", "glissement de terrain", "landslide*", "inundação",
            "inundações", "cheias", "ciclone*", "فيضانات",
        )),
        Rule("drought", 3, (
            "sécheresse*", "drought*", "criquet*", "locust*", "gafanhoto*", "جفاف", "el niño",
        )),
        Rule("displacement", 3, (
            "personnes déplacées", "populations déplacées", "déplacés internes",
            "déplacement de population*", "déplacements de population*", "réfugié*", "refugee*",
            "displaced", "displacement", "idps", "deslocados", "refugiados", "نازحين", "لاجئين", "نزوح",
        )),
        Rule("humanitarian_crisis", 3, (
            "crise humanitaire", "humanitarian crisis", "humanitarian situation", "aide humanitaire",
            "humanitarian aid", "ocha", "wfp", "unhcr", "crise humanitária",
        )),
        Rule("major_fire", 3, (
            "fire destroys", "fire destroyed", "fire engulf*",
            "fire tears through", "shops burned down",
            "shops destroyed by fire", "incendie detruit*",
            "incendie a detruit", "incendie ravage*",
            "feu a detruit", "feu ravage*",
        ), 2),
    ),
}

# En cas d'égalité de score entre domaines, le premier de la liste l'emporte.
_DOMAIN_PRIORITY = (Domain.SEC, Domain.SOC, Domain.GOV, Domain.ECO, Domain.CYB, Domain.HUM, Domain.GEO)

EVENT_TYPE_LABELS_FR = {
    "coup_attempt": "Coup d'État ou mutinerie", "terror_attack": "Attentat ou acte terroriste",
    "armed_attack": "Attaque armée", "armed_clash": "Affrontement armé ou groupe armé", "kidnapping": "Enlèvement ou prise d'otages", "piracy": "Piraterie",
    "trafficking": "Trafic ou contrebande", "protest": "Manifestation", "riot": "Émeute ou pillage",
    "strike": "Grève", "internet_shutdown": "Coupure d'internet", "communal_tension": "Tension communautaire",
    "military_deployment": "D\u00e9ploiement ou mouvement militaire",
    "cross_border_expulsion": "Expulsion transfrontali\u00e8re",
    "major_fire": "Incendie majeur",
    "election": "Élection", "corruption": "Corruption", "human_rights": "Droits humains et détentions", "judicial_action": "Action judiciaire visant des responsables", "political_tension": "Tension politique intérieure", "constitutional_change": "Changement constitutionnel ou transition",
    "default_devaluation": "Défaut ou dévaluation", "inflation_debt": "Inflation ou dette", "shortage": "Pénurie",
    "sanctions": "Sanctions ou embargo", "commodities": "Matières premières", "macro_economy": "Conjoncture économique",
    "diplomatic_break": "Rupture diplomatique", "border_dispute": "Différend frontalier", "diplomacy": "Diplomatie",
    "ransomware": "Rançongiciel", "data_breach": "Fuite de données", "vulnerability": "Vulnérabilité",
    "cyber_attack": "Cyberattaque", "epidemic": "Épidémie", "famine": "Famine ou insécurité alimentaire",
    "flood": "Inondation ou catastrophe naturelle", "drought": "Sécheresse ou criquets",
    "displacement": "Déplacement de population", "humanitarian_crisis": "Crise humanitaire",
}


def _compile(keyword: str) -> str:
    marker = "nyansawildcardtoken"
    folded = fold(keyword.replace("*", marker))
    parts = folded.split()
    return r"\s+".join(
        re.escape(part[:-len(marker)]) + r"\w*"
        if part.endswith(marker) else re.escape(part)
        for part in parts
    )


_COMPILED: dict[Domain, tuple[tuple[Rule, re.Pattern[str]], ...]] = {
    domain: tuple(
        (rule, re.compile(r"(?<!\w)(?:" + "|".join(_compile(k) for k in rule.keywords) + r")(?!\w)"))
        for rule in rules
    )
    for domain, rules in RULES.items()
}


@dataclass(frozen=True)
class Classification:
    domain: Domain
    secondary_domains: tuple[Domain, ...]
    event_type: str
    impact: int
    score: int
    evidence: tuple[str, ...]


# Titres qui décrivent une activité courante et non un incident (atelier, formation, réunion,
# investissement…). Hors sécurité, ils plafonnent l'impact : un mot-clé de santé ou d'économie
# ne fait pas à lui seul un événement de risque. Heuristique à faire valider par les analystes.
NEUTRAL_CUES = (
    "atelier*", "formation*", "formes", "formees", "projet de", "séminaire*", "conférence*", "forum*",
    "workshop*", "training", "trained", "seminar*", "conference*", "meeting*", "talks", "discuss*",
    "joins", "advances", "inflows", "investment*", "investisseur*", "woos", "optimistic", "optimiste",
    "signature d'un accord", "signs agreement", "inaugur*",
)
NEUTRAL_IMPACT_CAP = 2
# Titre de procès : les mots de violence du corps (« meurtre », « massacre ») racontent les faits jugés, pas un incident en cours.
_JUDICIAL_TITLE = re.compile(r"(?<!\w)(?:court|trial|verdict|guilty|sentenced|convicted|tribunal|proces|condamn\w*|acquitt\w*)(?!\w)")
_NEUTRAL = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(c[:-1]) + r"\w*" if c.endswith("*") else re.escape(c) for c in (fold(c) for c in NEUTRAL_CUES)
) + r")(?!\w)")


_MILITARY_EXERCISE_TITLE = re.compile(
    r"(?<!\w)(?:military exercise|joint exercise|military drill|"
    r"exercise militaire|exercice militaire|manoeuvre militaire|"
    r"manoeuvres militaires)(?!\w)"
)


def classify(title: str, body: str = "") -> Classification | None:
    """Retourne la classification, ou None si les indices sont insuffisants (file « à qualifier »)."""
    folded_title, folded_body = fold(title), fold(body)
    domain_scores: dict[Domain, int] = {}
    best_rule: dict[Domain, tuple[int, Rule]] = {}
    title_hit: set[Domain] = set()
    evidence: dict[Domain, list[str]] = {}

    for domain, compiled in _COMPILED.items():
        for rule, pattern in compiled:
            if (
                rule.event_type == "military_deployment"
                and _MILITARY_EXERCISE_TITLE.search(folded_title)
            ):
                continue
            in_title = [m.group(0) for m in pattern.finditer(folded_title)]
            in_body = [m.group(0) for m in pattern.finditer(folded_body)]
            score = rule.weight * (2 * len(in_title) + len(in_body))
            if score == 0:
                continue
            if in_title:
                title_hit.add(domain)
            domain_scores[domain] = domain_scores.get(domain, 0) + score
            evidence.setdefault(domain, []).extend(dict.fromkeys(in_title + in_body))
            if domain not in best_rule or score > best_rule[domain][0]:
                best_rule[domain] = (score, rule)

    if Domain.SEC in domain_scores and Domain.SEC not in title_hit and _JUDICIAL_TITLE.search(folded_title):
        del domain_scores[Domain.SEC]  # procès : la sécurité ne vient que du récit des faits dans le corps
    if not domain_scores:
        return None
    primary = max(_DOMAIN_PRIORITY, key=lambda d: (domain_scores.get(d, 0), -_DOMAIN_PRIORITY.index(d)))
    top = domain_scores[primary]
    if top < MIN_SCORE:
        return None
    rule = best_rule[primary][1]
    secondary = tuple(
        d for d in _DOMAIN_PRIORITY
        if d != primary and domain_scores.get(d, 0) >= max(MIN_SCORE, top / 2)
    )
    impact = rule.impact
    notes: list[str] = []
    neutral = _NEUTRAL.search(folded_title)
    if neutral and primary is not Domain.SEC and impact > NEUTRAL_IMPACT_CAP:
        impact = NEUTRAL_IMPACT_CAP
        notes.append(f"contexte non incident : « {neutral.group(0)} »")
    return Classification(
        domain=primary,
        secondary_domains=secondary,
        event_type=rule.event_type,
        impact=impact,
        score=top,
        evidence=tuple(dict.fromkeys(evidence[primary]))[:6] + tuple(notes),
    )
