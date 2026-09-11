"""Recoupement d'identifiants "durs" (téléphone, plaque, IBAN, adresse) —
ajouté après un retour utilisateur relayant une analyse externe (Gemini) sur
un vrai dossier de test (escroquerie Colmar) : le rapprochement de
déclarations basé sur le sens des phrases passait totalement à côté du fait
qu'un même numéro de téléphone et une même adresse relient une facture de
sous-traitant soi-disant indépendant au domicile du principal suspect —
exactement le genre de correspondance EXACTE qu'un LLM généraliste, focalisé
sur la sémantique, manque facilement, et qu'une correspondance déterministe
retrouve avec certitude."""

from __future__ import annotations

import sqlite3

from depouille.db import ouvrir_db
from depouille.recoupements import detecter_recoupements


def _db_avec_pages(tmp_path, textes_par_page: dict[int, str]) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "test.db")
    for numero, texte in textes_par_page.items():
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
            "VALUES (?, 'source.pdf', ?, ?, ?)",
            (numero, numero, texte, f"empreinte{numero}"),
        )
    db.commit()
    return db


def test_telephone_identique_sur_deux_pages_est_recoupe(tmp_path) -> None:
    db = _db_avec_pages(
        tmp_path,
        {
            4: "Siège social : 12 rue des Vosges, 68000 COLMAR - Téléphone : 06.99.88.77.66",
            5: "Mon numéro de téléphone personnel est le 06.99.88.77.66, vous pouvez me joindre dessus.",
        },
    )
    resultats = detecter_recoupements(db)
    telephones = [e for e in resultats if e.type_entite == "Téléphone"]
    assert len(telephones) == 1
    assert telephones[0].valeur == "06.99.88.77.66"
    pages_trouvees = {o.page for o in telephones[0].occurrences}
    assert pages_trouvees == {4, 5}


def test_meme_ecriture_differente_reconnue_comme_le_meme_numero(tmp_path) -> None:
    """"06.99.88.77.66" et "06 99 88 77 66" désignent le même numéro — la
    comparaison doit ignorer la ponctuation de mise en forme."""
    db = _db_avec_pages(
        tmp_path,
        {1: "Tél : 06.99.88.77.66", 2: "Contact : 06 99 88 77 66"},
    )
    telephones = [e for e in detecter_recoupements(db) if e.type_entite == "Téléphone"]
    assert len(telephones) == 1


def test_plaque_immatriculation_recoupee(tmp_path) -> None:
    db = _db_avec_pages(
        tmp_path,
        {
            1: "circulant à bord d'un SUV Mercedes GLE noir immatriculé WX-999-YZ.",
            5: "Vous êtes venue au commissariat à bord d'un Mercedes GLE noir immatriculé WX-999-YZ.",
        },
    )
    plaques = [e for e in detecter_recoupements(db) if e.type_entite == "Plaque d'immatriculation"]
    assert len(plaques) == 1
    assert plaques[0].valeur == "WX-999-YZ"


def test_adresse_identique_recoupee_malgre_contexte_different(tmp_path) -> None:
    db = _db_avec_pages(
        tmp_path,
        {
            2: "Lieu : Domicile de TARDIEU Jean-Marc, 12 rue des Vosges, 68000 COLMAR",
            4: "Siège social : 12 rue des Vosges, 68000 COLMAR - Téléphone : 06.99.88.77.66",
        },
    )
    adresses = [e for e in detecter_recoupements(db) if e.type_entite == "Adresse"]
    assert len(adresses) == 1
    assert "12 rue des Vosges" in adresses[0].valeur


def test_repetition_sur_une_seule_page_nest_pas_un_recoupement(tmp_path) -> None:
    """Un numéro répété deux fois dans l'en-tête ET la signature d'une même
    lettre ne recoupe rien : il faut au moins deux PAGES différentes."""
    db = _db_avec_pages(
        tmp_path,
        {1: "Tél : 06.99.88.77.66 ... signature ... Tél : 06.99.88.77.66"},
    )
    assert detecter_recoupements(db) == []


def test_valeur_unique_nest_pas_signalee(tmp_path) -> None:
    db = _db_avec_pages(tmp_path, {1: "Tél : 06.99.88.77.66", 2: "Aucun rapport ici."})
    assert detecter_recoupements(db) == []


def test_citation_isole_la_ligne_pas_toute_la_page(tmp_path) -> None:
    """Une facture n'a pas de ponctuation de fin de phrase entre ses
    champs : la citation ne doit jamais remonter jusqu'au haut de la page
    faute de point trouvé avant l'identifiant."""
    db = _db_avec_pages(
        tmp_path,
        {
            4: "FACTURE N° 2026-08-112\n\nBERRADA BTP\n\nSiège social : 12 rue des Vosges, 68000 COLMAR - Téléphone : 06.99.88.77.66",
            5: "Mon numéro de téléphone personnel est le 06.99.88.77.66.",
        },
    )
    telephones = [e for e in detecter_recoupements(db) if e.type_entite == "Téléphone"]
    citation_page_4 = next(o.citation for o in telephones[0].occurrences if o.page == 4)
    assert "FACTURE N°" not in citation_page_4
    assert "Téléphone : 06.99.88.77.66" in citation_page_4
