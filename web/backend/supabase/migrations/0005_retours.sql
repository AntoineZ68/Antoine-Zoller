-- Retours des avocats du pilote : « Signaler un problème » depuis un dossier
-- (un fait mal daté, une personne oubliée, un écran qui ne répond pas).
--
-- Consultables dans l'éditeur de tables de Supabase. Un retour survit à la
-- suppression de son dossier (dossier_id passe à null) : c'est l'historique
-- de ce qui n'allait pas, pas une donnée du dossier. Le message est écrit
-- par l'avocat lui-même ; le contenu des pièces n'y est jamais copié.
--
-- Tant que cette migration n'est pas appliquée, le bouton affiche que le
-- signalement n'a pas pu être enregistré (et propose l'e-mail de contact
-- s'il est configuré : variable EMAIL_CONTACT du backend).

create table if not exists retours (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    dossier_id uuid references dossiers(id) on delete set null,
    message text not null check (char_length(message) between 3 and 4000),
    contexte text,
    statut_dossier text,
    cree_le timestamptz not null default now()
);

create index if not exists retours_owner_id_idx on retours(owner_id);

alter table retours enable row level security;

create policy "Un utilisateur dépose ses propres retours"
    on retours for insert
    with check (
        owner_id = auth.uid()
        and (dossier_id is null or exists (
            select 1 from dossiers where dossiers.id = retours.dossier_id and dossiers.owner_id = auth.uid()
        ))
    );

create policy "Un utilisateur voit ses propres retours"
    on retours for select
    using (owner_id = auth.uid());
