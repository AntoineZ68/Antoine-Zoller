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


def trouver_heures(texte: str) -> list[str]:
    """Renvoie les heures trouvées, normalisées en HHhMM, dans l'ordre
    d'apparition dans le texte."""
    return [f"{int(h):02d}h{mn}" for h, mn in RE_HEURE.findall(texte)]
