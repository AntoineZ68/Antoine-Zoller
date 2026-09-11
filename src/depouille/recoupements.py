"""Recoupement d'identifiants "durs" (téléphone, plaque d'immatriculation,
IBAN, adresse) apparaissant sur plusieurs pages du dossier.

Différent du rapprochement de déclarations (voir declarations.py, qui
compare des affirmations en langage libre et dépend du modèle pour les
pièces sans structure Question/Réponse) : ceci ne fait aucun appel réseau
et ne dépend d'aucune interprétation sémantique. Un numéro de téléphone
identique, chiffre pour chiffre, cité par deux personnes différentes à
deux pages différentes, est un fait brut — pas une association que
l'outil "comprend" par le sens des phrases. C'est exactement le type de
tâche où un modèle de langage est le mauvais outil (il compare des idées,
pas des identifiants exacts) et où une correspondance déterministe est à
la fois plus fiable et gratuite.

Aucun de ces recoupements n'est présenté comme une preuve de qui que ce
soit : seulement "cet identifiant apparaît à ces pages précises", sourcé,
à l'avocat d'en tirer ce qu'il veut.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .regex_patterns import phrase_contenant


def _ligne_contenant(texte: str, position: int) -> str:
    """Une facture ou un en-tête n'a pas de ponctuation de fin de phrase
    entre ses champs ("Siège social : ... - Téléphone : ...") :
    phrase_contenant() y remonterait jusqu'au tout début de la page faute
    de trouver un point avant la position cherchée. La ligne visuelle
    contenant l'identifiant reste, elle, toujours pertinente — que le texte
    soit un champ de facture ou une phrase de récit."""
    debut = texte.rfind("\n", 0, position) + 1
    fin_index = texte.find("\n", position)
    fin = fin_index if fin_index != -1 else len(texte)
    ligne = texte[debut:fin].strip()
    return ligne if ligne else phrase_contenant(texte, position)

RE_TELEPHONE = re.compile(r"\b0[1-9](?:[\s.-]?\d{2}){4}\b")
# Format SIV (depuis 2009) : AA-123-AA. L'ancien format FNI (1234 AB 56) est
# volontairement exclu : trop proche de séquences numériques ordinaires
# (référence de pièce, montant...) pour rester fiable sans faux positifs.
RE_PLAQUE_SIV = re.compile(r"\b[A-Z]{2}-\d{3}-[A-Z]{2}\b")
# IBAN générique (pas seulement français) : deux lettres de pays, deux
# chiffres de clé, puis le BBAN en groupes de 4.
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
RE_ADRESSE = re.compile(
    r"\b\d{1,4}[,]?\s+(?:rue|avenue|boulevard|chemin|impasse|allée|place|route|quai|cours|"
    r"zone\s+industrielle|zone\s+d['’]activit[ée])\s+[A-Za-zÀ-ÿ'\-]+(?:\s+[A-Za-zÀ-ÿ'\-]+){0,4}",
    re.IGNORECASE,
)


def _normaliser_chiffres(valeur: str) -> str:
    return re.sub(r"\D", "", valeur)


def _normaliser_espaces(valeur: str) -> str:
    return re.sub(r"\s+", " ", valeur.strip().lower())


# (type affiché, motif, normalisation pour la comparaison — deux écritures
# de la même valeur doivent se rapprocher malgré des espaces/tirets
# différents, mais la valeur AFFICHÉE reste toujours celle lue littéralement).
_EXTRACTEURS: list[tuple[str, re.Pattern, "callable"]] = [
    ("Téléphone", RE_TELEPHONE, _normaliser_chiffres),
    ("Plaque d'immatriculation", RE_PLAQUE_SIV, lambda v: v.upper().replace(" ", "")),
    ("IBAN", RE_IBAN, lambda v: v.upper().replace(" ", "")),
    ("Adresse", RE_ADRESSE, _normaliser_espaces),
]


@dataclass
class OccurrenceEntite:
    page: int
    citation: str
    valeur_brute: str


@dataclass
class EntiteCommune:
    type_entite: str
    valeur: str
    occurrences: list[OccurrenceEntite] = field(default_factory=list)


def detecter_recoupements(db: sqlite3.Connection) -> list[EntiteCommune]:
    """Un identifiant compte comme "recoupé" s'il apparaît sur au moins deux
    PAGES différentes du dossier — jamais seulement répété deux fois sur la
    même page (ex. un numéro de téléphone dans l'en-tête ET la signature
    d'une même lettre), ce qui ne recouperait rien du tout."""
    pages = db.execute("SELECT numero_global, texte FROM pages ORDER BY numero_global").fetchall()

    par_cle: dict[tuple[str, str], list[OccurrenceEntite]] = {}
    for page in pages:
        texte = page["texte"]
        for type_entite, motif, normaliser in _EXTRACTEURS:
            vues_sur_cette_page: set[str] = set()
            for m in motif.finditer(texte):
                valeur_brute = m.group(0).strip().rstrip(",")
                cle_normalisee = normaliser(valeur_brute)
                if not cle_normalisee or cle_normalisee in vues_sur_cette_page:
                    continue  # une seule occurrence comptée par page, pour ne pas gonfler un recoupement avec des répétitions locales
                vues_sur_cette_page.add(cle_normalisee)
                citation = _ligne_contenant(texte, m.start())
                par_cle.setdefault((type_entite, cle_normalisee), []).append(
                    OccurrenceEntite(page=page["numero_global"], citation=citation, valeur_brute=valeur_brute)
                )

    resultats = []
    for (type_entite, _cle), occurrences in par_cle.items():
        pages_distinctes = {o.page for o in occurrences}
        if len(pages_distinctes) >= 2:
            resultats.append(EntiteCommune(type_entite=type_entite, valeur=occurrences[0].valeur_brute, occurrences=occurrences))

    resultats.sort(key=lambda e: (e.type_entite, e.valeur))
    return resultats
