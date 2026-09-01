# Sync Garmin → NATE FITNESS (gratuit, via GitHub Actions)

Récupère automatiquement, 2x/jour, tes données Garmin (Descent G1 + Index S2 +
activités) et les écrit dans `data/garmin-data.json`, que l'onglet **Cockpit** de
`fitness-tracker.html` va lire.

⚠️ Cette lib (`garminconnect`/`garth`) n'est pas l'API officielle Garmin — elle
imite la connexion de l'app mobile. Usage personnel très répandu, mais pas
formellement "autorisé" par les CGU Garmin. À toi de voir.

## Historique cumulatif

Chaque run ne récupère qu'une fenêtre récente de Garmin (14 jours santé/activités,
90 jours composition corporelle), mais **le script fusionne toujours avec le
`data/garmin-data.json` déjà présent dans le repo** avant d'écrire — rien n'est
jamais perdu, une même date resynchronisée écrase juste l'ancienne version d'elle-
même. Au fil des synchros (2×/jour), l'historique complet s'accumule tout seul :
après quelques semaines/mois, l'app peut filtrer sur 7j/30j/90j/180j/1an/Tout.

## Ce que ça récupère

- **`activities`** : toutes tes activités Garmin (course, vélo, muscu, plongée...)
- **`dives`** : détail des plongées Descent G1 (profondeur, durée, température) —
  voir note plus bas, certains champs peuvent rester vides au premier essai
- **`health`** : un point par jour sur 14 jours — FC repos, **FC 24h (moy/min/max)**,
  HRV, sommeil (durée + score + **détail des phases profond/léger/paradoxal/éveil**),
  Body Battery, stress moyen, pas
- **`bodyComposition`** : chaque pesée Index S2 sur 90 jours — poids, % masse
  grasse, % eau, masse musculaire, masse osseuse, graisse viscérale

## Étapes (15 min, une seule fois)

### 1. Crée un repo GitHub séparé
- Nouveau repo, **public** (nécessaire pour que l'app web lise le JSON sans
  authentification — il ne contiendra jamais ton mot de passe, seulement tes
  données de santé, ce qui reste sensible si le repo est public : garde ça en tête)
- Uploade-y tout le contenu de ce dossier

### 2. Génère tes jetons de connexion, EN LOCAL sur ton ordi
```bash
pip install garminconnect
python generate_tokens.py
```
Entre ton email/mot de passe Garmin (et le code MFA si activé). Le script
imprime un long bloc en base64 à la fin.

**Jamais ton mot de passe n'est envoyé à GitHub** — seulement ce jeton de session.

### 3. Ajoute le secret dans GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Nom : `GARMIN_TOKENS_B64`
- Valeur : colle le bloc base64 généré à l'étape 2

### 4. Active le workflow
Onglet **Actions** du repo → autorise les workflows si demandé → lance
`Sync Garmin bio data` manuellement (bouton "Run workflow") pour tester tout
de suite, sans attendre le cron.

Ouvre ensuite `data/garmin-data.json` dans le repo pour vérifier que les
sections sont bien remplies.

### 5. Branche l'app dessus
Dans `fitness-tracker.html`, icône ⚙️ (en haut) → colle l'URL brute :
```
https://raw.githubusercontent.com/TON_USER/TON_REPO/main/data/garmin-data.json
```

## Si les métriques de plongée reviennent vides

Garmin ne documente pas publiquement les noms de champs internes pour la
profondeur/température de plongée. Le script essaie plusieurs noms connus, et
à la première plongée traitée, il imprime les clés brutes disponibles dans les
logs (onglet **Actions** → dernier run → étape "Run sync"). Si `maxDepthM` ou
`waterTempC` restent `null` après une vraie synchro, copie ce bloc de logs et
on ajuste les noms de clés en une modif.

## Si la synchro casse un jour

Garmin change parfois son système de connexion, ce qui peut invalider les
jetons. Il suffit de relancer `generate_tokens.py` en local et de remettre à
jour le secret `GARMIN_TOKENS_B64`.
