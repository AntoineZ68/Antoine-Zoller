"""Garde-fou minimal, indépendant de tout dossier réel : vérifie que
l'ensemble des routes attendues par le frontend est bien enregistré sur
l'application FastAPI.

Ce test existe suite à un vrai incident : l'ajout d'une route (suppression
de dossier) a fait disparaître silencieusement le décorateur d'une autre
route (téléchargement d'un livrable) — la fonction Python restait valide,
mais n'était plus jamais appelée par aucune requête HTTP. Aucune exception
au démarrage, aucun test existant ne le détectait ; seul un vrai clic dans
le pilote en ligne l'a révélé. Un simple contrôle sur les routes déclarées
suffit à l'attraper avant tout déploiement."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

from app.main import app  # noqa: E402

ROUTES_ATTENDUES = {
    ("GET", "/health"),
    ("POST", "/api/dossiers"),
    ("GET", "/api/dossiers"),
    ("GET", "/api/dossiers/{dossier_id}"),
    ("DELETE", "/api/dossiers/{dossier_id}"),
    ("GET", "/api/dossiers/{dossier_id}/donnees"),
    ("GET", "/api/dossiers/{dossier_id}/documents"),
    ("GET", "/api/dossiers/{dossier_id}/documents/{nom_fichier}"),
    ("GET", "/api/dossiers/{dossier_id}/livrables/{nom_fichier}"),
}


def test_toutes_les_routes_attendues_sont_enregistrees() -> None:
    routes_reelles = {
        (methode, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for methode in route.methods
        if methode != "HEAD"
    }
    manquantes = ROUTES_ATTENDUES - routes_reelles
    assert not manquantes, f"Route(s) attendue(s) absente(s) de l'application : {manquantes}"
