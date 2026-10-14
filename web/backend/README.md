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
2. Dans l'éditeur SQL du projet, exécuter dans l'ordre le contenu de
   chaque fichier de `supabase/migrations/` (`0001_init.sql`, puis
   `0002…`, jusqu'à `0005_retours.sql`). Sur un projet déjà en service,
   n'exécuter que les fichiers pas encore appliqués — `0005_retours.sql`
   crée la table des signalements des avocats (bouton « Signaler un
   problème ») ; sans elle, le bouton répond que le signalement n'a pas pu
   être enregistré.
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
- `POST /api/dossiers/{id}/relancer` — relance le traitement d'un dossier
  en erreur à partir des PDF déjà transmis (rien à renvoyer).
- `POST /api/dossiers/{id}/retours` (JSON : `message`, `contexte`
  optionnel) — enregistre un signalement de l'avocat dans la table
  `retours`, consultable dans l'éditeur de tables de Supabase.
- `GET /api/infos` — public : fournisseur d'IA, limites de pages et de
  taille, e-mail de contact (`EMAIL_CONTACT`). Sert à la mention de
  confidentialité du formulaire d'envoi.

Toutes les routes `/api/dossiers*` attendent un en-tête
`Authorization: Bearer <jeton Supabase Auth de l'avocat>`.
