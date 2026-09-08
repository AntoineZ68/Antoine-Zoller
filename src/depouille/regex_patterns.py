"""Extractions déterministes par expression régulière.

Règle du produit : les dates, heures, cotes et numéros de procédure sont
extraits ici, avant tout appel à un modèle. Si un motif ne correspond à
rien, la fonction renvoie `None` — jamais une valeur devinée.
"""

from __future__ import annotations

import re

RE_COTE = re.compile(r"\bCote\s+([A-Za-z]\d{3,6})\b")
RE_NUM_PROCEDURE = re.compile(r"N[°ºo]\s*PARQUET\s*([\d/]+)", re.IGNORECASE)
RE_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
RE_DATE_ACTE = re.compile(r"\bLe\s+(\d{2}/\d{2}/\d{4})\b")
RE_HEURE = re.compile(r"\b(\d{1,2})\s*[hH]\s*(\d{2})\b")


def detecter_cote(texte: str) -> str | None:
    m = RE_COTE.search(texte)
    return m.group(1) if m else None


def detecter_numero_procedure(texte: str) -> str | None:
    m = RE_NUM_PROCEDURE.search(texte)
    return m.group(1) if m else None


def trouver_dates(texte: str) -> list[str]:
    """Renvoie les dates trouvées, normalisées en JJ/MM/AAAA, dans l'ordre
    d'apparition dans le texte."""
    return [f"{j}/{m}/{a}" for j, m, a in RE_DATE.findall(texte)]


RE_FIN_PHRASE = re.compile(r"(?<!\b[A-ZÀ-Ÿ])\.(?=\s|$)")


def phrase_contenant(texte: str, position: int) -> str:
    """Isole la phrase contenant la position donnée. Une fin de phrase est un
    point non précédé d'une seule lettre majuscule isolée (abréviation
    courante en style administratif : "M.", "N.", ...), pour éviter de
    couper une citation en plein mot sur ce genre d'abréviation."""
    debut = 0
    for m in RE_FIN_PHRASE.finditer(texte, 0, position):
        debut = m.end()
    fin_match = RE_FIN_PHRASE.search(texte, position)
    fin = fin_match.end() if fin_match else len(texte)
    return texte[debut:fin].strip()


def decouper_en_phrases(texte: str) -> list[str]:
    resultats = []
    debut = 0
    for m in RE_FIN_PHRASE.finditer(texte):
        resultats.append(texte[debut : m.end()].strip())
        debut = m.end()
    reste = texte[debut:].strip()
    if reste:
        resultats.append(reste)
    return [r for r in resultats if r]


def detecter_date_acte(texte: str) -> str | None:
    """Date de l'acte lui-même, reconnue uniquement via la formule
    d'ouverture standard des PV français ("Le JJ/MM/AAAA..."). Ne renvoie
    jamais une date incidente (ex. une date de naissance mentionnée dans le
    corps du texte) : à défaut de ce motif précis, NON TROUVÉ plutôt qu'une
    approximation."""
    m = RE_DATE_ACTE.search(texte)
    return m.group(1) if m else None


def texte_sans_entete(texte: str, est_titre) -> str:
    """Reconstruit le corps d'une page en excluant l'intitulé en capitales
    et le pied de page (numéro de procédure/cote), et en recollant les
    lignes en un seul bloc — une phrase peut être répartie sur plusieurs
    lignes visuelles du PDF, découper ligne à ligne couperait une citation
    en plein mot."""
    lignes = [
        ligne.strip()
        for ligne in texte.splitlines()
        if ligne.strip() and not est_titre(ligne.strip()) and "N° PARQUET" not in ligne
    ]
    return " ".join(lignes)


def trouver_heures(texte: str) -> list[str]:
    """Renvoie les heures trouvées, normalisées en HHhMM, dans l'ordre
    d'apparition dans le texte."""
    return [f"{int(h):02d}h{mn}" for h, mn in RE_HEURE.findall(texte)]
