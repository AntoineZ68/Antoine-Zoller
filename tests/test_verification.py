"""Vérifie la règle absolue du produit : aucun fait sans page ET citation
vérifiée ne doit pouvoir atteindre un livrable, et la tolérance floue reste
strictement bornée aux pages OCR."""

from __future__ import annotations

from depouille.verification import verifier_citation, verifier_table

from .conftest import DossierTraite


def test_citation_exacte_validee_sur_texte_natif() -> None:
    texte = "Le 14/03/2031 à 08h15, nous notifions le placement en garde à vue."
    valide, methode = verifier_citation(texte, "nous notifions le placement en garde à vue.", False, 97)
    assert valide is True
    assert methode == "exacte"


def test_citation_absente_est_rejetee_meme_sur_page_ocr() -> None:
    texte = "Le 14/03/2031 à 08h15, nous notifions le placement en garde à vue."
    valide, methode = verifier_citation(texte, "ceci n'apparaît nulle part dans le texte", True, 97)
    assert valide is False
    assert methode == "rejetee"


def test_tolerance_floue_refusee_sur_page_native() -> None:
    """La tolérance floue ne doit JAMAIS s'appliquer sur une page en texte
    natif — seule l'exactitude littérale y compte, sans exception."""
    texte = "Le 14/03/2031 à 08h15, nous notifions le placement en garde à vue."
    citation_alteree = "nous notifions le p1acement en garde à vue."  # une lettre change
    valide, methode = verifier_citation(texte, citation_alteree, False, 90)
    assert valide is False
    assert methode == "rejetee"


def test_tolerance_floue_acceptee_uniquement_sur_page_ocr() -> None:
    texte = "Le 14/03/2031 à 08h15, nous notifions le placement en garde à vue."
    citation_alteree = "nous notifions le p1acement en garde à vue."
    valide, methode = verifier_citation(texte, citation_alteree, True, 90)
    assert valide is True
    assert methode == "floue_ocr"


def test_pas_de_citation_vide_acceptee() -> None:
    valide, methode = verifier_citation("un texte quelconque.", "", True, 50)
    assert valide is False


def test_verifier_table_rejette_et_journalise(dossier_traite: DossierTraite) -> None:
    db = dossier_traite.db
    cur = db.execute(
        """INSERT INTO evenements_procedure (nature, page, citation, statut_verif)
           VALUES ('test_rejet', 3, 'ceci n''existe nulle part dans la page 3', 'a_faire')"""
    )
    db.commit()
    id_insere = cur.lastrowid

    resume = verifier_table(db, "evenements_procedure", 97)
    assert resume["rejetee"] >= 1

    ligne = db.execute("SELECT statut_verif FROM evenements_procedure WHERE id = ?", (id_insere,)).fetchone()
    assert ligne["statut_verif"] == "rejete"

    rejet = db.execute(
        "SELECT * FROM rejets_verification WHERE table_origine = 'evenements_procedure' AND id_origine = ?",
        (id_insere,),
    ).fetchone()
    assert rejet is not None


def test_aucun_evenement_procedure_sans_page_ni_citation(dossier_traite: DossierTraite) -> None:
    for row in dossier_traite.db.execute(
        "SELECT * FROM evenements_procedure WHERE statut_verif = 'verifie'"
    ):
        assert row["page"] is not None
        assert row["citation"] and row["citation"].strip()


def test_aucune_declaration_sans_page_ni_citation(dossier_traite: DossierTraite) -> None:
    for row in dossier_traite.db.execute("SELECT * FROM declarations WHERE statut_verif = 'verifie'"):
        assert row["page"] is not None
        assert row["citation"] and row["citation"].strip()


def test_toutes_les_citations_verifiees_se_retrouvent_sur_leur_page(dossier_traite: DossierTraite) -> None:
    """Reprend, sur les données réellement produites par le pipeline, la
    garantie centrale du produit : une citation marquée "verifie" doit
    effectivement se retrouver dans le texte de la page qu'elle annonce."""
    db = dossier_traite.db
    for table in ("evenements_procedure", "declarations"):
        for row in db.execute(f"SELECT * FROM {table} WHERE statut_verif = 'verifie'"):
            page = db.execute("SELECT texte FROM pages WHERE numero_global = ?", (row["page"],)).fetchone()
            assert page is not None
            assert row["citation"].strip().lower() in " ".join(page["texte"].lower().split())
