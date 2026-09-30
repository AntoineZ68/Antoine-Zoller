"""La cote est la référence que l'avocat retrouve tamponnée sur le papier,
et la démo présentée aux avocats l'affiche sur chaque pièce. L'ancienne
règle exigeait « cote » + lettre + au moins deux chiffres, sans
ponctuation : elle ne reconnaissait AUCUNE cote du dossier d'essai fourni
par l'utilisateur (« COTE : D. 1 »)."""

from __future__ import annotations

import pytest

from depouille.regex_patterns import cote_de_base, detecter_cote


@pytest.mark.parametrize("texte, attendu", [
    ("COTE : D. 1", "D1"),
    ("COTE : D.12", "D12"),
    ("Cote D12", "D12"),
    ("cote n° D 45/2", "D45/2"),
    ("Cote n°D7", "D7"),
    ("COTE D-012 / PV INTERROGATOIRE", "D012"),
    ("Cote 45", "45"),
])
def test_ecritures_de_cote_explicites(texte, attendu) -> None:
    assert detecter_cote(texte) == attendu


def test_tampon_de_greffe_en_marge_sans_le_mot_cote() -> None:
    assert detecter_cote("D 12\nPROCÈS-VERBAL D'AUDITION\nL'an deux mille...") == "D12"
    assert detecter_cote("texte du PV\nsuite\nDont procès-verbal.\nD.45/3") == "D45/3"


def test_ligne_isolee_au_milieu_de_la_page_nest_pas_une_cote() -> None:
    """Débris de texte pivoté observé sur une facture saisie : « B 02 » seul
    sur sa ligne, au milieu de la page."""
    page = "\n".join(["en-tête", "ligne", "ligne", "corps", "B 02", "corps", "pied", "pied", "pied"])
    assert detecter_cote(page) is None


@pytest.mark.parametrize("texte", [
    "l'affaire est cotée 2 fois",
    "fracture de la côte 3",
    "cote de popularité 12",
    "N° PARQUET 2026/00482",
    "autoroute\nA7 direction Lyon",
    "le véhicule est coté D5 à l'Argus",
])
def test_pas_de_fausse_cote(texte) -> None:
    assert detecter_cote(texte) is None


def test_cote_de_la_piece_sans_sous_cote() -> None:
    assert cote_de_base("D45/3") == "D45"
    assert cote_de_base("D12") == "D12"
    assert cote_de_base(None) is None


def test_piece_prend_la_cote_de_sa_deuxieme_page(tmp_path) -> None:
    """Le tampon manque souvent sur la page de garde, ou y est illisible,
    alors qu'il figure sur les pages suivantes de la même pièce."""
    from rich.console import Console

    from depouille.classify import lancer_classification
    from depouille.config import Config
    from depouille.db import ouvrir_db
    from depouille.regex_patterns import detecter_cote

    db = ouvrir_db(tmp_path / "t.db")
    pages = [
        "PROCÈS-VERBAL D'AUDITION DE TÉMOIN\nLe 13/02/2026 à 14h00, nous entendons M. CHABERT René.\nIl déclare avoir vu un break blanc.",
        "Suite de l'audition. Le témoin précise la couleur du véhicule.\nLecture faite, persiste et signe.\nD 2/2",
    ]
    for n, texte in enumerate(pages, 1):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256, cote_detectee) "
            "VALUES (?, 'd.pdf', ?, ?, 'x', ?)",
            (n, n, texte, detecter_cote(texte)),
        )
    db.commit()

    lancer_classification(db, Config(offline=True), force=False, console=Console(quiet=True))

    pieces = db.execute("SELECT page_debut, page_fin, cote FROM pieces ORDER BY page_debut").fetchall()
    assert [(p["page_debut"], p["page_fin"]) for p in pieces] == [(1, 2)], "une seule pièce de deux pages"
    assert pieces[0]["cote"] == "D2"
