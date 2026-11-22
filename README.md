# Nyansa (nom de travail du code : ThreatLens)

Plateforme de renseignement sur les risques et menaces pour les 54 pays d'Afrique.
État : squelette MVP (phase 2) : backend + tableau de bord web local sur données fictives.

## Contenu

| Chemin | Rôle |
|---|---|
| `backend/nyansa/` | Cœur : taxonomie, gazetteer des pays, classification, scoring explicable, ingestion RSS/Atom, pipeline, API FastAPI |
| `backend/tests/` | Tests unitaires (core, pipeline, API) |
| `backend/nyansa/devserver.py`, `demo.py` | Serveur local sans dépendance + jeu de données FICTIF de démonstration |
| `web/` | Tableau de bord sombre (HTML/CSS/JS natif, sans build) : carte des 54 pays (satellite ou fond uni, frontières), filtres région/thème/source, détail pays explicable |
| `db/schema.sql` | Schéma PostgreSQL 16 + PostGIS |
| `db/seed_countries.sql` | Pays (généré par `scripts/gen_seed.py`) |
| `config/sources.json` | Sources RSS du pilote (5 actives, 4 inactives) ; `sources.example.json` : modèle |
| `backend/nyansa/runner.py` | Collecte des sources actives, rapport par source |

## Tests

Sans dépendance externe (les tests API sont ignorés si FastAPI/httpx sont absents) :

```bash
cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -t . -v
```

Avec les dépendances de dev :

```bash
pip install -e .[dev]
pytest
```

## Voir le tableau de bord (sans aucune dépendance)

```bash
cd backend && python3 -m nyansa.devserver   # http://127.0.0.1:8000
```

Données fictives uniquement, écoute sur 127.0.0.1, pas d'authentification. Vue partageable : `/?region=WEST&domain=SEC#MLI`.
### Mode réel (flux RSS)

```bash
cd backend && python3 -m nyansa.devserver --live ../config/sources.json [--interval 15]
```

Aucune donnée fictive : seules les sources `active: true` sont lues (au démarrage puis toutes les 15 min). L'état de chaque source (OK/échec, documents lus, rattachés, doublons, écartés) s'affiche en bas du tableau de bord. Il faut un poste qui accède à Internet. On ne conserve que titre, résumé tronqué (1 000 caractères) et lien. Les CGU de toutes les sources sont à valider avant exploitation durable (voir `license_note`).

La carte a deux fonds, au choix : **Satellite** (NASA GIBS « Blue Marble », données ouvertes) et **Map** (fond uni). Dans les deux, les pays sont dessinés avec leurs **frontières** (Natural Earth via le paquet `world-atlas`, domaine public, tracé indicatif sans valeur juridique) et colorés selon leur niveau de risque ; un point n'apparaît que pour un pays sans polygone ou si les frontières ne chargent pas. Les fonds et frontières sont chargés depuis le navigateur (Internet requis ; `?map=map` ouvre directement le mode Map). La carte se navigue : molette ou boutons +/− pour zoomer, glisser pour se déplacer, double-clic pour agrandir, ⌂ pour la vue d'ensemble ; les capitales apparaissent à partir d'un zoom moyen, les autres villes en zoomant davantage. Chaque événement affiche aussi les mots-clés qui ont déclenché son classement (« Indices ») et un bouton par source vers l'original : **Lire ↗** (article de presse) ou **Post ↗** (publication sociale), dans le tableau comme dans « Contributions au score » (liens http/https uniquement). Le tableau affiche 40 lignes puis « Afficher … de plus ».

### Réseaux sociaux

Sources de canal `"channel": "social"` dans `config/sources.json` : flux RSS publics, sans clé d'API. Actifs par défaut : hashtags Mastodon (#Sahel, #Mali, #BurkinaFaso) ; inactifs : subreddits Nigeria et Ghana (conditions Reddit à lire). Garde-fous :

- fiabilité **E** (message individuel non vérifié) : un événement issu uniquement de réseaux sociaux porte l'étiquette « non confirmé » et une probabilité faible ;
- vie privée : @pseudonymes et adresses e-mail retirés avant stockage, texte tronqué à 400 caractères ;
- le filtre « Sources » (Toutes / Médias / Réseaux sociaux) agit sur la liste et les indicateurs ; les scores de la carte restent calculés sur toutes les sources.

