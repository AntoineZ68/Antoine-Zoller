"""Résumé de l'affaire : un court paragraphe tiré des seuls faits et
déclarations déjà vérifiés, qui dit ce que le client reconnaît et ce qu'il
conteste — sans jamais deviner qui est le client ni qualifier."""

from __future__ import annotations

import sqlite3

import pytest
from rich.console import Console

import depouille.resume as resume
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd.pdf', 1, 'x', 'x')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, 'PV d''audition', 1, 1)")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Lucas MARTINON', 'mis_en_cause'), (2, 'Odile SERMET', 'victime')")
    db.execute(
        "INSERT INTO evenements_faits (piece_id, page, citation, description, statut_verif) "
        "VALUES (1, 1, 'c', 'Cambriolage d''un pavillon à Biviers le 12/02/2026', 'verifie'), "
        "(1, 1, 'c', 'Fait non vérifié qui ne doit jamais apparaître', 'rejete')"
    )
    db.execute(
        "INSERT INTO declarations (personne_id, piece_id, page, citation, point_factuel, statut_verif) "
        "VALUES (1, 1, 1, 'c', 'Reconnaît avoir attendu dans la voiture le 12/02', 'verifie'), "
        "(2, 1, 1, 'c', 'La victime a quitté son domicile à 13h30', 'verifie')"
    )
    db.commit()
    return db


def _modele(monkeypatch, texte: str) -> dict:
    vu = {}

    class Faux:
        def appeler(self, systeme, prompt, modele):
            vu["systeme"], vu["prompt"] = systeme, prompt
            return ReponseLLM(texte=texte, tokens_in=10, tokens_out=10)

    monkeypatch.setattr(resume, "obtenir_provider", lambda config: Faux())
    return vu


def _resume_en_base(db) -> str | None:
    ligne = db.execute("SELECT texte FROM resume_affaire WHERE id = 1").fetchone()
    return ligne["texte"] if ligne else None


def test_paragraphe_nourri_des_seuls_elements_verifies(monkeypatch, db) -> None:
    vu = _modele(monkeypatch, "Un cambriolage est commis à Biviers. Votre client, Lucas MARTINON, reconnaît avoir attendu dans la voiture.")
    resume.generer_resume(db, Config(offline=False), Console(quiet=True))

    assert "Reconnaît avoir attendu dans la voiture" in vu["prompt"], "les déclarations du mis en cause nourrissent le résumé"
    assert "La victime a quitté" not in vu["prompt"], "seules celles du mis en cause"
    assert "jamais apparaître" not in vu["prompt"], "jamais un fait non vérifié"
    assert "3 à 5 phrases" in vu["systeme"]
    assert _resume_en_base(db).startswith("Un cambriolage")


def test_votre_client_seulement_avec_un_seul_mis_en_cause(monkeypatch, db) -> None:
    vu = _modele(monkeypatch, "Résumé.")
    resume.generer_resume(db, Config(offline=False), Console(quiet=True))
    assert "« votre client, Lucas MARTINON »" in vu["prompt"]


def test_plusieurs_mis_en_cause_jamais_votre_client(monkeypatch, db) -> None:
    """Avec des coauteurs, l'outil ne sait pas lequel l'avocat défend."""
    db.execute("INSERT INTO personnes (nom, role) VALUES ('Yannick FONTANEL', 'mis_en_cause')")
    db.commit()
    vu = _modele(monkeypatch, "Résumé.")
    resume.generer_resume(db, Config(offline=False), Console(quiet=True))
    assert "sans jamais écrire « votre client »" in vu["prompt"]
    assert "désigne-la comme" not in vu["prompt"]


def test_resume_qui_qualifie_ecarte(monkeypatch, db) -> None:
    _modele(monkeypatch, "La géolocalisation est entachée de nullité, ce qui fragilise l'accusation.")
    resume.generer_resume(db, Config(offline=False), Console(quiet=True))
    assert _resume_en_base(db) is None


def test_pas_de_resume_hors_ligne(db) -> None:
    resume.generer_resume(db, Config(offline=True), Console(quiet=True))
    assert _resume_en_base(db) is None
