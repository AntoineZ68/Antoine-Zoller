-- Schéma initial du pilote MVP.
--
-- Choix délibéré : cette base ne contient PAS les pièces/déclarations/
-- événements extraits d'un dossier — ce contenu, très sensible (secret
-- professionnel), continue de vivre dans le fichier SQLite par affaire déjà
-- produit et testé par le moteur Python (src/depouille), stocké dans
-- Supabase Storage. Cette base ne contient que les métadonnées nécessaires
-- au produit web : qui possède quel dossier, où en est son traitement.
--
-- Authentification : gérée par Supabase Auth (table auth.users), pas
-- réimplémentée ici.

create extension if not exists pgcrypto;

create table dossiers (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    nom text not null,
    reference text,
    statut text not null default 'en_attente'
        check (statut in ('en_attente', 'en_cours', 'termine', 'erreur')),
    etape_courante text,
    message_erreur text,
    fichier_source_path text,
    resultat_db_path text,
    couleurs_surlignage jsonb not null default '{"procedure": "#cf9e2e", "declaration": "#3e6e93", "faits": "#4c7a5c"}'::jsonb,
    cree_le timestamptz not null default now(),
    mis_a_jour_le timestamptz not null default now()
);

create index dossiers_owner_id_idx on dossiers(owner_id);

-- Historique des étapes de traitement, pour afficher une progression réelle
-- côté front ("Ingestion... Classification... Chronologie...") plutôt qu'un
-- simple statut binaire.
create table traitement_etapes (
    id uuid primary key default gen_random_uuid(),
    dossier_id uuid not null references dossiers(id) on delete cascade,
    etape text not null
        check (etape in ('ingest', 'classify', 'index', 'chrono', 'decl', 'build')),
    statut text not null default 'en_attente'
        check (statut in ('en_attente', 'en_cours', 'termine', 'erreur')),
    debut timestamptz,
    fin timestamptz,
    tokens_in integer not null default 0,
    tokens_out integer not null default 0,
    cout_usd numeric(10, 4) not null default 0,
    message_erreur text
);

create index traitement_etapes_dossier_id_idx on traitement_etapes(dossier_id);

-- Maintient mis_a_jour_le automatiquement.
create or replace function maj_mis_a_jour_le()
returns trigger as $$
begin
    new.mis_a_jour_le = now();
    return new;
end;
$$ language plpgsql;

create trigger dossiers_mis_a_jour_le
    before update on dossiers
    for each row execute function maj_mis_a_jour_le();

-- Row Level Security : un avocat ne voit et ne modifie que ses propres
-- dossiers. Non négociable dès le pilote, pas une amélioration pour plus
-- tard — c'est la garantie minimale d'isolation entre cabinets.
alter table dossiers enable row level security;
alter table traitement_etapes enable row level security;

create policy "Un utilisateur gère ses propres dossiers"
    on dossiers for all
    using (owner_id = auth.uid())
    with check (owner_id = auth.uid());

create policy "Un utilisateur voit les étapes de ses propres dossiers"
    on traitement_etapes for select
    using (
        exists (
            select 1 from dossiers
            where dossiers.id = traitement_etapes.dossier_id
            and dossiers.owner_id = auth.uid()
        )
    );

-- Les écritures sur traitement_etapes viennent du backend (clé service_role,
-- qui contourne RLS), jamais directement du navigateur — pas de policy
-- insert/update pour les utilisateurs authentifiés normaux.
