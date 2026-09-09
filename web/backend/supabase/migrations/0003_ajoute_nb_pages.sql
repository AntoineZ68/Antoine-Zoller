-- Le backend écrit le nombre de pages du dossier une fois le traitement
-- terminé (app/pipeline.py) ; colonne oubliée dans la migration initiale.

alter table dossiers add column nb_pages integer;
