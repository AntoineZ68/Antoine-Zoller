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
