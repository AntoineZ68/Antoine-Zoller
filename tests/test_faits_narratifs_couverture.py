"""Régression sur un vrai dossier testé par l'utilisateur (affaire
financière) : seule la pièce d'audition produisait des faits — le
signalement du commissaire aux comptes, le relevé bancaire et l'email saisi,
pourtant au cœur de l'affaire, ne généraient jamais rien parce que
l'extraction de faits narratifs ne s'appliquait qu'à une liste fermée de
quatre types de PV. Une pièce que la classification ne rattache à aucun
type de PV connu (signalement Art. 40, pièce saisie, relevé bancaire...)
doit quand même pouvoir produire des faits — seules les pièces purement
procédurales ou couvertes par le secret professionnel en sont exclues."""

from __future__ import annotations

import sqlite3

import depouille.chrono as chrono_module
from depouille.chrono import TYPES_SANS_FAITS_NARRATIFS, _extraire_faits_llm
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM
from rich.console import Console


class FauxProvider:
    def __init__(self, reponse_json: str) -> None:
        self.reponse_json = reponse_json
        self.appels = 0

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        self.appels += 1
        return ReponseLLM(texte=self.reponse_json, tokens_in=10, tokens_out=10)


def _db_avec_piece(tmp_path, type_piece: str, texte_page: str) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "test.db")
    db.execute(
        "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
        "VALUES (1, 'source.pdf', 1, ?, 'empreinte')",
        (texte_page,),
    )
    db.execute(
        "INSERT INTO pieces (type, page_debut, page_fin) VALUES (?, 1, 1)",
        (type_piece,),
    )
    db.commit()
    return db


def test_piece_non_reconnue_comme_pv_produit_quand_meme_des_faits(monkeypatch, tmp_path) -> None:
    texte = (
        "OBJET : Révélation de faits délictueux en application de l'article 40 du Code "
        "de procédure pénale.\n"
        "Les investigations n'ont permis d'obtenir aucun livrable, aucun rapport de "
        "conseil, ni le moindre contrat.\n"
    )
    db = _db_avec_piece(tmp_path, "Pièce de procédure – autre", texte)
    faux = FauxProvider(
        '[{"page": 1, "citation": "Les investigations n\'ont permis d\'obtenir aucun '
        'livrable, aucun rapport de conseil, ni le moindre contrat.", '
        '"description": "Absence de justificatif des prestations.", "personne_source": ""}]'
    )
    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: faux)

    pieces = db.execute("SELECT * FROM pieces").fetchall()
    nb = _extraire_faits_llm(db, Config(offline=False, provider="anthropic"), pieces, Console(), {"tokens_in": 0, "tokens_out": 0})

    assert faux.appels == 1
    assert nb == 1
    assert db.execute("SELECT COUNT(*) FROM evenements_faits").fetchone()[0] == 1


def test_entretien_avocat_exclu_par_secret_professionnel(monkeypatch, tmp_path) -> None:
    assert "PV d'entretien avocat" in TYPES_SANS_FAITS_NARRATIFS
    db = _db_avec_piece(tmp_path, "PV d'entretien avocat", "Entretien confidentiel avec le client.\n")
    faux = FauxProvider("[]")
    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: faux)

    pieces = db.execute("SELECT * FROM pieces").fetchall()
    nb = _extraire_faits_llm(db, Config(offline=False, provider="anthropic"), pieces, Console(), {"tokens_in": 0, "tokens_out": 0})

    assert faux.appels == 0
    assert nb == 0
