"""Appels au modèle menés de front : un dossier de 2 000 pages en demande
plus d'un millier, de 10 à 30 s chacun — en file indienne, une nuit."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest
from rich.console import Console

import depouille.chrono as chrono_module
from depouille.chrono import _extraire_faits_llm
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ErreurModeOffline, ReponseLLM, appels_paralleles, executer_en_parallele


def test_resultats_rendus_dans_lordre_des_elements(monkeypatch) -> None:
    monkeypatch.setenv("LLM_APPELS_PARALLELES", "4")

    def lent_puis_rapide(n: int) -> int:
        time.sleep(0.05 if n == 0 else 0)  # le premier finit en dernier
        return n * 10

    assert executer_en_parallele(lent_puis_rapide, list(range(6))) == [0, 10, 20, 30, 40, 50]


def test_un_echec_est_rendu_a_sa_place_sans_interrompre_les_autres(monkeypatch) -> None:
    monkeypatch.setenv("LLM_APPELS_PARALLELES", "3")

    def parfois(n: int) -> int:
        if n == 1:
            raise ValueError("réponse illisible")
        return n

    resultats = executer_en_parallele(parfois, [0, 1, 2])
    assert resultats[0] == 0 and resultats[2] == 2
    assert isinstance(resultats[1], ValueError)


def test_parallelisme_borne(monkeypatch) -> None:
    """Au-delà du palier de débit du compte, le fournisseur refuse les
    appels : le nombre d'appels simultanés doit rester plafonné."""
    monkeypatch.setenv("LLM_APPELS_PARALLELES", "3")
    en_cours, pic, verrou = [0], [0], threading.Lock()

    def appel(_):
        with verrou:
            en_cours[0] += 1
            pic[0] = max(pic[0], en_cours[0])
        time.sleep(0.02)
        with verrou:
            en_cours[0] -= 1

    executer_en_parallele(appel, list(range(12)))
    assert pic[0] == 3


def test_reglage_du_parallelisme(monkeypatch) -> None:
    monkeypatch.delenv("LLM_APPELS_PARALLELES", raising=False)
    assert appels_paralleles() == 4
    for valeur, attendu in (("8", 8), ("0", 1), ("n'importe quoi", 4)):
        monkeypatch.setenv("LLM_APPELS_PARALLELES", valeur)
        assert appels_paralleles() == attendu


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return ouvrir_db(tmp_path / "test.db")


def test_faits_de_toutes_les_pieces_extraits_en_parallele_et_enregistres_dans_lordre(monkeypatch, db) -> None:
    monkeypatch.setenv("LLM_APPELS_PARALLELES", "4")
    for numero in range(1, 9):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
            "VALUES (?, 'dossier.pdf', ?, ?, 'x')",
            (numero, numero, f"page {numero} " + "mot " * 1900),  # une page par lot
        )
        db.execute(
            "INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (?, \"PV d'audition\", ?, ?)",
            (numero, numero, numero),
        )
    db.commit()

    en_cours, pic, verrou = [0], [0], threading.Lock()

    class Provider:
        def appeler(self, systeme, prompt, modele):
            page = int(prompt.split("]")[0].removeprefix("[page "))
            with verrou:
                en_cours[0] += 1
                pic[0] = max(pic[0], en_cours[0])
            time.sleep(0.03 * (9 - page))  # les premières pages répondent en dernier
            with verrou:
                en_cours[0] -= 1
            fait = {"page": page, "citation": "c", "description": "d", "personne_source": ""}
            return ReponseLLM(texte=json.dumps([fait]), tokens_in=1, tokens_out=1)

    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: Provider())
    compteur = {"tokens_in": 0, "tokens_out": 0}
    pieces = db.execute("SELECT * FROM pieces ORDER BY page_debut").fetchall()
    assert _extraire_faits_llm(db, Config(offline=False), pieces, Console(quiet=True), compteur) == 8

    assert pic[0] > 1, "les lots partent de front"
    assert [r[0] for r in db.execute("SELECT page FROM evenements_faits ORDER BY id")] == list(range(1, 9))
    assert compteur == {"tokens_in": 8, "tokens_out": 8}


def test_mode_hors_ligne_toujours_bloquant(monkeypatch, db) -> None:
    """Le filet de sécurité hors ligne ne doit pas être avalé par la
    parallélisation comme un simple lot en échec."""
    db.execute(
        "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
        "VALUES (1, 'dossier.pdf', 1, 'texte', 'x')"
    )
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, \"PV d'audition\", 1, 1)")
    db.commit()

    class Provider:
        def appeler(self, systeme, prompt, modele):
            raise ErreurModeOffline("hors ligne")

    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: Provider())
    with pytest.raises(ErreurModeOffline):
        _extraire_faits_llm(
            db, Config(offline=False), db.execute("SELECT * FROM pieces").fetchall(),
            Console(quiet=True), {"tokens_in": 0, "tokens_out": 0},
        )
