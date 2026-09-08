"""Teste le repli sur le modèle pour identifier la personne d'une pièce
sans tag de rôle explicite — avec un fournisseur simulé, sans appel réseau
réel. Vérifie surtout que la règle de vérifiabilité s'applique aussi à une
identification par le modèle : un nom que le modèle invente, absent du
texte, doit être rejeté exactement comme une citation non retrouvée."""

from __future__ import annotations

import sqlite3

import pytest

import depouille.classify as classify_module
from depouille.classify import identifier_personne_principale
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
        return ReponseLLM(texte=self.reponse_json, tokens_in=42, tokens_out=17)


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return ouvrir_db(tmp_path / "test.db")


@pytest.fixture
def config_en_ligne() -> Config:
    return Config(offline=False, provider="anthropic", modele_classification="modele-test")


TEXTE_AUDITION = (
    "PROCES-VERBAL D'AUDITION DU GARDE A VUE\n"
    "Le 12 novembre 2024 à 15 h 00.\n"
    "Q. : Que pouvez-vous nous dire ?\n"
    "R. : C'est pas à moi, un type de Roubaix m'a donné les clés. "
    "Je m'appelle Yanis BELKACEM.\n"
)


def test_identification_llm_acceptee_si_nom_present(monkeypatch, db, config_en_ligne) -> None:
    faux = FauxProvider('{"nom": "Yanis BELKACEM", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "PV d'audition", TEXTE_AUDITION, Console(quiet=True), compteur
    )

    assert methode == "llm"
    assert personne_id is not None
    assert faux.appels == 1
    assert compteur["tokens_in"] == 42

    row = db.execute("SELECT nom, role FROM personnes WHERE id = ?", (personne_id,)).fetchone()
    assert row["nom"] == "Yanis BELKACEM"
    assert row["role"] == "mis_en_cause"


def test_identification_llm_rejetee_si_nom_invente(monkeypatch, db, config_en_ligne) -> None:
    """Le modèle renvoie un nom qui n'apparaît nulle part dans le texte de
    la pièce : rejeté, comme une citation non vérifiée."""
    faux = FauxProvider('{"nom": "Quelquun DINVENTE", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "PV d'audition", TEXTE_AUDITION, Console(quiet=True), compteur
    )

    assert personne_id is None
    assert methode == "non_identifie"
    assert db.execute("SELECT COUNT(*) FROM personnes").fetchone()[0] == 0


def test_identification_llm_rejetee_si_role_invalide(monkeypatch, db, config_en_ligne) -> None:
    faux = FauxProvider('{"nom": "Yanis BELKACEM", "role": "coupable"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "PV d'audition", TEXTE_AUDITION, Console(quiet=True), compteur
    )

    assert personne_id is None
    assert methode == "non_identifie"


def test_pas_dappel_llm_hors_types_audition(monkeypatch, db, config_en_ligne) -> None:
    """Le repli LLM ne doit se déclencher que pour les auditions — pas pour
    un type de pièce où on considère qu'une absence de tag signifie
    simplement "non identifié"."""
    faux = FauxProvider('{"nom": "Yanis BELKACEM", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "Rapport d'expertise", TEXTE_AUDITION, Console(quiet=True), compteur
    )

    assert personne_id is None
    assert methode == "non_identifie"
    assert faux.appels == 0


def test_identification_par_personne_deja_connue(monkeypatch, db, config_en_ligne) -> None:
    """Régression trouvée en testant sur un dossier réel : une audition qui
    ne renomme jamais la personne ("le gardé à vue" seulement) doit quand
    même être attribuée si elle correspond à une personne déjà identifiée
    ailleurs dans le dossier — sans que ce soit une invention, puisque
    cette identité est déjà vérifiée."""
    db.execute("INSERT INTO personnes (nom, role) VALUES (?, ?)", ("Yanis BELKACEM", "mis_en_cause"))
    db.commit()

    texte_sans_nom = (
        "PROCES-VERBAL D'AUDITION DU GARDE A VUE\n"
        "Le 12 novembre 2024 à 15 h 00.\n"
        "Devant nous, Capitaine François V., OPJ.\n"
        "R. : C'est pas à moi, un type de Roubaix m'a donné les clés.\n"
    )
    faux = FauxProvider('{"nom": "Yanis BELKACEM", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "PV d'audition", texte_sans_nom, Console(quiet=True), compteur
    )

    assert methode == "llm"
    assert personne_id is not None
    # Une seule personne en base : l'appel a bien réutilisé l'existante,
    # pas créé un doublon.
    assert db.execute("SELECT COUNT(*) FROM personnes").fetchone()[0] == 1


def test_identification_rejetee_si_ni_connue_ni_dans_le_texte(monkeypatch, db, config_en_ligne) -> None:
    faux = FauxProvider('{"nom": "Un Inconnu", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)

    texte_sans_nom = "PROCES-VERBAL D'AUDITION DU GARDE A VUE\nR. : C'est pas à moi.\n"
    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_en_ligne, "PV d'audition", texte_sans_nom, Console(quiet=True), compteur
    )
    assert personne_id is None
    assert methode == "non_identifie"


def test_pas_dappel_llm_en_offline(monkeypatch, db) -> None:
    faux = FauxProvider('{"nom": "Yanis BELKACEM", "role": "mis_en_cause"}')
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: faux)
    config_offline = Config(offline=True, provider="offline")

    compteur = {"tokens_in": 0, "tokens_out": 0}
    personne_id, methode = identifier_personne_principale(
        db, config_offline, "PV d'audition", TEXTE_AUDITION, Console(quiet=True), compteur
    )

    assert personne_id is None
    assert methode == "non_identifie"
    assert faux.appels == 0
