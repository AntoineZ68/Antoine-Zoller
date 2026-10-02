"""L'avocat désigne lui-même son client ; l'outil ne le devine jamais."""

from __future__ import annotations

import pytest

from depouille.client import PersonneInconnue, designer_client
from depouille.db import appliquer_migrations, ouvrir_db


@pytest.fixture
def db(tmp_path):
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO personnes (nom, role) VALUES ('Lucas MARTINON', 'mis_en_cause'), "
               "('Yannick FONTANEL', 'mis_en_cause'), ('Odile SERMET', 'victime')")
    db.execute("INSERT INTO resume_affaire (id, texte, genere_le) VALUES (1, 'Votre client, Lucas MARTINON, ...', 'x')")
    db.commit()
    return db


def _clients(db):
    return [r[0] for r in db.execute("SELECT nom FROM personnes WHERE est_client = 1")]


def test_un_seul_client_a_la_fois(db) -> None:
    designer_client(db, "Lucas MARTINON")
    designer_client(db, "Yannick FONTANEL")
    assert _clients(db) == ["Yannick FONTANEL"]


def test_changer_de_client_supprime_le_resume_qui_nommait_l_ancien(db) -> None:
    """Un résumé qui dirait « votre client » de la mauvaise personne est
    pire que pas de résumé : il est supprimé, à régénérer."""
    designer_client(db, "Yannick FONTANEL")
    assert db.execute("SELECT COUNT(*) FROM resume_affaire").fetchone()[0] == 0


def test_la_victime_peut_etre_le_client(db) -> None:
    designer_client(db, "Odile SERMET")
    assert _clients(db) == ["Odile SERMET"]


def test_retirer_la_designation(db) -> None:
    designer_client(db, "Lucas MARTINON")
    designer_client(db, None)
    assert _clients(db) == []


def test_personne_inconnue_refusee(db) -> None:
    with pytest.raises(PersonneInconnue):
        designer_client(db, "Quelqu'un INVENTÉ")
    assert _clients(db) == []


def test_base_d_un_ancien_dossier_mise_a_niveau(tmp_path) -> None:
    """La base d'un dossier traité avant l'ajout de la colonne doit rester
    lisible et désignable."""
    import sqlite3

    chemin = tmp_path / "ancien.db"
    ancien = sqlite3.connect(chemin)
    ancien.executescript(
        "CREATE TABLE personnes (id INTEGER PRIMARY KEY, nom TEXT NOT NULL, role TEXT NOT NULL, alias_json TEXT NOT NULL DEFAULT '[]');"
        "CREATE TABLE pieces (id INTEGER PRIMARY KEY, type TEXT NOT NULL, page_debut INTEGER NOT NULL, page_fin INTEGER NOT NULL);"
        "INSERT INTO personnes (nom, role) VALUES ('Lucas MARTINON', 'mis_en_cause');"
    )
    ancien.commit()
    appliquer_migrations(ancien)
    colonnes = {r[1] for r in ancien.execute("PRAGMA table_info(personnes)")}
    assert "est_client" in colonnes
    assert "titre" in {r[1] for r in ancien.execute("PRAGMA table_info(pieces)")}
