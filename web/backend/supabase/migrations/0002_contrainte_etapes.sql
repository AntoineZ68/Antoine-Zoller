-- Une seule ligne par (dossier, étape) : le backend fait un upsert à chaque
-- mise à jour de progression plutôt que de gérer lui-même l'existence de la
-- ligne. Migration séparée de 0001 (déjà appliquée) plutôt que de la
-- modifier après coup.

alter table traitement_etapes
    add constraint traitement_etapes_dossier_etape_uniq unique (dossier_id, etape);
