"""Dossier réel scanné (cas repris d'un dossier de test fourni par
l'utilisateur) : PV paginés en feuillets, objet du PV en tête, bordereau et
notes de l'avocat mêlés à la procédure, nom du mis en cause mal lu par l'OCR,
refus lus comme des demandes, contrôles propres à ce dossier."""

from __future__ import annotations

import pytest

from depouille.actes import fiche_verifiee
from depouille.classify import (
    TYPE_DOCUMENT_AVOCAT,
    _detecter_pieces_par_page,
    _document_avocat,
    _feuillet,
    fusionner_personnes_proches,
    intitule_de_classement,
    meme_personne,
)
from depouille.conformite import detecter_signalements
from depouille.db import ouvrir_db, pages_hors_procedure
from depouille.regex_patterns import detecter_date_acte

ENTETE = "POLICE NATIONALE\nDIRECTION DÉPARTEMENTALE DE LA SÉCURITÉ PUBLIQUE DE L'HÉRAULT\nCommissariat central de Montpellier\n"


def _page(numero: int, texte: str, fichier: str = "d.pdf") -> dict:
    return {"numero_global": numero, "fichier_source": fichier, "texte": texte}


def test_feuillets_decident_des_frontieres() -> None:
    pages = [
        _page(1, ENTETE + "PROCÈS-VERBAL\nObjet : Audition de personne gardée à vue — n° 1\nFeuillet 1/2\nNous, ..."),
        _page(2, ENTETE + "PROCÈS-VERBAL\nObjet : Audition de personne gardée à vue — n° 1\nFeuillet 2/2\nQuestion : ..."),
        _page(3, ENTETE + "PROCÈS-VERBAL\nObjet : Audition de personne gardée à vue — n° 2\nFeuillet 1/1\nNous, ..."),
    ]
    assert [(p["page_debut"], p["page_fin"]) for p in _detecter_pieces_par_page(pages)] == [(1, 2), (3, 3)], (
        "l'en-tête répété sur le feuillet 2 ne doit pas ouvrir une nouvelle pièce"
    )
    assert _feuillet("texte\nPage 3/2") is None, "numéro de feuillet incohérent ignoré"


def test_objet_du_pv_sert_au_classement() -> None:
    texte = ENTETE + "PROCÈS-VERBAL\nObjet : Audition de personne gardée à vue — n° 3\nFeuillet 1/2"
    assert "PROCÈS-VERBAL D'AUDITION DE PERSONNE GARDÉE À VUE" in intitule_de_classement(texte)


def test_notes_de_l_avocat_hors_procedure() -> None:
    notes = "Cabinet de Maître Claire VIDAL — Avocat au barreau de Montpellier\nNotes d'entretien — parloir\nÀ faire : DML"
    assert _document_avocat(notes)
    assert not _document_avocat(ENTETE + "PROCÈS-VERBAL\nNous, OPJ, avisons Maître VIDAL, avocat, par téléphone."), (
        "un PV qui cite l'avocat reste une pièce de la procédure"
    )
    pages = [_page(1, ENTETE + "PROCÈS-VERBAL\nNous, ..."), _page(2, notes), _page(3, "suite du PV sans intitulé")]
    assert [(p["page_debut"], p["page_fin"]) for p in _detecter_pieces_par_page(pages)] == [(1, 1), (2, 2), (3, 3)]


def test_pages_hors_procedure(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, ?, 2, 3)", (TYPE_DOCUMENT_AVOCAT,))
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (2, 'PV d''audition', 4, 4)")
    assert pages_hors_procedure(db) == {2, 3}


@pytest.mark.parametrize("a, b, attendu", [
    ("Yanis DERRADJI", "Yanis DERRADIJI", True),
    ("Yanis DERRADJI", "DERRADII Yanis", True),
    ("Lucas MARTIN", "Lucas MARTINEZ", False),
    ("Lucas MARTIN", "Paul MARTIN", False),
    ("Yanis DERRADJI", "Karim DERRADJI", False),
])
def test_meme_personne(a, b, attendu) -> None:
    assert meme_personne(a, b) is attendu


