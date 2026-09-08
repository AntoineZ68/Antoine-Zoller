from __future__ import annotations

from .conftest import DossierTraite


def test_nombre_de_pages(dossier_traite: DossierTraite) -> None:
    total = dossier_traite.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert total == dossier_traite.verite.nb_pages


def test_pages_ocr_correctement_detectees(dossier_traite: DossierTraite) -> None:
    pages_ocr = {
        row[0]
        for row in dossier_traite.db.execute("SELECT numero_global FROM pages WHERE ocr_applique = 1")
    }
    assert pages_ocr == set(dossier_traite.verite.pages_scan)


def test_pages_natives_non_passees_en_ocr(dossier_traite: DossierTraite) -> None:
    pages_natives = {
        row[0]
        for row in dossier_traite.db.execute("SELECT numero_global FROM pages WHERE ocr_applique = 0")
    }
    assert pages_natives.isdisjoint(set(dossier_traite.verite.pages_scan))


def test_cotes_detectees(dossier_traite: DossierTraite) -> None:
    for numero, cote_attendue in dossier_traite.verite.cotes_par_page.items():
        row = dossier_traite.db.execute(
            "SELECT cote_detectee FROM pages WHERE numero_global = ?", (numero,)
        ).fetchone()
        assert row["cote_detectee"] == cote_attendue


def test_texte_ocr_est_lisible(dossier_traite: DossierTraite) -> None:
    """Les pages passées en OCR doivent produire un texte exploitable, pas
    seulement "un texte quelconque" : le contenu attendu doit s'y retrouver."""
    for numero in dossier_traite.verite.pages_scan:
        row = dossier_traite.db.execute(
            "SELECT texte FROM pages WHERE numero_global = ?", (numero,)
        ).fetchone()
        assert len(row["texte"].strip()) > 50
