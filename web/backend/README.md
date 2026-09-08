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

1. Créer le projet sur [supabase.com](https://supabase.com), région
   **Frankfurt (eu-central-1)** — c'est le choix de résidence des données
   UE qu'on avait retenu.
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

```
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
ANTHROPIC_API_KEY=...        # ou MISTRAL_API_KEY selon le provider choisi
```
