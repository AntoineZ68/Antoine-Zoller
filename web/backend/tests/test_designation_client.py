"""Désignation du client par l'avocat : la base du dossier est modifiée
puis renvoyée dans le Storage, et le résumé ne désigne jamais la mauvaise
personne — régénéré, ou supprimé si la régénération échoue."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from depouille.db import ouvrir_db


@pytest.fixture
def base_dossier(tmp_path) -> bytes:
    chemin = tmp_path / "d.db"
    db = ouvrir_db(chemin)
    db.execute("INSERT INTO personnes (nom, role) VALUES ('Lucas MARTINON', 'mis_en_cause'), ('Yannick FONTANEL', 'mis_en_cause')")
    db.execute("INSERT INTO resume_affaire (id, texte, genere_le) VALUES (1, 'Ancien résumé', 'x')")
    db.commit()
    db.close()
    return chemin.read_bytes()


class FauxBucket:
    def __init__(self, contenu: bytes) -> None:
        self.contenu = contenu
        self.envoye: bytes | None = None

    def download(self, chemin):
        return self.contenu

    def upload(self, chemin, fichier, options):
        self.envoye = Path(fichier).read_bytes()


@pytest.fixture
def client_api(monkeypatch, base_dossier):
    bucket = FauxBucket(base_dossier)

    class FauxService:
        class storage:
            @staticmethod
            def from_(nom):
                return bucket

    monkeypatch.setattr(main, "client_service", lambda: FauxService())
    monkeypatch.setattr(main, "_dossier_ou_404", lambda supabase, dossier_id: {"id": dossier_id, "statut": "termine"})
    main.app.dependency_overrides[main._contexte_utilisateur] = lambda: (None, "u1")
    yield TestClient(main.app), bucket
    main.app.dependency_overrides.clear()


def _lire(octets: bytes, tmp_path) -> sqlite3.Connection:
    chemin = tmp_path / "relu.db"
    chemin.write_bytes(octets)
    db = sqlite3.connect(chemin)
    db.row_factory = sqlite3.Row
    return db


def test_designation_enregistree_et_resume_regenere(monkeypatch, client_api, tmp_path) -> None:
    api, bucket = client_api

    def faux_resume(db, config, console):
        db.execute("INSERT INTO resume_affaire (id, texte, genere_le) VALUES (1, 'Votre client, Lucas MARTINON…', 'y')")
        db.commit()

    monkeypatch.setattr(main, "generer_resume", faux_resume)
    r = api.post("/api/dossiers/d1/client", json={"nom": "Lucas MARTINON"})
    assert r.status_code == 200, r.text
    assert r.json() == {"client": "Lucas MARTINON", "resume_regenere": True}

    db = _lire(bucket.envoye, tmp_path)
    assert [x[0] for x in db.execute("SELECT nom FROM personnes WHERE est_client = 1")] == ["Lucas MARTINON"]
    assert db.execute("SELECT texte FROM resume_affaire").fetchone()[0].startswith("Votre client, Lucas")


def test_regeneration_echouee_aucun_resume_plutot_qu_un_faux(monkeypatch, client_api, tmp_path) -> None:
    api, bucket = client_api
    monkeypatch.setattr(main, "generer_resume", lambda db, config, console: None)
    r = api.post("/api/dossiers/d1/client", json={"nom": "Yannick FONTANEL"})
    assert r.json()["resume_regenere"] is False
    db = _lire(bucket.envoye, tmp_path)
    assert db.execute("SELECT COUNT(*) FROM resume_affaire").fetchone()[0] == 0


def test_personne_inconnue(monkeypatch, client_api) -> None:
    api, bucket = client_api
    monkeypatch.setattr(main, "generer_resume", lambda db, config, console: None)
    r = api.post("/api/dossiers/d1/client", json={"nom": "Personne INVENTÉE"})
    assert r.status_code == 404
    assert bucket.envoye is None, "rien n'est réécrit dans le Storage"
