"""Passe de vérification obligatoire — jamais un LLM.

Pour chaque fait extrait (page + citation), on recherche littéralement la
citation dans le texte de la page annoncée. Aucune exception : un fait dont
la citation n'est pas retrouvée est rejeté et versé dans
`rejets_verification`, quelle que soit l'étape qui l'a produit.

Tolérance strictement bornée (voir PLAN.md section 1.2) : sur une page
passée en OCR, une correspondance floue (>= seuil configuré) est acceptée
et marquée comme telle — jamais sur une page en texte natif, où seule
l'exactitude compte.
"""

from __future__ import annotations

import re
import sqlite3

from rapidfuzz import fuzz

TABLES_VERIFIABLES = ("evenements_procedure", "evenements_faits", "declarations")


def _normaliser(texte: str) -> str:
    texte = texte.replace("’", "'").replace("‘", "'").replace(""", '"').replace(""", '"')
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip().lower()


def verifier_citation(
    texte_page: str, citation: str, ocr_applique: bool, seuil_flou_ocr: int
) -> tuple[bool, str]:
    """Renvoie (valide, méthode). méthode ∈ {"exacte", "floue_ocr", "rejetee"}."""
    page_norm = _normaliser(texte_page)
    citation_norm = _normaliser(citation)

    if not citation_norm:
        return False, "rejetee"

    if citation_norm in page_norm:
        return True, "exacte"

    if ocr_applique:
        score = fuzz.partial_ratio(citation_norm, page_norm)
        if score >= seuil_flou_ocr:
            return True, "floue_ocr"

    return False, "rejetee"


def verifier_table(db: sqlite3.Connection, table: str, seuil_flou_ocr: int) -> dict[str, int]:
    if table not in TABLES_VERIFIABLES:
        raise ValueError(f"Table non vérifiable : {table!r}")

    resume = {"exacte": 0, "floue_ocr": 0, "rejetee": 0}
    lignes = db.execute(f"SELECT id, page, citation FROM {table} WHERE statut_verif = 'a_faire'").fetchall()

    for ligne in lignes:
        page = db.execute(
            "SELECT texte, ocr_applique FROM pages WHERE numero_global = ?", (ligne["page"],)
        ).fetchone()

        if page is None:
            valide, methode, raison = False, "rejetee", "page annoncée introuvable dans le dossier"
        else:
            valide, methode = verifier_citation(page["texte"], ligne["citation"], bool(page["ocr_applique"]), seuil_flou_ocr)
            raison = "citation introuvable sur la page annoncée"

        if valide:
            db.execute(
                f"UPDATE {table} SET statut_verif = 'verifie', methode_verif = ? WHERE id = ?",
                (methode, ligne["id"]),
            )
            resume[methode] += 1
        else:
            db.execute(
                f"UPDATE {table} SET statut_verif = 'rejete', methode_verif = ? WHERE id = ?",
                (methode, ligne["id"]),
            )
            db.execute(
                """INSERT INTO rejets_verification
                   (table_origine, id_origine, page_annoncee, citation_proposee, raison)
                   VALUES (?, ?, ?, ?, ?)""",
                (table, ligne["id"], ligne["page"], ligne["citation"], raison),
            )
            resume["rejetee"] += 1

    db.commit()
    return resume


def verifier_tout(db: sqlite3.Connection, seuil_flou_ocr: int) -> dict[str, dict[str, int]]:
    return {table: verifier_table(db, table, seuil_flou_ocr) for table in TABLES_VERIFIABLES}