def test_fusion_des_variantes_ocr(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES "
               "(1, 'd', 1, 'Yanis DERRADJI, Yanis DERRADJI, Yanis DERRADIJI', 'x')")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Yanis DERRADIJI', 'temoin'), "
               "(2, 'DERRADJI Yanis', 'mis_en_cause'), (3, 'Lucas MARTIN', 'victime')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, personne_principale_id) VALUES (1, 'PV', 1, 1, 2)")
    assert fusionner_personnes_proches(db) == 1
    assert [tuple(r) for r in db.execute("SELECT id, nom, role FROM personnes ORDER BY id")] == [
        (1, "Yanis DERRADJI", "mis_en_cause"), (3, "Lucas MARTIN", "victime"),
    ]
    assert db.execute("SELECT personne_principale_id FROM pieces").fetchone()[0] == 1


@pytest.mark.parametrize("citation", [
    "Médecin : « Non. »",
    "Il ne souhaite pas être examiné par un médecin.",
    "L'intéressé refuse l'assistance d'un avocat.",
    "n'a sollicité aucun examen médical",
])
def test_refus_non_lu_comme_demande(citation) -> None:
    nature = "demande_entretien_avocat" if "avocat" in citation else "demande_examen_medical"
    assert fiche_verifiee({"nature": nature, "page": 1, "citation": citation}, None) is None


def test_date_rappelee_n_est_pas_la_date_de_la_piece() -> None:
    texte = ("PROLONGATION DE GARDE À VUE\nGarde à vue de Yanis DERRADJI depuis le 15 mars 2026 à 07h25.\n"
             "Le 16 mars 2026 à 07h05, le procureur autorise la prolongation.")
    assert detecter_date_acte(texte) == "16/03/2026"


def _gav(db, actes: list[tuple]) -> None:
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Yanis DERRADJI', 'mis_en_cause')")
    for nature, date, heure, page, details in actes:
        db.execute(
            "INSERT INTO evenements_procedure (nature, date, heure, personne_id, page, citation, details_json, statut_verif) "
            "VALUES (?, ?, ?, 1, ?, 'x', ?, 'verifie')", (nature, date, heure, page, details),
        )
    db.commit()


def test_audition_apres_l_echeance_et_avocat_avise_peu_avant(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    _gav(db, [
        ("interpellation", "15/03/2026", "06h10", 1, None),
        ("placement_garde_a_vue", "15/03/2026", "07h25", 2, None),
        ("avis_parquet", "15/03/2026", "07h30", 2, None),
        ("avis_avocat", "15/03/2026", "07h40", 3, None),
        ("debut_audition", "15/03/2026", "09h45", 4, None),
        ("avis_avocat", "16/03/2026", "06h15", 5, None),
        ("debut_audition", "16/03/2026", "06h40", 6, '{"avocat": "absent"}'),
        ("prolongation_garde_a_vue", "16/03/2026", "07h05", 7, '{"autorisation_juge": "oui"}'),
    ])
    titres = [s.titre for s in detecter_signalements(db)]
    assert "Audition après l'échéance des 24 heures, avant toute prolongation" in titres, titres
    assert "Audition hors la présence de l'avocat, avisé 25 min avant" in titres, titres


def _piece(db, id_: int, type_: str, date: str, page: int, texte: str) -> None:
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
               "VALUES (?, 'd', ?, ?, ?)", (page, page, texte, str(page)))
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, date_apparente) VALUES (?, ?, ?, ?, ?)",
               (id_, type_, page, page, date))


def test_flagrance_apres_l_ouverture_et_geolocalisation_sans_autorisation(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    _piece(db, 1, "Réquisitoire introductif", "16/03/2026", 1, "Requérons qu'il plaise au juge d'instruction informer.")
    _piece(db, 2, "Réquisition", "17/03/2026", 2,
           "Agissant en enquête de flagrance, requérons l'opérateur de procéder à la géolocalisation en temps réel "
           "de la ligne, sans limitation de durée.")
    _piece(db, 3, "Réquisition", "18/03/2026", 3,
           "Agissant sur commission rogatoire, requérons la géolocalisation en temps réel de la ligne, "
           "autorisée par le juge d'instruction.")
    db.commit()
    signalements = detecter_signalements(db)
    flagrance = [s for s in signalements if s.titre.startswith("Acte d'enquête de flagrance")]
    geoloc = [s for s in signalements if s.titre.startswith("Géolocalisation")]
    assert [s.page_reference for s in flagrance] == [2]
    assert [s.page_reference for s in geoloc] == [2] and "sans limitation de durée" in geoloc[0].description
