# Backend web (pilote MVP)

Enveloppe le moteur `depouille` (src/depouille, non modifié) derrière une API
pour qu'un avocat externe puisse l'utiliser depuis un navigateur, sans que
quelqu'un lance une commande à sa place.

## Choix d'architecture

Le contenu extrait d'un dossier (pièces, déclarations, événements —
couvert par le secret professionnel) reste dans le fichier SQLite par
affaire déjà produit et testé par le moteur. Cette base Supabase ne
contient que les métadonnées nécessaires au produit web : qui possède quel
dossier, où en est son traitement. Voir le commentaire en tête de
`supabase/migrations/0001_init.sql` pour le détail de ce choix.

## Mise en place de Supabase

1. Créer le projet sur [supabase.com](https://supabase.com), dans une
   région UE/EEE (Frankfurt `eu-central-1`, Ireland `eu-west-1`, etc.) —
   n'importe laquelle convient pour la résidence des données, seul le fait
   de rester dans l'UE/EEE compte.
2. Dans l'éditeur SQL du projet, exécuter le contenu de
   `supabase/migrations/0001_init.sql`.
3. Dans Storage, créer deux buckets **privés** :
   - `dossiers-source` (les PDF uploadés par les avocats)
   - `dossiers-resultats` (le fichier SQLite + les livrables générés)
4. Dans Project Settings → API, récupérer :
   - Project URL
   - `anon` key (utilisée par le frontend)
   - `service_role` key (utilisée uniquement par ce backend — jamais
     exposée au navigateur)

## Variables d'environnement attendues

Voir `.env.example` pour la liste complète (URL, clés, provider LLM,
origine CORS du frontend).

## Lancer l'API en local

```
cd web/backend
pip install -r requirements.txt
pip install -e ../..          # installe le moteur depouille (src/depouille)
cp .env.example .env           # puis renseigner les vraies valeurs
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload
```

## Routes

- `POST /api/dossiers` (multipart : `fichier` PDF, `nom`, `reference`
  optionnelle, `mode_offline` optionnel) — crée le dossier et lance le
  traitement en tâche de fond.
- `GET /api/dossiers` — liste les dossiers de l'utilisateur authentifié.
- `GET /api/dossiers/{id}` — détail d'un dossier, avec l'état de chaque
  étape du pipeline.
- `GET /api/dossiers/{id}/livrables/{nom_fichier}` — URL signée temporaire
  pour télécharger un livrable généré.

Toutes les routes `/api/dossiers*` attendent un en-tête
`Authorization: Bearer <jeton Supabase Auth de l'avocat>`.
