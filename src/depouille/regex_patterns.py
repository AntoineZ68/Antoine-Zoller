"""Extractions déterministes par expression régulière.

Règle du produit : les dates, heures, cotes et numéros de procédure sont
extraits ici, avant tout appel à un modèle. Si un motif ne correspond à
rien, la fonction renvoie `None` — jamais une valeur devinée.

Les dossiers réels n'écrivent pas tous les dates et heures de la même
façon : "14/03/2031" ou "14 novembre 2024", "08h15" ou "08 h 15". Les
fragments FRAGMENT_DATE et FRAGMENT_HEURE couvrent ces variantes ; les
fonctions normaliser_date/normaliser_heure ramènent toujours le résultat
au même format (JJ/MM/AAAA, HHhMM) pour que le reste du pipeline n'ait
qu'un seul format à connaître.
"""

from __future__ import annotations

import re

MOIS_FR = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

_NOMS_MOIS = "|".join(MOIS_FR.keys())

# Fragments réutilisables (sans groupe capturant nommé, à insérer tels
# quels dans une regex plus large ; le premier groupe capturant () du
# fragment renvoie le texte brut de la date/heure, à repasser par
# normaliser_date / normaliser_heure).
FRAGMENT_DATE = rf"(\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{1,2}}(?:er)?\s+(?:{_NOMS_MOIS})\s+\d{{4}})"
FRAGMENT_HEURE = r"(\d{1,2}\s*[hH]\s*\d{2})"

RE_COTE = re.compile(r"\bcote\s+([A-Za-z][\s-]?\d{2,6})\b", re.IGNORECASE)
RE_NUM_PROCEDURE = re.compile(r"N[°ºo]\s*PARQUET\s*([\d/]+)", re.IGNORECASE)
RE_DATE = re.compile(FRAGMENT_DATE, re.IGNORECASE)
RE_DATE_ACTE = re.compile(rf"\bLe\s+{FRAGMENT_DATE}\b", re.IGNORECASE)
RE_HEURE = re.compile(FRAGMENT_HEURE)


def normaliser_date(texte_date: str) -> str | None:
    """Ramène une date, quelle que soit son écriture (chiffres ou lettres),
    au format JJ/MM/AAAA. Renvoie None si le texte ne correspond à aucun
    format reconnu — jamais une date devinée."""
    texte_date = texte_date.strip()

    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", texte_date)
    if m:
        jour, mois, annee = m.groups()
        return f"{int(jour):02d}/{int(mois):02d}/{annee}"

    m = re.match(rf"^(\d{{1,2}})(?:er)?\s+({_NOMS_MOIS})\s+(\d{{4}})$", texte_date, re.IGNORECASE)
    if m:
        jour, mois_nom, annee = m.groups()
        mois_num = MOIS_FR.get(mois_nom.lower())
        if mois_num:
            return f"{int(jour):02d}/{mois_num:02d}/{annee}"

    return None


def normaliser_heure(texte_heure: str) -> str | None:
    m = re.match(r"^(\d{1,2})\s*[hH]\s*(\d{2})$", texte_heure.strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}h{m.group(2)}"


def detecter_cote(texte: str) -> str | None:
    m = RE_COTE.search(texte)
    if not m:
        return None
    return re.sub(r"[\s-]", "", m.group(1)).upper()


def detecter_numero_procedure(texte: str) -> str | None:
    m = RE_NUM_PROCEDURE.search(texte)
    return m.group(1) if m else None


def trouver_dates(texte: str) -> list[str]:
    """Renvoie les dates trouvées, normalisées en JJ/MM/AAAA, dans l'ordre
    d'apparition dans le texte."""
    resultats = []
    for brut in RE_DATE.findall(texte):
        normalisee = normaliser_date(brut)
        if normalisee:
            resultats.append(normalisee)
    return resultats


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
    d'ouverture standard des PV français ("Le [date]..."). Ne renvoie
    jamais une date incidente (ex. une date de naissance mentionnée dans le
    corps du texte) : à défaut de ce motif précis, NON TROUVÉ plutôt qu'une
    approximation."""
    m = RE_DATE_ACTE.search(texte)
    if not m:
        return None
    return normaliser_date(m.group(1))


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
    resultats = []
    for brut in RE_HEURE.findall(texte):
        normalisee = normaliser_heure(brut)
        if normalisee:
            resultats.append(normalisee)
    return resultats
