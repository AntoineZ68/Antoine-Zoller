"""Résumé détaillé : chaque phrase renvoie à des éléments déjà vérifiés, et
une phrase qui ajoute un nom, un lieu ou un nombre absent de ses propres
sources est retirée."""

from __future__ import annotations

import json
import sqlite3

import pytest
from rich.console import Console

import depouille.resume_detaille as rd
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (2, 'd.pdf', 2, 'x', 'x')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, date_apparente, heure_apparente) VALUES (1, 'PV de plainte', 2, 2, '13/02/2026', '09h15')")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'SERMET Odile', 'victime'), (2, 'MARTINON Lucas', 'mis_en_cause')")
    db.execute(
        "INSERT INTO evenements_faits (id, piece_id, page, citation, personne_id_source, description, statut_verif) VALUES "
        "(1, 1, 2, 'En rentrant à 16h45, j''ai trouvé la porte-fenêtre du salon fracturée.', 1, 'La victime découvre l''effraction', 'verifie'), "
        "(2, 1, 2, 'une montre Longines numéro de série 48 217 559', 1, 'Une montre Longines est volée', 'verifie'), "
        "(3, 1, 2, 'texte rejeté', 1, 'Fait rejeté à la vérification', 'rejete')"
    )
    db.execute(
        "INSERT INTO declarations (id, personne_id, piece_id, page, citation, point_factuel, statut_verif) VALUES "
        "(1, 2, 1, 2, 'Je suis resté dans la voiture.', 'Reste dans la voiture', 'verifie')"
    )
    db.commit()
    return db


def _modele(monkeypatch, sections) -> dict:
    vu = {}

    class Faux:
        def appeler(self, systeme, prompt, modele):
            vu["prompt"] = prompt
            return ReponseLLM(texte=json.dumps({"sections": sections}), tokens_in=1, tokens_out=1)

    monkeypatch.setattr(rd, "obtenir_provider", lambda config: Faux())
    return vu


def _phrases(db):
    return [(r["section_titre"], r["texte"], json.loads(r["sources_json"]))
            for r in db.execute("SELECT * FROM resume_detaille ORDER BY section_ordre, phrase_ordre")]


def test_seuls_les_elements_verifies_sont_transmis(monkeypatch, db) -> None:
    vu = _modele(monkeypatch, [])
    rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True))
    assert "[F1]" in vu["prompt"] and "[F2]" in vu["prompt"] and "[D1]" in vu["prompt"]
    assert "Fait rejeté" not in vu["prompt"]
    assert "13/02/2026" in vu["prompt"], "la date de la pièce accompagne le fait"


def test_phrase_sourcee_conservee_avec_ses_citations(monkeypatch, db) -> None:
    _modele(monkeypatch, [{"titre": "Les faits", "phrases": [
        {"texte": "Le 13/02/2026, SERMET Odile découvre à 16h45 sa porte-fenêtre fracturée ; une montre Longines est volée.",
         "sources": ["F1", "F2"]},
    ]}])
    n = rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True))
    assert n == 1
    (section, texte, sources), = _phrases(db)
    assert section == "Les faits"
    assert [s["page"] for s in sources] == [2, 2]
    assert sources[1]["citation"].startswith("une montre Longines")


@pytest.mark.parametrize("phrase, raison", [
    ({"texte": "La victime découvre l'effraction.", "sources": []}, "aucune source"),
    ({"texte": "La victime découvre l'effraction.", "sources": ["F99"]}, "source inexistante"),
    ({"texte": "La victime découvre l'effraction.", "sources": ["F3"]}, "source rejetée à la vérification"),
    ({"texte": "SERMET Odile découvre l'effraction à Biviers.", "sources": ["F1"]}, "lieu absent des sources"),
    ({"texte": "Une montre Longines est volée le 12/02.", "sources": ["F2"]}, "date absente des sources"),
    ({"texte": "La montre Longines est volée par MARTINON Lucas.", "sources": ["F2"]}, "nom venu d'un autre élément"),
    ({"texte": "L'effraction est entachée d'une irrégularité.", "sources": ["F1"]}, "qualification juridique"),
])
def test_phrase_douteuse_retiree(monkeypatch, db, phrase, raison) -> None:
    _modele(monkeypatch, [{"titre": "Les faits", "phrases": [phrase]}])
    assert rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True)) == 0, raison
    assert _phrases(db) == []


def test_une_phrase_retiree_n_emporte_pas_le_reste(monkeypatch, db) -> None:
    _modele(monkeypatch, [
        {"titre": "Les faits", "phrases": [
            {"texte": "Une montre Longines est volée.", "sources": ["F2"]},
            {"texte": "Le cambriolage a lieu à Grenoble.", "sources": ["F1"]},
        ]},
        {"titre": "Les déclarations", "phrases": [
            {"texte": "MARTINON Lucas déclare être resté dans la voiture.", "sources": ["D1"]},
        ]},
        {"titre": "Section inventée", "phrases": [{"texte": "Une montre Longines.", "sources": ["F2"]}]},
    ])
    rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True))
    assert [(s, t) for s, t, _ in _phrases(db)] == [
        ("Les faits", "Une montre Longines est volée."),
        ("Les déclarations", "MARTINON Lucas déclare être resté dans la voiture."),
    ]


def test_regeneration_remplace_l_ancien(monkeypatch, db) -> None:
    _modele(monkeypatch, [{"titre": "Les faits", "phrases": [{"texte": "Une montre Longines est volée.", "sources": ["F2"]}]}])
    rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True))
    rd.generer_resume_detaille(db, Config(offline=False), Console(quiet=True))
    assert len(_phrases(db)) == 1


def test_hors_ligne_aucun_resume_detaille(db) -> None:
    assert rd.generer_resume_detaille(db, Config(offline=True), Console(quiet=True)) == 0
