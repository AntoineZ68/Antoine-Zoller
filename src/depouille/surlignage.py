"""Génère une copie surlignée du dossier — les passages retenus par l'outil
apparaissent surlignés directement dans le PDF, comme le ferait l'avocat au
feutre. Une seule sortie fusionnée, numérotée dans le même ordre que tous
les autres livrables (numero_global) : "page X" veut dire la même chose
partout, dans le PDF surligné comme dans les tableaux Word/Excel.

Ne surligne QUE des citations déjà vérifiées (statut_verif = 'verifie') —
une citation rejetée par la passe de vérification n'est jamais surlignée,
exactement comme elle n'apparaît dans aucun autre livrable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pymupdf

COULEUR_PROCEDURE = (1.0, 0.85, 0.2)  # jaune : chronologie de procédure
COULEUR_DECLARATION = (0.6, 0.85, 1.0)  # bleu clair : déclarations
COULEUR_FAIT = (0.7, 1.0, 0.7)  # vert clair : chronologie des faits

LEGENDE = {
    COULEUR_PROCEDURE: "jaune : chronologie de procédure (dates, heures, actes)",
    COULEUR_DECLARATION: "bleu : déclarations",
    COULEUR_FAIT: "vert : chronologie des faits",
}


def _citations_par_page(db: sqlite3.Connection) -> dict[int, list[tuple[str, tuple[float, float, float]]]]:
    par_page: dict[int, list[tuple[str, tuple[float, float, float]]]] = {}
    requetes = [
        ("SELECT page, citation FROM evenements_procedure WHERE statut_verif = 'verifie'", COULEUR_PROCEDURE),
        ("SELECT page, citation FROM declarations WHERE statut_verif = 'verifie'", COULEUR_DECLARATION),
        ("SELECT page, citation FROM evenements_faits WHERE statut_verif = 'verifie'", COULEUR_FAIT),
    ]
    for sql, couleur in requetes:
        for row in db.execute(sql):
            par_page.setdefault(row["page"], []).append((row["citation"], couleur))
    return par_page


def _chercher_zones(page: "pymupdf.Page", citation: str) -> list:
    """Cherche la citation sur la page. À défaut d'une correspondance
    exacte (une citation reconstruite peut différer de la mise en page PDF
    sur un retour à la ligne), retente sur les six premiers mots : mieux
    vaut surligner un peu court que ne rien surligner du tout."""
    zones = page.search_for(citation)
    if zones:
        return zones
    mots = citation.split()
    if len(mots) >= 6:
        return page.search_for(" ".join(mots[:6]))
    return []


def _chemin_source_effectif(affaire_dir: Path, fichier: str) -> Path:
    """La copie OCRisée (work/ocr_<fichier>.pdf) est celle qui porte la
    couche de texte invisible sur les pages scannées — c'est elle qu'il
    faut chercher pour le surlignage, jamais l'original tel qu'ingéré, qui
    n'a aucun texte du tout sur ces pages-là."""
    chemin_ocr = affaire_dir / "work" / f"ocr_{Path(fichier).stem}.pdf"
    if chemin_ocr.exists():
        return chemin_ocr
    return affaire_dir / "source" / fichier


def construire_pdf_surligne(db: sqlite3.Connection, affaire_dir: Path, chemin_sortie: Path) -> dict[str, int]:
    pages = db.execute("SELECT * FROM pages ORDER BY numero_global").fetchall()
    citations_par_page = _citations_par_page(db)

    doc_sortie = pymupdf.open()
    docs_source: dict[str, "pymupdf.Document"] = {}
    nb_surlignes = 0
    nb_introuvables = 0

    try:
        for page_row in pages:
            fichier = page_row["fichier_source"]
            if fichier not in docs_source:
                docs_source[fichier] = pymupdf.open(str(_chemin_source_effectif(affaire_dir, fichier)))
            doc_source = docs_source[fichier]
            index_page = page_row["page_fichier"] - 1

            doc_sortie.insert_pdf(doc_source, from_page=index_page, to_page=index_page)
            page_sortie = doc_sortie[-1]

            for citation, couleur in citations_par_page.get(page_row["numero_global"], []):
                zones = _chercher_zones(page_sortie, citation)
                if zones:
                    annot = page_sortie.add_highlight_annot(zones)
                    annot.set_colors(stroke=couleur)
                    annot.update()
                    nb_surlignes += 1
                else:
                    nb_introuvables += 1
    finally:
        for doc_source in docs_source.values():
            doc_source.close()

    chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
    doc_sortie.save(str(chemin_sortie))
    doc_sortie.close()

    return {"surlignes": nb_surlignes, "introuvables": nb_introuvables}
