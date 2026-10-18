# BadPelican — alertes de tournois BadNet sur WhatsApp

BadPelican vérifie les tournois de badminton publiés sur [BadNet](https://badnet.fr/) et envoie un message dans un groupe WhatsApp lorsqu'un tournoi répond aux critères configurés : inscriptions ouvertes, places encore disponibles et distance inférieure ou égale à 100 km de Pélissanne (France).

Le projet utilise Python 3.10, `requests` et BeautifulSoup pour interroger et analyser les résultats publics de BadNet, puis `geopy` pour calculer les distances géodésiques. Les coordonnées fournies par BadNet sont utilisées quand elles sont disponibles ; sinon, la ville est géocodée avec Nominatim/OpenStreetMap. Les messages WhatsApp sont envoyés par l'API Green-API.

## Fonctionnement

1. Le workflow GitHub Actions [`checker.yml`](.github/workflows/checker.yml) lance `main.py` toutes les heures, ou manuellement depuis l'onglet **Actions**.
2. Le script parcourt les résultats BadNet des départements configurés dans `SEARCH_DEPARTMENTS` et demande les pages de résultats disponibles avec le filtre « inscriptions ouvertes ».
3. Les tournois sans places restantes sont ignorés. Pour les autres, le script compare leur distance à Pélissanne avec la limite configurée dans `MAX_DISTANCE_KM`.
4. Une alerte contenant le nom, la ville, la date et le lien BadNet est envoyée au groupe WhatsApp configuré.
5. Après un envoi réussi, l'URL du tournoi est ajoutée à `sent_tournaments.json`. Le workflow enregistre ce fichier dans le dépôt pour conserver l'historique entre ses exécutions.

BadNet peut modifier son site ou son interface de recherche. Si sa structure change, le scraper peut nécessiter une mise à jour ; les erreurs de récupération ou de lecture apparaissent dans les journaux du workflow.

## Configuration WhatsApp et GitHub Actions

Il faut un compte Green-API avec une instance WhatsApp autorisée et connectée. Le numéro WhatsApp associé à l'instance doit être membre du groupe cible. Dans Green-API, la méthode **GetChats** permet de trouver l'ID du groupe : sa valeur se termine généralement par `@g.us`.

Dans le dépôt GitHub, ouvrir **Settings → Secrets and variables → Actions → New repository secret** et créer les trois secrets suivants :

| Nom du secret | Valeur |
| --- | --- |
| `WHATSAPP_INSTANCE_ID` | Identifiant de l'instance Green-API |
| `WHATSAPP_TOKEN` | Jeton API de cette instance |
| `WHATSAPP_CHAT_ID` | Identifiant du groupe WhatsApp, par exemple une valeur se terminant par `@g.us` |

Ne jamais inscrire le jeton ou les autres secrets dans le code, un commit, un ticket public ou un message. Le dépôt étant public, son code est visible par tous ; les secrets Actions ne le sont pas. Accorder l'accès d'écriture au dépôt et aux workflows uniquement aux personnes de confiance.

Pour vérifier l'envoi WhatsApp sans lancer la recherche BadNet, ouvrir **Actions → Vérifier les tournois BadNet → Run workflow**, cocher `test_whatsapp`, puis lancer. Pour exécuter une vérification complète, lancer le workflow sans cocher cette option.

## Lancer le script localement

Installer Python 3.10 ou une version compatible, puis depuis le dossier du projet :

```bash
python -m pip install -r requirements.txt
```

Définir les trois variables d'environnement WhatsApp avant l'exécution. Exemple PowerShell :

```powershell
$env:WHATSAPP_INSTANCE_ID = "votre-instance"
$env:WHATSAPP_TOKEN = "votre-token"
$env:WHATSAPP_CHAT_ID = "identifiant-du-groupe@g.us"
python main.py
```

Une première exécution envoie les alertes correspondantes et crée/met à jour `sent_tournaments.json`. Conserver ce fichier entre les exécutions pour éviter les notifications en double.

## Adapter le projet à un autre club

Pour créer une copie destinée à un autre club :

1. **Forker ou copier le dépôt** dans un dépôt contrôlé par le club. La copie du code ne reprend pas les secrets du dépôt d'origine : il faut créer les siens.
2. Dans `main.py`, remplacer `PELISSANNE_COORDINATES` par la latitude et la longitude du club ou de la ville de référence, en ordre `(latitude, longitude)`.
3. Modifier `MAX_DISTANCE_KM` pour choisir le rayon voulu.
4. Adapter `SEARCH_DEPARTMENTS` aux départements à explorer autour du club. Ce paramètre limite la recherche initiale ; le calcul géodésique reste le filtre définitif de distance.
5. Créer ou choisir le groupe WhatsApp, connecter à Green-API un compte membre de ce groupe, puis configurer ses trois secrets dans le nouveau dépôt.
6. Dans les paramètres GitHub Actions du nouveau dépôt, autoriser les workflows à écrire dans le dépôt. Le workflow enregistre `sent_tournaments.json` après les envois pour conserver la déduplication.
7. Lancer d'abord le test WhatsApp, puis une vérification complète depuis l'onglet **Actions**.

Si le projet est utilisé hors de France, adapter également la requête de géocodage Nominatim dans `_coordinates_for_city`, qui ajoute actuellement « France » au nom de ville.

## Fichiers principaux

- `main.py` — collecte BadNet, filtre de distance, détection des tournois complets, déduplication et envoi Green-API.
- `requirements.txt` — dépendances Python.
- `.github/workflows/checker.yml` — exécutions horaires et déclenchement manuel.
- `sent_tournaments.json` — état versionné des alertes déjà envoyées et cache de géocodage.
