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


# --- Cote tamponnée en tête : elle ne doit pas se coller à la première phrase ---


def test_cote_en_tete_ne_se_colle_pas_a_la_premiere_phrase() -> None:
    """La cote « D3 » apposée en haut de page, puis l'intitulé, puis la
    première phrase : une fois l'intitulé retiré, « D3 » se collait à la
    phrase — citation « D3 Le 14/03/2031 à 08h15, … » introuvable telle
    quelle sur la page, donc rejetée."""
    from depouille.classify import _est_titre
    from depouille.regex_patterns import phrase_contenant, texte_sans_entete

    page = "D3\n\nPROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE\n\nLe 14/03/2031 à 08h15, nous notifions."
    bloc = texte_sans_entete(page, _est_titre)
    assert phrase_contenant(bloc, bloc.index("08h15")) == "Le 14/03/2031 à 08h15, nous notifions."


def test_une_phrase_ne_traverse_jamais_un_intitule_retire() -> None:
    from depouille.classify import _est_titre
    from depouille.regex_patterns import phrase_contenant, texte_sans_entete

    page = "Brigade de Vaucressin\nPROCÈS-VERBAL D'AUDITION\nLe 14/03/2031, audition débutée à 10h00"
    bloc = texte_sans_entete(page, _est_titre)
    assert phrase_contenant(bloc, bloc.index("10h00")) == "Le 14/03/2031, audition débutée à 10h00"
    assert phrase_contenant(bloc, 0) == "Brigade de Vaucressin"


def test_cote_en_marge_retiree_mais_pas_dans_le_corps() -> None:
    from depouille.classify import _est_titre
    from depouille.regex_patterns import texte_sans_entete

    lignes = ["D12", "Le scellé numéro 4 est ouvert.", "Il contient :", "A 12", "un téléphone.", "suite", "fin", "Cote D12"]
    bloc = texte_sans_entete("\n".join(lignes), _est_titre)
    assert not bloc.startswith("D12") and "Cote D12" not in bloc
    assert "A 12" in bloc, "au milieu de la page, « A 12 » n'est pas une cote"


def test_actes_verifies_sur_un_dossier_cote_en_tete(tmp_path) -> None:
    """De bout en bout : placement et notification des droits d'une pièce
    cotée en tête sont retrouvés ET vérifiés."""
    from rich.console import Console

    from depouille.chrono import lancer_chrono
    from depouille.config import Config
    from depouille.db import ouvrir_db

    db = ouvrir_db(tmp_path / "d.db")
    texte = (
        "D3\n\nPROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE\n\n"
        "Le 14/03/2031 à 08h15, nous notifions à Julien MORVANNEC son placement en garde à vue."
    )
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Julien MORVANNEC', 'mis_en_cause')")
    db.execute(
        "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd.pdf', 1, ?, 'x')",
        (texte,),
    )
    db.execute(
        "INSERT INTO pieces (id, type, page_debut, page_fin, personne_principale_id) "
        "VALUES (1, 'PV de notification de placement en garde à vue', 1, 1, 1)"
    )
    db.commit()
    lancer_chrono(db, Config(offline=True, provider="offline"), force=True, console=Console(quiet=True))
    acte = db.execute("SELECT heure, statut_verif, citation FROM evenements_procedure WHERE nature = 'placement_garde_a_vue'").fetchone()
    assert acte["heure"] == "08h15" and acte["statut_verif"] == "verifie"
    assert not acte["citation"].startswith("D3")
