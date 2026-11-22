"""Jeu de données FICTIF de démonstration (jamais de vraies informations).

Chaque titre est préfixé « [DÉMO] » et rattaché à la source « demo ». Il sert uniquement à
alimenter l'interface locale ; il traverse le vrai pipeline (classification, géocodage, scoring).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Source
from .services import ingest_manual
from .store import InMemoryStore

DEMO_SOURCE = Source("demo", "Données de démonstration (fictives)", "manual", default_reliability="C")

# (jours avant maintenant, titre, texte)
SAMPLES: tuple[tuple[float, str, str], ...] = (
    (0.3, "Coup d'État en cours à Bamako, au Mali", "Des militaires annoncent la dissolution du gouvernement au Mali ; tirs signalés à Bamako."),
    (1.0, "Attaque terroriste près de Ouagadougou au Burkina Faso", "Une attaque terroriste a fait des victimes au Burkina Faso."),
    (2.0, "Attentat contre un convoi militaire au Niger", "Un attentat a visé un convoi de l'armée du Niger près d'Agadez."),
    (3.0, "Manifestations et émeutes à Dakar au Sénégal", "Des manifestations violentes ont éclaté à Dakar, Sénégal ; émeutes signalées."),
    (1.5, "Violences post-électorales en Guinée", "Des violences post-électorales ont éclaté à Conakry, Guinée."),
    (4.0, "Grève générale au Nigeria", "Une grève générale paralyse Lagos, Nigeria."),
    (2.5, "Attaque de bandits armés dans le nord du Nigeria", "Une attaque armée a fait des morts au Nigeria, près de Kano."),
    (5.0, "Affrontements armés au Soudan", "Des affrontements armés opposent des factions à Khartoum, Soudan."),
    (6.0, "Déplacements massifs de population au Soudan du Sud", "Des milliers de déplacés fuient les violences au Soudan du Sud."),
    (3.5, "Épidémie de choléra en République démocratique du Congo", "Une épidémie de choléra se propage en République démocratique du Congo."),
    (7.0, "Sécheresse et insécurité alimentaire en Somalie", "La sécheresse aggrave l'insécurité alimentaire en Somalie."),
    (2.0, "Attaque de piraterie dans le golfe d'Aden", "Une attaque de pirates a visé un navire au large de la Somalie."),
    (8.0, "Inondations meurtrières au Mozambique", "Des inondations ont causé des morts et des déplacés au Mozambique."),
    (4.5, "Manifestations contre la hausse des prix au Kenya", "Des manifestations contre la vie chère ont lieu à Nairobi, Kenya."),
    (9.0, "Tension diplomatique entre l'Algérie et le Maroc", "Une tension diplomatique persiste entre l'Algérie et le Maroc."),
    (10.0, "Ransomware contre une banque en Afrique du Sud", "Une attaque par rançongiciel (ransomware) a visé une banque en Afrique du Sud."),
    (6.5, "Dévaluation et crise de la dette au Ghana", "La crise de la dette et l'inflation s'aggravent au Ghana."),
    (12.0, "Tentative de coup d'État déjouée au Tchad", "Une tentative de coup d'État a été déjouée à N'Djamena, Tchad."),
    (11.0, "Insurrection armée dans le Cabo Delgado au Mozambique", "Des insurgés ont attaqué un village au Mozambique."),
    (5.5, "Grève des enseignants en Côte d'Ivoire", "Une grève des enseignants débute à Abidjan, Côte d'Ivoire."),
    (14.0, "Tensions pré-électorales en Tanzanie", "L'élection approche en Tanzanie ; arrestations d'opposants."),
    (0.8, "Attaque terroriste contre un camp militaire à Gao, au Mali", "Une attaque terroriste a visé un camp de l'armée à Gao, Mali ; des soldats tués."),
    (2.2, "Enlèvement d'otages près de Tombouctou au Mali", "Des otages ont été enlevés près de Tombouctou, Mali, par un groupe armé."),
    (1.2, "Attaque terroriste contre un village du nord du Burkina Faso", "Une attaque terroriste a tué des civils au Burkina Faso."),
    (1.8, "Combats armés à El-Facher au Soudan", "Des affrontements armés ont fait des victimes à El-Facher, Soudan."),
    (20.0, "Attaque contre des civils en Éthiopie", "Une attaque armée a tué des civils en Éthiopie."),
)


def load_demo(store: InMemoryStore, now: datetime | None = None) -> int:
    """Charge le jeu fictif ; renvoie le nombre de documents routés vers un événement."""
    now = now or datetime.now(timezone.utc)
    if store.get_source(DEMO_SOURCE.id) is None:
        store.add_source(DEMO_SOURCE)
    routed = 0
    for days, title, text in SAMPLES:
        outcome = ingest_manual(
            store, title=f"[DÉMO] {title}", text=text, source_id=DEMO_SOURCE.id,
            published_at=now - timedelta(days=days),
        )
        routed += outcome.status == "routed"
    return routed
