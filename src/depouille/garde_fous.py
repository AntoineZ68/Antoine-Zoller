"""Garde-fous déterministes appliqués à tout texte libre produit par le
modèle (résumé, réponse à une question).

La consigne donnée au modèle ne suffit pas : un modèle peut l'ignorer. Ce
filtre, lui, ne dépend pas de sa bonne volonté — un texte qui contient une
qualification juridique n'atteint jamais l'écran."""

from __future__ import annotations

import re
import unicodedata

RE_QUALIFICATION = re.compile(
    r"nullit|irr[ée]gul|invocable|qualit[ée] (?:pour|à) agir|\bgrief\b|vice de proc|ill[ée]gal|annulable|entach",
    re.IGNORECASE,
)


def contient_qualification(texte: str) -> bool:
    return bool(RE_QUALIFICATION.search(texte))


# --- Éléments identifiants : noms, lieux, nombres -------------------------
#
# Ce qu'un texte rédigé par le modèle peut inventer de dangereux, ce sont
# les éléments qui identifient : un nom, un lieu, un opérateur, une date, un
# montant. Chaque mot en capitales, chaque mot à majuscule initiale (hors
# premier mot) et chaque nombre doit donc figurer dans le texte de
# référence — la pièce pour un intitulé, les sources citées pour une phrase
# de résumé.

RE_NOMBRE = re.compile(r"\d+")
RE_MOT = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")
# Mots qui n'identifient rien ni personne : abréviations de procédure et
# civilités. « PV de pose de la balise » reste exact si la pièce écrit
# « PROCÈS-VERBAL » ; « Mme SERMET » reste exact si la source écrit « SERMET
# Odile ».
MOTS_GENERIQUES = frozenset({
    "pv", "gav", "cpp", "opj", "apj", "jld", "ji", "tj", "cp", "rg",
    "m", "mme", "mlle", "me", "dr", "maitre", "monsieur", "madame", "docteur",
})


def _normaliser(texte: str) -> str:
    forme = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in forme if not unicodedata.combining(c)).lower()


def elements_absents(texte: str, reference: str) -> list[str]:
    """Liste les noms, lieux et nombres de `texte` introuvables dans
    `reference`. Vide : le texte n'invente aucun élément identifiant."""
    ref = _normaliser(reference)
    mots_ref = set(re.findall(r"[a-z0-9]+", ref))
    absents = [n for n in RE_NOMBRE.findall(texte) if n not in mots_ref and n not in ref]
    for position, mot in enumerate(RE_MOT.findall(texte)):
        lettres = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", mot)
        if len(lettres) < 2:
            continue
        if not (lettres.isupper() or (lettres[0].isupper() and position > 0)):
            continue
        for partie in re.split(r"['’-]", _normaliser(mot)):
            if len(partie) >= 2 and partie not in mots_ref and partie not in MOTS_GENERIQUES:
                absents.append(mot)
                break
    return absents
