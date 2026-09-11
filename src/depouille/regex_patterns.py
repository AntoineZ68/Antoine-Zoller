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


_UNITES_FR = {
    "zéro": 0, "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9,
}
_DIX_DIXNEUF_FR = {
    "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
    "seize": 16, "dix-sept": 17, "dix-huit": 18, "dix-neuf": 19,
}
_DIZAINES_FR = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60}


def _nombre_cardinal_fr(texte: str) -> int | None:
    """Convertit un nombre cardinal français en toutes lettres (0-99) en
    entier — couvre les irrégularités d'usage (soixante-dix, quatre-vingts).
    Renvoie None si le texte ne correspond à aucune écriture reconnue,
    jamais une valeur approchée."""
    mot = texte.strip().lower().replace("’", "'")
    mot = re.sub(r"[\s-]+et[\s-]+", "-", mot)
    mot = re.sub(r"[\s-]+", "-", mot).strip("-")

    if mot in _UNITES_FR:
        return _UNITES_FR[mot]
    if mot in _DIX_DIXNEUF_FR:
        return _DIX_DIXNEUF_FR[mot]
    if mot in _DIZAINES_FR:
        return _DIZAINES_FR[mot]
    if mot in ("quatre-vingt", "quatre-vingts"):
        return 80
    if mot.startswith("quatre-vingt-"):
        reste = mot[len("quatre-vingt-") :]
        if reste in _DIX_DIXNEUF_FR:
            return 80 + _DIX_DIXNEUF_FR[reste]
        if reste in _UNITES_FR:
            return 80 + _UNITES_FR[reste]
        return None
    if mot == "soixante-dix":
        return 70
    if mot.startswith("soixante-"):
        reste = mot[len("soixante-") :]
        if reste in _DIX_DIXNEUF_FR:
            return 60 + _DIX_DIXNEUF_FR[reste]
        return None
    for prefixe, base in _DIZAINES_FR.items():
        if mot.startswith(prefixe + "-"):
            reste = mot[len(prefixe) + 1 :]
            if reste in _UNITES_FR:
                return base + _UNITES_FR[reste]
    return None


def _jour_depuis_texte(texte: str) -> int | None:
    mot = texte.strip().lower()
    if mot == "premier":
        return 1
    m = re.match(r"^(\d{1,2})(?:er)?$", mot)
    if m:
        return int(m.group(1))
    return _nombre_cardinal_fr(mot)


def _annee_depuis_texte(texte: str) -> int | None:
    mot = re.sub(r"[\s-]+", " ", texte.strip().lower())
    if not mot.startswith("deux mille"):
        return None
    reste = mot[len("deux mille") :].strip()
    if not reste:
        return 2000
    nombre = _nombre_cardinal_fr(reste)
    return 2000 + nombre if nombre is not None else None


def _heure_depuis_mots(texte: str) -> str | None:
    """Convertit une heure en toutes lettres ("six heures et quinze
    minutes", "quatorze heures trente", "midi", "minuit") en HHhMM. Renvoie
    None si la formulation n'est pas reconnue avec certitude."""
    mot = texte.strip().lower().replace("’", "'").rstrip(".,;")
    if mot == "minuit":
        return "00h00"
    if mot == "midi":
        return "12h00"
    m = re.match(r"^([a-zà-ÿ\s-]+?)\s+heures?(?:\s+(?:et\s+)?([a-zà-ÿ\s-]+?)(?:\s+minutes?)?)?$", mot)
    if not m:
        return None
    heure = _nombre_cardinal_fr(m.group(1).strip())
    if heure is None or not (0 <= heure <= 23):
        return None
    minute_mot = (m.group(2) or "").strip()
    if not minute_mot:
        minute = 0
    elif minute_mot in ("demie", "demi"):
        minute = 30
    else:
        minute = _nombre_cardinal_fr(minute_mot)
        if minute is None or not (0 <= minute <= 59):
            return None
    return f"{heure:02d}h{minute:02d}"


def _normaliser_heure_libre(texte: str) -> str | None:
    """Tente le format chiffré ("14h30") puis les toutes lettres ("quatorze
    heures trente") — jamais de valeur approchée si ni l'un ni l'autre ne
    correspond avec certitude."""
    return normaliser_heure(texte) or _heure_depuis_mots(texte)


# Formule d'ouverture la plus répandue des PV français : l'année est
# déclarée une fois en toutes lettres ("L'an deux mille vingt-six"), puis le
# jour et le mois suivent SANS année accolée ("le deux septembre"). Le motif
# ci-dessus (RE_DATE_ACTE, "Le [date complète] à [heure]") ne la reconnaît
# jamais, quelle que soit l'écriture du jour — un vrai manque, pas un cas
# rare : c'est la formule sacramentelle enseignée en école de police.
RE_FORMULE_OUVERTURE_LETTRES = re.compile(
    r"\bl['’]an\s+(?P<annee>deux\s+mille(?:[\s-]+[a-zà-ÿ]+){0,3})\s*,\s*"
    rf"le\s+(?P<jour>\d{{1,2}}(?:er)?|[a-zà-ÿ]+(?:[\s-][a-zà-ÿ]+)?)\s+(?P<mois>{_NOMS_MOIS})"
    r"(?:\s+à\s+(?P<heure>[^.,;\n]{1,40}))?",
    re.IGNORECASE,
)

# Troisième formule d'ouverture rencontrée en pratique : ni la prose "L'an
# ...", ni "Le [date] à [heure]", mais un champ encadré en tête de PV
# ("Date : ...", "Date de placement : ...", "Date et Heure : ..."). Exclut
# explicitement "Date de naissance"/"Date de délivrance", qui identifient
# une personne ou un document, jamais l'acte lui-même.
RE_DATE_BOITE_ACTE = re.compile(
    rf"\bDate\b(?!\s+de\s+(?:naissance|d[ée]livrance))[^:\n]{{0,30}}:\s*{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}",
    re.IGNORECASE,
)


def detecter_date_heure_acte(texte: str) -> tuple[str | None, str | None]:
    """Date et heure de l'acte lui-même, reconnues via l'une des deux
    formules d'ouverture standard des PV français : "Le [date complète] à
    [heure]" (année accolée, chiffres) ou "L'an [année en toutes lettres],
    le [jour] [mois] à [heure]" (année déclarée séparément — la plus
    répandue en pratique). Jour et heure peuvent être en chiffres ou en
    toutes lettres dans les deux cas. Ne renvoie jamais de valeur
    approchée : à défaut d'une reconnaissance complète, None."""
    m = RE_FORMULE_OUVERTURE_LETTRES.search(texte)
    if m:
        annee = _annee_depuis_texte(m.group("annee"))
        jour = _jour_depuis_texte(m.group("jour"))
        mois = MOIS_FR.get(m.group("mois").lower())
        if annee is not None and jour is not None and mois is not None:
            date = f"{jour:02d}/{mois:02d}/{annee}"
            heure_brute = m.group("heure")
            heure = _normaliser_heure_libre(heure_brute) if heure_brute else None
            return date, heure

    m = re.search(rf"\bLe\s+{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}\b", texte, re.IGNORECASE)
    if m:
        return normaliser_date(m.group(1)), normaliser_heure(m.group(2))

    m = RE_DATE_BOITE_ACTE.search(texte)
    if m:
        return normaliser_date(m.group(1)), normaliser_heure(m.group(2))

    return detecter_date_acte(texte), None


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
