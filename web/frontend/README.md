# Frontend (pilote MVP)

Une seule page HTML/JS, sans étape de build (pas de npm, pas de framework) :
choix délibéré pour aller vite pendant qu'on valide que le parcours complet
fonctionne. Une fois ça prouvé, cette page pourra être remplacée par un vrai
frontend (React ou autre) reprenant le style de la maquette visuelle.

`index.html` contient en dur trois valeurs, publiques par conception (la clé
Supabase `anon`/`sb_publishable_...` est faite pour être exposée côté
navigateur — l'isolation entre cabinets est garantie par la Row Level
Security côté base de données, pas par le secret de cette clé) :

- l'URL du projet Supabase,
- la clé `anon`,
- l'URL de l'API backend (Render).

## Déployer sur Render (Static Site)

1. Dashboard Render → **New +** → **Static Site**.
2. Sélectionner le même dépôt GitHub, branche `claude/inspiring-knuth-ug7qgd`.
3. **Root Directory** : `web/frontend`
4. **Build Command** : laisser vide (rien à compiler).
5. **Publish Directory** : `.`
6. Déployer.

Une fois l'adresse attribuée (ex. `https://depouille-web.onrender.com`),
mettre à jour la variable d'environnement `FRONTEND_ORIGIN` du service
backend avec cette adresse exacte, puis redéployer le backend — sans ça,
le navigateur bloque les appels à l'API (CORS).