X/Twitter, Facebook, Instagram, TikTok, WhatsApp et Telegram ne sont pas branchés : pas de flux gratuit et autorisé. Les URL des flux sociaux n'ont pas pu être testées depuis l'environnement de développement (réseau bloqué) : l'état de chaque flux (OK ou échec) s'affiche en bas du tableau de bord.

### Performance

Serveur : réponses compressées (gzip), ETag (304 si inchangé), calculs mis en cache tant que les données ne changent pas. Navigateur : éléments de carte réutilisés (déplacer ou zoomer ne recrée plus les polygones, villes et noms), sélection d'un pays sans reconstruire tableau ni classement, rafraîchissement ignoré si les données n'ont pas changé et suspendu quand l'onglet est masqué, frontières chargées en deux temps (110 m léger, puis 50 m détaillé), préconnexion aux serveurs de fonds de carte.

Tests du front : `cd web && node --test js/*.test.mjs`.

## Surveillance des réseaux sociaux (page « Réseaux sociaux », `/social.html`)

Trois types de veille : **hashtag** et **compte public** (collecte : flux RSS publics d'une instance Mastodon, la veille devient une source de canal « social », fiabilité E) et **mot-clé** (aucune collecte : repère le mot dans les publications déjà collectées). Un pays ou des catégories peuvent restreindre l'affichage d'une veille. Veilles initiales possibles via `config/watches.json` (modèle : `watches.example.json`, option `--watches`) ; en mode réel, celles créées dans l'interface sont enregistrées dans `--data-dir/watches.json`. Ajouter une veille lance une collecte immédiate.

Chaque publication passe par le pipeline commun (pays, thème, événement) puis reçoit un statut : **corroboré** (l'événement est aussi rapporté par au moins une source média ; des publications sociales ne se corroborent jamais entre elles), **NON CONFIRMÉ** (publications sociales seules) ou **à qualifier** (pays ou thème non reconnu, raison affichée). Les publications cliquables renvoient vers l'original ; @pseudonymes et e-mails sont retirés avant stockage.

- **Droits** : lecture pour tous ; créer, mettre en pause ou supprimer une veille : analyste, responsable de veille, admin (identité déclarative). Écritures protégées comme les autres (en-tête obligatoire, contrôle d'hôte et d'origine).
- **Sécurité** : le nom d'instance doit être un nom de domaine public (ni IP, ni nom local) et, avant chaque collecte, son adresse résolue doit être publique. Limite connue : une nouvelle résolution reste théoriquement possible entre ce contrôle et la requête.
- **Alertes sur les signaux sociaux** : ajoutez une règle `include_social: true` dans `config/alert_rules.json` (l'alerte porte alors « NON CONFIRMÉ »).
- **Limites** : seul Mastodon (flux publics) ; X, Facebook, Instagram, TikTok, WhatsApp et Telegram ne sont pas branchés ; la corroboration repose sur des titres proches (même pays, même thème, 48 h) : une dépêche très différemment formulée ne sera pas rapprochée ; les flux réels n'ont pas pu être testés depuis l'environnement de développement (réseau bloqué), l'état de chaque flux s'affiche dans la liste des veilles.

## Livrables (page « Livrables », `/deliverables.html`)

25 modèles (alerte flash, bulletins, notes, SITREP, évaluations, profils pays, analyses prospectives…), chacun avec sa structure de sections. Un brouillon est **généré à partir des données de la plateforme, sans IA** : chaque bloc porte son statut (FAIT sourcé, CALCUL, ESTIMATION, HYPOTHÈSE, ANALYSE, À RÉDIGER) et un renvoi [n] vers la table des sources ; les sections d'analyse sont à rédiger par l'analyste. Les blocs générés sont verrouillés (supprimables, non modifiables).

- **Workflow** : brouillon → soumis → validé (ou rejeté avec commentaire) → archivé ; la soumission est bloquée tant qu'un point bloquant subsiste (bouton « Contrôler ») ; historique de versions avec comparaison ; un document validé n'est plus modifiable (on le duplique).
- **Exports** : HTML (aperçu et impression, sans script), Word `.docx` (sans dépendance), PDF A4 (couverture, filigrane brouillon ou données fictives, pagination, liens cliquables). Le PDF demande `pip install reportlab` ; sans lui, l'API répond 501 et propose l'export HTML puis « Imprimer > Enregistrer en PDF ». L'arabe n'est pas rendu dans le PDF (caractères absents de la police remplacés par « ? »).
- **Persistance** : en mode réel, les livrables sont enregistrés dans `--data-dir` (défaut `./data`, à sauvegarder) ; en démonstration, ils restent en mémoire.
- **Droits** : l'identité (nom, rôle) est **déclarative**, sans authentification : elle teste la séparation des rôles, elle ne protège rien. Écritures protégées contre les requêtes d'autres sites (en-tête obligatoire, contrôle d'hôte et d'origine).
- **Pas encore fait** : assistance IA (corriger, résumer, traduire : `llm: false`), cartes et graphiques dans les documents, rapports programmés, envoi automatique WhatsApp/e-mail (les boutons ouvrent le partage manuel), contrôle d'accès par organisation.

## Alertes (carte « Alertes » du tableau de bord)

Règles dans `config/alert_rules.json` (modèle : `alert_rules.example.json` ; option `--alert-rules`) : niveau minimal, pays, région, thèmes, mots-clés, réseaux sociaux acceptés ou non, webhook, e-mails, plage de silence (UTC), limite par heure. Sans fichier : une règle prudente (élevé ou critique, médias, application seulement). Une règle invalide empêche le démarrage avec un message précis.

- **Pas de répétition** : un incident ne déclenche qu'une alerte par règle ; une aggravation (niveau supérieur) produit une alerte « escalade ». L'empreinte de l'incident est stable d'un redémarrage à l'autre (pays, thème, mots du titre), l'historique est enregistré dans `--data-dir/alerts.json` en mode réel.
- **Fiabilité** : une publication sociale isolée ne déclenche rien sauf règle `include_social` ; l'alerte porte alors « NON CONFIRMÉ ». Chaque alerte indique le statut de vérification, les sources cliquables et un lien de recherche du pays sur OpenStreetMap (localisation approximative : pays seulement).
- **Notifications externes** (mode réel uniquement, jamais en démonstration) : webhook (JSON) et e-mail, 3 tentatives, plage de silence sauf niveau critique, limite horaire par règle. L'e-mail demande `NYANSA_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` dans l'environnement (jamais dans le code ni dans les règles). L'URL d'un webhook n'est jamais affichée en entier (elle peut contenir un jeton) ; elle doit venir d'un fichier que vous contrôlez.
- **Acquittement** : bouton dans l'interface (rôles analyste, validateur, responsable, admin ; identité déclarative).
- **Pas encore fait** : éditeur de règles dans l'interface, push Android, WhatsApp, zones géographiques et géorepérage, SMTP et webhook non testés contre un vrai serveur depuis l'environnement de développement (tests avec transports simulés et un récepteur HTTP local).

## Lancer l'API (poste de développement uniquement)

```bash
pip install -e .
cd backend
NYANSA_AUTH_MODE=dev uvicorn nyansa.api:app --reload
# requêtes : en-tête X-Dev-Role: analyst | validator | manager | executive | admin
```

Sans `NYANSA_AUTH_MODE=dev`, toute route protégée répond 501 (OIDC + MFA non branchés). Ne jamais activer le mode dev hors poste local.

Variable optionnelle : `NYANSA_SOURCES=config/sources.json` pour charger des sources.

## Limites connues

- Couche API (`api.py`) non exécutée à ce jour : FastAPI n'a pas pu être installé dans l'environnement de développement. À valider dès que les dépendances sont disponibles.
- Stockage en mémoire (non persistant) ; le magasin PostgreSQL reste à écrire.
- Paramètres de scoring (demi-vie 14 j, saturation, pondérations) et correspondance Admiralty → probabilité : hypothèses initiales à calibrer avec les analystes.
- Gazetteer arabe partiel (pas de préfixes collés).
- Front actuel = version sans build, jetable : navigation de carte écrite à la main, tests d'interaction faits par pilotage de Chromium (non automatisés dans le dépôt). À remplacer par React/TypeScript + MapLibre dès que npm est accessible. Frontières et imagerie : chargement non testé depuis l'environnement de développement (réseau bloqué), testé avec des données simulées.
- Corroboration : une publication sociale compte comme une source distincte dans la crédibilité Admiralty d'un événement (2 sources distinctes = « probablement vraie »). Or les réseaux relaient souvent la presse : à décider si seules les sources médias doivent compter pour relever la crédibilité.
- Mesures de performance faites avec des frontières factices (réseau bloqué) : elles prouvent la baisse du travail sur le DOM, pas le gain exact avec les vraies frontières.
- OIDC/MFA, ML v1 : à venir.

## Diagnostic d'un flux qui ne rapporte rien

- Le panneau « Sources » (tableau de bord) affiche le **cumul en base** (documents, rattachés, à qualifier) et la **dernière collecte** (lus, nouveaux, ignorés). Après le 2e cycle tout est « déjà connu » : seul le cumul renseigne sur l'état réel.
- La page Réseaux sociaux indique pour chaque veille le nombre d'éléments lus. « Flux lu, mais aucune publication renvoyée » signifie que l'instance a répondu sans contenu : essayez un hashtag plus actif ou une autre instance.
- Les éléments RSS **sans `<title>`** (fréquent sur Mastodon) reçoivent un titre de secours tiré du début du texte ; ils étaient auparavant écartés en silence. Cette correction n'a pas pu être vérifiée sur un vrai flux depuis l'environnement de développement (réseau bloqué) : c'est à confirmer sur votre machine.

## Réduction du bruit des publications sociales

- Page Réseaux sociaux : le filtre Statut est sur **« Pertinentes (hors à qualifier) »** par défaut ; le nombre de publications « à qualifier » masquées est indiqué, et « Tous » les affiche.
- Une même publication reçue par plusieurs flux (#Mali, #Sahel, #BurkinaFaso) n'apparaît qu'une fois (« aussi dans : … »). Deux messages différents ne fusionnent jamais, même avec la même adresse.
- Les hashtags en CamelCase des publications sociales sont éclatés avant analyse (« #BurkinaFaso » devient « Burkina Faso ») : le texte conservé est donc modifié sur ce point. Les hashtags en minuscules sont inchangés.
- Une publication d'un hashtag n'est **jamais** rattachée à un pays par simple déduction du hashtag : sans pays reconnu dans le texte, elle reste « à qualifier ».
- Limites connues : un mot isolé comme « coup » ne suffit pas à classer une publication (trop ambigu) ; d'anciens articles repostés apparaissent avec la date du repost.

## Rattachement à un pays et crédibilité (corrections après la première alerte flash réelle)

- **Titre sur un pays hors Afrique** : si le titre cite un pays hors Afrique (Bangladesh, Chine, États-Unis…) et aucun pays africain, l'article n'est rattaché à un pays africain que si celui-ci pèse au moins 3 mentions dans le corps du texte. Sinon il part « à qualifier » (raison : « titre sur un pays hors Afrique »). Heuristique : quelques articles légitimes (ex. prêt chinois évoquant brièvement un pays africain) iront aussi en qualification manuelle. La liste des pays étrangers est partielle (`_FOREIGN_NAMES`, geo.py).
- **Crédibilité Admiralty** : seules les sources **médias** distinctes la relèvent (2 sources → 2, 3 ou plus → 1). Les publications de réseaux sociaux ne comptent pas, même relayées par plusieurs flux : un événement uniquement social reste à « E3 ».
- **Livrables** : un même lien reçu par plusieurs flux porte un seul numéro dans la liste des sources.

## Plateformes de la surveillance sociale (état au 19 octobre 2026)

| Plateforme | Statut | Détail |
|---|---|---|
| Mastodon | Vérifié en réel | Hashtags et comptes publics (flux RSS de l'instance). |
| Bluesky | **Non vérifié en réel** | Comptes seulement (flux RSS public `bsky.app/profile/<nom>/rss`). Les hashtags et mots-clés exigent la recherche de l'API, qui « peut exiger une authentification » selon la documentation : non branchés. |
| YouTube | **Non vérifié en réel** | Chaînes (flux Atom officiel `feeds/videos.xml?channel_id=UC…`) : titre et description des vidéos. |
| Reddit | **Non vérifié en réel** | Subreddits (`/r/<nom>/.rss`). CGU restrictives pour un usage professionnel : à valider ; Reddit peut refuser l'agent de collecte. |
| X (Twitter) | Non branché | Plus d'accès gratuit ; paiement à l'usage (≈ 0,005 $ par post lu, recherche limitée à 7 jours selon PostProxy). Nécessite votre compte développeur et un jeton. |
| Facebook, Instagram | Non branchés, **non prévus** | Pas d'accès public autorisé : CrowdTangle fermé (14 août 2024), Meta Content Library réservée aux chercheurs et partenaires. L'extraction automatique violerait les CGU. Alternative : import manuel ou export d'un outil commercial. |
| Telegram | Non branché | Les conditions de l'API n'établissent pas clairement la lecture de canaux publics à des fins de veille ; l'accès officiel passe par une application avec `api_id` et une connexion par compte. À clarifier avant tout code. |
| TikTok, WhatsApp | Non branchés | Pas d'accès public autorisé pour ce cas d'usage. |

Les hôtes de Bluesky, YouTube et Reddit sont fixes ; seul l'identifiant validé entre dans l'adresse (pas de requête vers une adresse choisie).

## Classification : lacunes connues (liste des « articles à qualifier » du 20 octobre 2026)

- Ajoutés : affrontements et groupes armés (« clash between », « militants », « armed men », « drone strike », JNIM, ISIS, ISWAP, Boko Haram, Al-Shabaab…), grèves (« suspend strike », « health workers strike »…), censure du web, monnaies (naira, cedi, forex). « clash » et « battle » seuls sont volontairement exclus (« battles measles », « clash over the budget »).
- Non couverts : atteintes aux droits humains (torture, détentions secrètes), accidents d'aviation, emploi, eau/assainissement ; publications en espagnol, allemand, italien ; publications dont seul le Sahel (région) est cité (aucun pays déduit). La liste « à qualifier » du tableau de bord montre maintenant jusqu'à 10 articles par source.
- Ajoutés après la collecte France 24 : « affrontements » (pluriel seulement), M23, Wagner, Africa Corps, Forces de soutien rapide ; droits humains et détentions (« jours de détention », « détention arbitraire », torture, crimes de guerre…) ; actions judiciaires (perquisitions, mandats d'arrêt, inculpations) ; « tensions diplomatiques ».
- Volontairement non classés : santé chronique (cancer, maladies rénales), sport, culture, société. Un article de synthèse hebdomadaire (« l'hebdo Afrique ») est classé selon son premier thème et ne fait pas un événement fiable : à traiter comme une revue de presse.


## Politique intérieure (décision du 20 octobre 2026)

Nouveau type d'événement `political_tension` (domaine Gouvernance, impact 2) : opposition/opposant, parti ou majorité au pouvoir,
crise politique, motion de censure, remaniement, limogeage, dissolution, front politique, destitution.
Limites assumées : « opposition » seul en titre est un signal faible ; « la majorité » ou « coalition » seuls ne déclenchent rien ;
une mention dans le corps seul ne passe pas le seuil. Sur 50 posts réels testés : 16 classés, le reste est culturel (musique, livres),
en double (bot OsazuwaAkonedo) ou sans mot-clé exploitable (non traités volontairement).

## Regroupement d'événements (octobre 2026)

En plus de Jaccard ≥ 0,5, deux titres du même pays, domaine et fenêtre de 48 h fusionnent s'ils partagent au moins 3 mots distinctifs
(hors nom du pays) couvrant la moitié du plus court ; ordinaux et « cas/case » harmonisés FR/EN. Limite : approximatif, une fusion à tort
est possible sur des titres génériques ; une fusion manquée gonfle le nombre d'événements « source unique ».

## Anciens articles republiés (raison `stale`)

Si l'adresse de l'article contient une date (`/2026/06/…`, `/2026-06-12-…`) antérieure de plus de 45 jours à la date de publication du flux,
le document n'est pas rattaché (« ancien article republié ») : la date du flux est celle de la republication, pas celle des faits.
Limite : sans date dans l'adresse, la détection est impossible ; ces cas restent des événements datés du jour de republication.

## Historique conservé entre deux lancements (mode réel)

Les documents collectés sont enregistrés dans `data/documents.json` après chaque collecte (30 jours au plus) et rechargés au démarrage
(ligne « Historique : N document(s) rechargé(s) »). Seuls les champs bruts sont conservés : pays, thème et événements sont recalculés
avec les règles à jour, donc une amélioration de la classification s'applique aussi à l'historique. Les publications de réseaux
sociaux sont déjà anonymisées avant l'enregistrement. Supprimer `documents.json` repart d'une base vide. Ce fichier est un palier avant
PostgreSQL : pas de verrou entre deux serveurs lancés en même temps sur le même dossier `data`.
