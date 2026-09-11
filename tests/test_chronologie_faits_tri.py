"""Régression : la chronologie des faits (03_chronologie_faits.docx) triait
sur evenements_faits.date_evenement_affirmee, une colonne jamais renseignée
nulle part dans le pipeline — le tri retombait donc silencieusement sur
l'ordre des pages, pas sur la date réelle des faits, et l'heure n'était même
pas affichée. Remarqué en confrontant le rendu à un vrai dossier (Colmar,
où la page 3 précède chronologiquement la page 1)."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from depouille.build_deliverables import _construire_chronologie_faits
from depouille.db import ouvrir_db


def test_chronologie_faits_triee_par_date_pas_par_page(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "test.db")

    # Pièce de la page 5 datée AVANT la pièce de la page 2 : si le tri
    # retombe sur l'ordre des pages, le fait de la page 2 sortirait en
    # premier alors qu'il est postérieur.
    db.execute(
        "INSERT INTO pieces (id, type, page_debut, page_fin, date_apparente, heure_apparente) "
        "VALUES (1, 'PV de synthèse', 5, 5, '01/10/2026', '09h00')"
    )
    db.execute(
        "INSERT INTO pieces (id, type, page_debut, page_fin, date_apparente, heure_apparente) "
        "VALUES (2, 'PV de synthèse', 2, 2, '15/10/2026', '14h00')"
    )
    db.execute(
        "INSERT INTO evenements_faits (piece_id, page, citation, description, statut_verif) "
        "VALUES (2, 2, 'fait le plus récent', 'desc récente', 'verifie')"
    )
    db.execute(
        "INSERT INTO evenements_faits (piece_id, page, citation, description, statut_verif) "
        "VALUES (1, 5, 'fait le plus ancien', 'desc ancienne', 'verifie')"
    )
    db.commit()

    chemin = tmp_path / "03_chronologie_faits.docx"
    _construire_chronologie_faits(db, chemin)

    doc = Document(str(chemin))
    texte_paragraphes = [p.text for p in doc.paragraphs if p.text.strip()]
    texte_complet = "\n".join(texte_paragraphes)

    position_ancien = texte_complet.index("fait le plus ancien")
    position_recent = texte_complet.index("fait le plus récent")
    assert position_ancien < position_recent, "le fait du 01/10 doit apparaître avant celui du 15/10"

    # L'heure doit être visible dans l'en-tête du fait, pas seulement la page.
    assert any("01/10/2026 à 09h00" in p for p in texte_paragraphes)
    assert any("15/10/2026 à 14h00" in p for p in texte_paragraphes)
