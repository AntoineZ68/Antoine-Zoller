"""Le PDF surligné doit couvrir la quasi-totalité des citations vérifiées,
sur les pages natives comme sur les pages OCR, et ne jamais surligner une
citation rejetée par la passe de vérification."""

from __future__ import annotations

import pymupdf

from depouille.surlignage import construire_pdf_surligne

from .conftest import DossierTraite


def test_toutes_les_citations_sont_surlignees(dossier_traite: DossierTraite, tmp_path) -> None:
    chemin_sortie = tmp_path / "dossier_surligne.pdf"
    resultat = construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, chemin_sortie)

    assert resultat["introuvables"] == 0
    assert resultat["surlignes"] > 0
    assert chemin_sortie.exists()


def test_surlignage_fonctionne_sur_page_ocr(dossier_traite: DossierTraite, tmp_path) -> None:
    """Régression : le surlignage doit chercher le texte dans la copie
    OCRisée, pas dans le PDF original (qui n'a aucun texte sur les pages
    scannées)."""
    chemin_sortie = tmp_path / "dossier_surligne.pdf"
    construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, chemin_sortie)

    pages_ocr = [
        row["numero_global"]
        for row in dossier_traite.db.execute("SELECT numero_global FROM pages WHERE ocr_applique = 1")
    ]
    doc = pymupdf.open(chemin_sortie)
    au_moins_une_page_ocr_annotee = False
    for numero in pages_ocr:
        page = doc[numero - 1]
        if page.annots():
            au_moins_une_page_ocr_annotee = list(page.annots())
            break
    doc.close()
    # Le jeu d'essai place le certificat médical (une page OCR) en source
    # d'un événement de procédure vérifié : au moins une page OCR doit donc
    # porter une annotation de surlignage.
    assert au_moins_une_page_ocr_annotee


def test_le_pdf_surligne_a_le_meme_nombre_de_pages_que_le_dossier(
    dossier_traite: DossierTraite, tmp_path
) -> None:
    chemin_sortie = tmp_path / "dossier_surligne.pdf"
    construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, chemin_sortie)

    nb_pages_db = dossier_traite.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    doc = pymupdf.open(chemin_sortie)
    assert doc.page_count == nb_pages_db
    doc.close()


def test_positions_enregistrees_pour_encadrer_le_passage(dossier_traite: DossierTraite, tmp_path) -> None:
    """Chaque citation surlignée a ses zones enregistrées, dans les limites
    de sa page : c'est ce qui permet d'encadrer LE passage au clic. Ce qui
    est encadré est exactement ce qui est surligné."""
    import json

    chemin_sortie = tmp_path / "dossier_surligne.pdf"
    resultat = construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, chemin_sortie)
    positions = dossier_traite.db.execute("SELECT * FROM positions_citations").fetchall()

    assert len(positions) > 0
    assert len(positions) <= resultat["surlignes"], "une position par citation surlignée (doublons fusionnés)"
    doc = pymupdf.open(chemin_sortie)
    for pos in positions:
        page = doc[pos["page"] - 1]
        assert abs(pos["largeur_page"] - page.rect.width) < 1
        zones = json.loads(pos["zones_json"])
        assert zones
        for x0, y0, x1, y1 in zones:
            assert 0 <= x0 < x1 <= pos["largeur_page"] + 1
            assert 0 <= y0 < y1 <= pos["hauteur_page"] + 1


def test_reconstruction_remplace_les_positions(dossier_traite: DossierTraite, tmp_path) -> None:
    construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, tmp_path / "a.pdf")
    n = dossier_traite.db.execute("SELECT COUNT(*) FROM positions_citations").fetchone()[0]
    construire_pdf_surligne(dossier_traite.db, dossier_traite.affaire_dir, tmp_path / "b.pdf")
    assert dossier_traite.db.execute("SELECT COUNT(*) FROM positions_citations").fetchone()[0] == n
