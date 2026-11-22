# Mise à jour éditoriale NYANSA — Note stratégique pour décideurs

Cette version harmonise les exports HTML, PDF et Word de la note stratégique pour décideurs.

## Changements

- Couverture harmonisée avec l'identité NYANSA, la classification, le statut et la version du document.
- Hiérarchie visuelle renforcée dans les trois formats (typographie, titres, métadonnées et lisibilité).
- Le modèle `executive_brief` comprend désormais les rubriques : synthèse exécutive, faits majeurs, méthodologie et limites, impacts opérationnels, recommandations et conclusion.
- Les rubriques `Sources` et `Limites et biais de couverture` restent conservées dans le document afin d'assurer la traçabilité et de rendre visibles les limites de collecte.
- Les nouvelles rubriques rédigées par l'analyste restent volontairement à compléter : aucun fait, aucune source et aucune recommandation contextualisée n'est inventé par le générateur.
- Les anciens brouillons restent lisibles : les rendus utilisent les sections présentes dans chaque enregistrement et n'exigent pas de migration des données.

## Tests

Depuis le dossier du projet, exécuter :

```powershell
$env:PYTHONPATH="backend"
python -m unittest discover -s backend/tests -v
```

Les tests couvrent la structure du modèle décideur et la présence des éléments de couverture dans les exports HTML, DOCX et PDF.

## Démarrage local

Le serveur de démonstration doit être lancé depuis le dossier `backend` :

```powershell
cd backend
python -m nyansa.devserver --port 8000
```

L'interface est accessible à `http://127.0.0.1:8000`.

**Attention :** le mode démonstration utilise des données fictives et conserve les livrables en mémoire. Ils sont perdus à l'arrêt du serveur. Ne pas l'utiliser pour conserver des livrables opérationnels.
