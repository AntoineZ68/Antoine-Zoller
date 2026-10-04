"""« Interroger le dossier » : une réponse libre du modèle n'est affichée que
si elle s'appuie sur au moins une citation vérifiée dans une page du
dossier, et jamais avec une qualification juridique."""

from __future__ import annotations

import json
import sqlite3

import pytest

import depouille.questions as questions
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM

PAGES = {
    1: "PROCÈS-VERBAL DE PLAINTE. Mme SERMET Odile déclare avoir quitté son domicile de Biviers à 13h30.",
    2: "Le témoin CHABERT René décrit un break blanc stationné devant le portail.",
    3: "Exploitation LAPI : véhicule Skoda Octavia Combi, couleur gris métallisé, immatriculé ZV-553-RT.",
}


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "test.db")
    for n, texte in PAGES.items():
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (?, 'd.pdf', ?, ?, 'x')",
            (n, n, texte),
        )
    db.commit()
    return db


def _modele(monkeypatch, reponse: dict | str) -> dict:
    vu = {}

    class Faux:
        def appeler(self, systeme, prompt, modele):
            vu["prompt"] = prompt
            texte = reponse if isinstance(reponse, str) else json.dumps(reponse)
            return ReponseLLM(texte=texte, tokens_in=1, tokens_out=1)

    monkeypatch.setattr(questions, "obtenir_provider", lambda config: Faux())
    return vu


def test_reponse_sourcee_affichee_avec_ses_citations(monkeypatch, db) -> None:
    _modele(monkeypatch, {
        "reponse": "Le témoin décrit un break blanc ; la LAPI relève une Skoda gris métallisé.",
        "citations": [
            {"page": 2, "citation": "décrit un break blanc"},
            {"page": 3, "citation": "couleur gris métallisé"},
        ],
    })
    r = questions.repondre_question(db, Config(offline=False), "Quel véhicule ?")
    assert r["statut"] == "sourcee"
    assert [c["page"] for c in r["citations"]] == [2, 3]


def test_citation_inventee_ecartee(monkeypatch, db) -> None:
    _modele(monkeypatch, {
        "reponse": "Un break blanc.",
        "citations": [
            {"page": 2, "citation": "décrit un break blanc"},
            {"page": 2, "citation": "le suspect a avoué"},
        ],
    })
    r = questions.repondre_question(db, Config(offline=False), "Quel véhicule ?")
    assert r["citations"] == [{"page": 2, "citation": "décrit un break blanc"}]


def test_citation_attribuee_a_la_mauvaise_page_ecartee(monkeypatch, db) -> None:
    _modele(monkeypatch, {"reponse": "Un break blanc.", "citations": [{"page": 1, "citation": "décrit un break blanc"}]})
    r = questions.repondre_question(db, Config(offline=False), "Quel véhicule ?")
    assert r["statut"] == "introuvable"


def test_reponse_sans_aucune_citation_verifiee_jamais_affichee(monkeypatch, db) -> None:
    """Une affirmation que rien ne permet de contrôler n'est pas montrée."""
    _modele(monkeypatch, {"reponse": "Le client était à Lyon ce jour-là.", "citations": []})
    r = questions.repondre_question(db, Config(offline=False), "Où était le client ?")
    assert r["statut"] == "introuvable"
    assert "Lyon" not in r["reponse"]


def test_modele_qui_ne_sait_pas(monkeypatch, db) -> None:
    _modele(monkeypatch, {"reponse": None, "citations": []})
    assert questions.repondre_question(db, Config(offline=False), "Date de mise en examen ?")["statut"] == "introuvable"


def test_reponse_illisible(monkeypatch, db) -> None:
    _modele(monkeypatch, "je ne suis pas du JSON")
    assert questions.repondre_question(db, Config(offline=False), "?")["statut"] == "introuvable"


