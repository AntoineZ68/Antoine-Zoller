"""Taille de dossier prise en charge : un dossier trop gros est refusé dès
l'envoi, avec un message compréhensible, sans laisser de dossier fantôme."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

import pymupdf
import pytest
from fastapi.testclient import TestClient

import app.main as main


def _pdf(nb_pages: int) -> bytes:
    with pymupdf.open() as doc:
        for _ in range(nb_pages):
            doc.new_page()
        return doc.tobytes()


class FauxSupabase:
    """Enregistre ce qui est créé en base : rien ne doit l'être en cas de refus."""

    def __init__(self) -> None:
        self.inserts: list[dict] = []

    def table(self, nom):
        faux = self

        class Requete:
            def insert(self, ligne):
                faux.inserts.append(ligne)
                return self

            def update(self, champs):
                return self

            def delete(self):
                return self

            def eq(self, *args):
                return self

            def execute(self):
                class R:
                    data = [{"id": "d1", "nom": "x", "statut": "en_attente", "cree_le": "2026-10-04T10:00:00Z", "mis_a_jour_le": "2026-10-04T10:00:00Z"}]
                return R()

        return Requete()


@pytest.fixture
def api(monkeypatch):
    supabase = FauxSupabase()
    envoyes: list[str] = []

    class FauxService:
        class storage:
            @staticmethod
            def from_(nom):
                class Bucket:
                    def upload(self, chemin, fichier, options):
                        envoyes.append(chemin)
                return Bucket()

    monkeypatch.setattr(main, "client_service", lambda: FauxService())
    monkeypatch.setattr(main, "_traiter_puis_nettoyer", lambda *a, **k: None)
    main.app.dependency_overrides[main._contexte_utilisateur] = lambda: (supabase, "u1")
    yield TestClient(main.app), supabase, envoyes
    main.app.dependency_overrides.clear()


def _envoyer(client, *fichiers):
    return client.post(
        "/api/dossiers",
        data={"nom": "Dupont"},
        files=[("fichiers", (nom, contenu, "application/pdf")) for nom, contenu in fichiers],
    )


def test_dossier_dans_la_limite_accepte(api) -> None:
    client, supabase, envoyes = api
    r = _envoyer(client, ("tome1.pdf", _pdf(80)), ("tome2.pdf", _pdf(70)))
    assert r.status_code == 201, r.text
    assert len(supabase.inserts) == 1 and len(envoyes) == 2


def test_trop_de_pages_refuse_avec_un_message_clair(api) -> None:
    """La limite porte sur le dossier entier, tous fichiers confondus."""
    client, supabase, envoyes = api
    r = _envoyer(client, ("tome1.pdf", _pdf(100)), ("tome2.pdf", _pdf(51)))
    assert r.status_code == 413
    assert "151 pages" in r.json()["detail"] and str(main.LIMITE_PAGES) in r.json()["detail"]
    assert supabase.inserts == [] and envoyes == [], "aucun dossier fantôme"


def test_fichier_trop_lourd_refuse(api, monkeypatch) -> None:
    client, supabase, envoyes = api
    monkeypatch.setattr(main, "LIMITE_MO_PAR_FICHIER", 0)
    r = _envoyer(client, ("scan.pdf", _pdf(3)))
    assert r.status_code == 413
    assert "scan.pdf" in r.json()["detail"] and "Mo" in r.json()["detail"]
    assert supabase.inserts == []


def test_pdf_illisible_refuse(api) -> None:
    client, supabase, _ = api
    r = _envoyer(client, ("abime.pdf", b"%PDF-1.4 pas un vrai pdf"))
    assert r.status_code == 400
    assert "abime.pdf" in r.json()["detail"]
    assert supabase.inserts == []
