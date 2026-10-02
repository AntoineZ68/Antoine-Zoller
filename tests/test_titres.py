"""Intitulés d'index : le modèle propose, la vérification écarte tout
intitulé qui contiendrait un nom ou un nombre absent de la pièce."""

from __future__ import annotations

import json
import sqlite3

import pytest
from rich.console import Console

import depouille.titres as titres
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM
from depouille.titres import intitule_verifie

PIECE_REQUISITION = (
    "RÉQUISITION À PERSONNE QUALIFIÉE\nRequérons la société ORBIS Télécom de nous communiquer "
    "l'identité du titulaire de la ligne 06 39 98 14 52.\nRéponse : abonnement au nom de FONTANEL Yannick."
)


@pytest.mark.parametrize("titre", [
    "Réquisition ORBIS (ligne 14 52) et réponse",
    "Réquisition à ORBIS Télécom — ligne de FONTANEL Yannick",
    "Réquisition à personne qualifiée",
])
def test_intitule_fidele_accepte(titre) -> None:
    assert intitule_verifie(titre, PIECE_REQUISITION)


@pytest.mark.parametrize("titre, raison", [
    ("Réquisition NEXALINE (ligne 14 52)", "opérateur inventé"),
    ("Réquisition ORBIS (ligne 63 08)", "numéro inventé"),
    ("Réquisition ORBIS — ligne de MARTINON Lucas", "personne inventée"),
    ("Réquisition irrégulière à ORBIS", "qualification juridique"),
    ("R" * 120, "trop long"),
    ("", "vide"),
])
def test_intitule_invente_rejete(titre, raison) -> None:
    assert not intitule_verifie(titre, PIECE_REQUISITION), raison


def test_abreviation_generique_toleree() -> None:
    assert intitule_verifie("PV de pose de la balise", "PROCÈS-VERBAL. Procédons à la pose de la balise.")


def test_accents_et_casse_tolérés() -> None:
    assert intitule_verifie("Audition de témoin — CHABERT René", "Nous entendons M. Chabert Rene, témoin.")


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd.pdf', 1, ?, 'x')", (PIECE_REQUISITION,))
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (2, 'd.pdf', 2, 'PROCÈS-VERBAL D''AUDITION. Nous entendons CHABERT René.', 'x')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, 'Réquisition', 1, 1), (2, 'PV d''audition', 2, 2)")
    db.commit()
    return db


def test_titrer_pieces_retient_seulement_les_intitules_verifies(monkeypatch, db) -> None:
    class Faux:
        def appeler(self, systeme, prompt, modele):
            return ReponseLLM(texte=json.dumps({"titres": [
                {"id": 1, "titre": "Réquisition ORBIS (ligne 14 52) et réponse"},
                {"id": 2, "titre": "Audition de témoin — DUPONT Marcel"},
                {"id": 99, "titre": "Pièce qui n'existe pas"},
            ]}), tokens_in=1, tokens_out=1)

    monkeypatch.setattr(titres, "obtenir_provider", lambda config: Faux())
    compteur = {"tokens_in": 0, "tokens_out": 0}
    retenus = titres.titrer_pieces(db, Config(offline=False), Console(quiet=True), compteur)

    assert retenus == 1
    lignes = {r["id"]: r["titre"] for r in db.execute("SELECT id, titre FROM pieces")}
    assert lignes == {1: "Réquisition ORBIS (ligne 14 52) et réponse", 2: None}


def test_hors_ligne_aucun_appel(db) -> None:
    assert titres.titrer_pieces(db, Config(offline=True), Console(quiet=True), {"tokens_in": 0, "tokens_out": 0}) == 0
