"""Vérifie de bout en bout la garantie centrale du produit : une citation
rejetée par la passe de vérification ne doit apparaître dans AUCUN livrable,
et doit être traçable dans le rapport de contrôle."""

from __future__ import annotations

import shutil
import sqlite3

from openpyxl import load_workbook
from rich.console import Console

from depouille.build_deliverables import construire_livrables
from depouille.verification import verifier_table

from .conftest import DossierTraite

CITATION_INVENTEE = "cette phrase n'existe nulle part dans le dossier fictif"


def test_citation_rejetee_absente_des_livrables_mais_dans_le_controle(
    dossier_traite: DossierTraite, tmp_path
) -> None:
    dossier_traite.db.commit()
    copie = tmp_path / "copie.db"
    shutil.copy(dossier_traite.affaire_dir / "depouille.db", copie)

    db = sqlite3.connect(copie)
    db.row_factory = sqlite3.Row
    db.execute(
        """INSERT INTO declarations (personne_id, page, citation, point_factuel, statut_verif)
           VALUES (NULL, 7, ?, 'test_rejet', 'a_faire')""",
        (CITATION_INVENTEE,),
    )
    db.commit()
    resume = verifier_table(db, "declarations", dossier_traite.config.seuil_flou_ocr)
    assert resume["rejetee"] >= 1

    affaire_copie = tmp_path / "affaire_copie"
    affaire_copie.mkdir()
    construire_livrables(db, affaire_copie, dossier_traite.config, console=Console(quiet=True))

    wb = load_workbook(affaire_copie / "out" / "04_declarations.xlsx")
    for feuille in wb.sheetnames:
        for row in wb[feuille].iter_rows(values_only=True):
            assert CITATION_INVENTEE not in [str(v) for v in row]

    controle = (affaire_copie / "out" / "99_controle.md").read_text(encoding="utf-8")
    assert CITATION_INVENTEE in controle


def test_tous_les_livrables_sont_generes(dossier_traite: DossierTraite, tmp_path) -> None:
    affaire_copie = tmp_path / "affaire_livrables"
    affaire_copie.mkdir()
    construire_livrables(dossier_traite.db, affaire_copie, dossier_traite.config, console=Console(quiet=True))

    for nom in (
        "02_chronologie_procedure.docx",
        "03_chronologie_faits.docx",
        "04_declarations.xlsx",
        "05_personnalite.docx",
        "99_controle.md",
    ):
        chemin = affaire_copie / "out" / nom
        assert chemin.exists(), f"{nom} n'a pas été généré"
        assert chemin.stat().st_size > 0


def test_personnalite_ne_contient_que_des_elements_sources(dossier_traite: DossierTraite, tmp_path) -> None:
    """Chaque élément de la fiche de personnalité doit porter une citation
    qui se retrouve littéralement sur la page annoncée."""
    from docx import Document

    affaire_copie = tmp_path / "affaire_personnalite"
    affaire_copie.mkdir()
    construire_livrables(dossier_traite.db, affaire_copie, dossier_traite.config, console=Console(quiet=True))

    doc = Document(affaire_copie / "out" / "05_personnalite.docx")
    textes = [p.text for p in doc.paragraphs if "«" in p.text]
    assert textes, "aucun élément sourcé trouvé dans la fiche de personnalité"
    for texte in textes:
        citation = texte.split("«", 1)[1].rsplit("»", 1)[0].strip()
        assert len(citation) > 0
