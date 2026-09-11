from __future__ import annotations

from depouille.ingest import SEUIL_OCR_CARACTERES, _longueur_contenu

from .conftest import DossierTraite


def test_longueur_contenu_ignore_les_lignes_vides_de_mise_en_page() -> None:
    """Régression : l'extraction en layout=True (voir ingest._texte_page)
    reproduit fidèlement les espaces blancs visuels d'une page sous forme
    de lignes vides — une page quasiment blanche (juste un tampon, par
    exemple) ne doit pas paraître au-dessus du seuil de déclenchement de
    l'OCR simplement parce qu'elle contient beaucoup de lignes vides."""
    page_presque_blanche = "\n" * 40 + "Tampon" + "\n" * 40
    assert _longueur_contenu(page_presque_blanche) < SEUIL_OCR_CARACTERES

    page_avec_contenu_reel = "Un paragraphe de texte tout à fait normal, avec plusieurs mots.\n" * 2
    assert _longueur_contenu(page_avec_contenu_reel) >= SEUIL_OCR_CARACTERES


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