@pytest.mark.parametrize("formulation", [
    "La géolocalisation est entachée de nullité.",
    "Cet acte est irrégulier.",
    "Cette nullité est invocable par votre client.",
])
def test_qualification_juridique_retiree_passages_conserves(monkeypatch, db, formulation) -> None:
    """Filtre déterministe : même si le modèle ignore la consigne, aucune
    qualification n'atteint l'écran — seuls les passages cités restent."""
    _modele(monkeypatch, {"reponse": formulation, "citations": [{"page": 3, "citation": "immatriculé ZV-553-RT"}]})
    r = questions.repondre_question(db, Config(offline=False), "Y a-t-il une nullité ?")
    assert r["statut"] == "passages"
    assert not questions.RE_QUALIFICATION.search(r["reponse"])
    assert r["citations"] == [{"page": 3, "citation": "immatriculé ZV-553-RT"}]


def test_petit_dossier_soumis_en_entier(monkeypatch, db) -> None:
    vu = _modele(monkeypatch, {"reponse": None, "citations": []})
    questions.repondre_question(db, Config(offline=False), "Quel véhicule ?")
    assert all(f"[page {n}]" in vu["prompt"] for n in PAGES)


def test_gros_dossier_pages_les_plus_pertinentes() -> None:
    pages = [{"numero_global": n, "texte": f"page {n} " + "procès-verbal ordinaire " * 400} for n in range(1, 40)]
    pages[24]["texte"] += " Skoda Octavia gris métallisé"
    retenues = questions.pages_pertinentes(pages, "Quelle est la couleur de la Skoda ?", budget=12_000)
    assert retenues and retenues[0]["numero_global"] == 25


def test_question_vide(db) -> None:
    assert questions.repondre_question(db, Config(offline=False), "   ")["statut"] == "introuvable"


def test_mon_client_designe_par_l_avocat(monkeypatch, db) -> None:
    db.execute("INSERT INTO personnes (nom, role, est_client) VALUES ('Lucas MARTINON', 'mis_en_cause', 1)")
    db.commit()
    vu = _modele(monkeypatch, {"reponse": None, "citations": []})
    questions.repondre_question(db, Config(offline=False), "Mon client a-t-il vu un avocat ?")
    assert "L'avocat défend Lucas MARTINON" in vu["prompt"]


def test_sans_client_designe_mon_client_n_est_pas_devine(monkeypatch, db) -> None:
    vu = _modele(monkeypatch, {"reponse": None, "citations": []})
    questions.repondre_question(db, Config(offline=False), "Mon client a-t-il vu un avocat ?")
    assert "n'a pas désigné son client" in vu["prompt"]


def test_reponse_qui_avance_un_element_absent_des_pages_citees(monkeypatch, db) -> None:
    """Le texte de la réponse est rédigé : une heure qui ne figure sur aucune
    page citée n'est pas affichée, seuls les passages vérifiés le sont."""
    _modele(monkeypatch, {
        "reponse": "Mme SERMET a quitté son domicile de Biviers à 14h15.",
        "citations": [{"page": 1, "citation": "déclare avoir quitté son domicile de Biviers à 13h30"}],
    })
    r = questions.repondre_question(db, Config(offline=False), "Quand la victime est-elle partie ?")
    assert r["statut"] == "passages"
    assert r["reponse"] == questions.MESSAGE_PASSAGES
    assert [c["page"] for c in r["citations"]] == [1]


def test_consigne_de_donner_chaque_version(monkeypatch, db) -> None:
    assert "donne chaque version avec sa page" in questions.PROMPT_SYSTEME


def test_nom_complete_par_une_personne_identifiee(monkeypatch, db) -> None:
    """La page citée dit « René » ; « René CHABERT » est une personne
    identifiée du dossier : la réponse n'invente rien."""
    db.execute("INSERT INTO personnes (nom, role) VALUES ('René CHABERT', 'témoin')")
    db.execute("UPDATE pages SET texte = 'René décrit un break blanc stationné devant le portail.' WHERE numero_global = 2")
    db.commit()
    _modele(monkeypatch, {
        "reponse": "René CHABERT décrit un break blanc.",
        "citations": [{"page": 2, "citation": "René décrit un break blanc"}],
    })
    assert questions.repondre_question(db, Config(offline=False), "Qui a vu le break ?")["statut"] == "sourcee"
