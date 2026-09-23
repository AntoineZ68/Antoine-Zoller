-- Détail lisible de l'étape en cours, affiché sous son libellé côté front.
-- Utilisé pendant la lecture du document pour montrer l'avancement de la
-- reconnaissance de caractères (« Pages numérisées reconnues : 23 / 105 ») :
-- sur un gros dossier scanné, cette phase dure des dizaines de minutes, et
-- sans avancement visible rien ne la distinguait d'un traitement bloqué.
--
-- Le backend écrit cette colonne au mieux : tant que cette migration n'est
-- pas appliquée, le traitement fonctionne normalement, sans avancement.

alter table traitement_etapes add column if not exists detail text;
