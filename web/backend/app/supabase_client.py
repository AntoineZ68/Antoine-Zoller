"""Deux clients Supabase, pour deux usages distincts — ne jamais les confondre.

- `client_utilisateur` : authentifié avec le jeton de l'avocat connecté.
  Toutes les lectures/écritures sur `dossiers` et `traitement_etapes`
  passent par lui, pour que la Row Level Security de Postgres s'applique
  automatiquement — aucune vérification d'appartenance à réécrire côté
  application, Postgres la fait déjà.
- `client_service` : clé service_role, contourne la RLS. Utilisé
  uniquement côté serveur, jamais exposé au navigateur, et seulement pour
  le stockage (upload/download des PDF et des livrables) — les tables
  metier passent toujours par le client utilisateur.
"""

from __future__ import annotations

import os

from supabase import Client, create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _verifier_config() -> None:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Configuration Supabase manquante. Renseigne SUPABASE_URL, "
            "SUPABASE_ANON_KEY et SUPABASE_SERVICE_ROLE_KEY (voir .env.example)."
        )


def client_utilisateur(jeton_acces: str) -> Client:
    """Client Postgres agissant comme l'utilisateur authentifié (RLS active)."""
    _verifier_config()
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.postgrest.auth(jeton_acces)
    return client


def client_service() -> Client:
    """Client à privilèges élevés — stockage uniquement, jamais les tables métier."""
    _verifier_config()
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
