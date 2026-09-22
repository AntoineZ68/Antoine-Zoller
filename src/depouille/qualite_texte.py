"""Détection des pages dont le texte est trop dégradé pour être exploité.

Un dossier pénal réel arrive rarement en texte propre : pièces manuscrites
(certificats médicaux, mains courantes), photocopies de photocopies, scans
de travers, PDF déjà passés par un OCR médiocre en amont. Sur ces pages, la
reconnaissance de caractères rend un texte partiellement faux — et le
problème n'est pas qu'elle se trompe, c'est qu'elle se trompe SANS LE DIRE :
« Je soussigné Docteur Marchand certifie avoir examiné » ressort en
« Je SusSiha DŒt dr Mrchand certifie #WOir eXarire ». Rien dans le pipeline
ne distingue alors cette page d'une page lue correctement, et l'avocat n'a
aucune raison de se méfier de ce qu'il lit.

Ce module mesure, sans dictionnaire ni modèle, à quel point le texte d'une
page s'écarte de la forme d'un texte écrit en alphabet latin. Il ne corrige
rien et ne qualifie rien : il signale les pages à relire sur l'original.

La mesure porte sur TOUTES les pages, pas seulement sur celles que nous
avons nous-mêmes passées en OCR : un PDF fourni par le greffe ou par le
client porte souvent déjà une couche de texte produite par un scanner, que
nous n'avons donc pas générée mais qui peut être tout aussi mauvaise.
"""

from __future__ import annotations

import re
import sqlite3

MAJUSCULES = "A-ZÀ-ÖØ-Þ"
MINUSCULES = "a-zß-öø-ÿ"

RE_TOKEN = re.compile(r"\S+")
RE_LETTRE = re.compile(f"[{MAJUSCULES}{MINUSCULES}]")
# Une minuscule suivie d'une majuscule À L'INTÉRIEUR d'un mot : « eXarire »,
# « SusSiha ». En français écrit, cette forme est quasi inexistante.
RE_CASSE_INTERNE = re.compile(f"[{MINUSCULES}][{MAJUSCULES}]")
# Un chiffre encadré de lettres des deux côtés : « en1pr9es » pour
# « en 1996 présentant ». Exiger cet encadrement écarte les références et
# horaires parfaitement normaux d'un dossier pénal — « D.1 », « 17h45 »,
# « 2ème », « N°2026 », « 2026/00482 » — où le chiffre borde le mot.
RE_CHIFFRE_DANS_MOT = re.compile(f"[{MAJUSCULES}{MINUSCULES}][0-9]+[{MAJUSCULES}{MINUSCULES}]")
# Quatre consonnes de suite à l'intérieur d'un mot : « Mrchag » pour
# « Marchand ». Le français n'en produit pratiquement pas.
CONSONNES = "bcdfghjklmnpqrstvwxzßçñBCDFGHJKLMNPQRSTVWXZÇÑ"
RE_SUITE_CONSONNES = re.compile(f"[{CONSONNES}]{{4}}")
VOYELLES = set("aeiouyàâäéèêëîïôöùûüÿæœAEIOUYÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÆŒ")
# Caractères qu'un OCR fabrique en interprétant du bruit. « ° » en est
# volontairement absent : « N° PARQUET » figure sur presque chaque page d'un
# PV, et le compter comme un parasite condamnerait tout le dossier.
CARACTERES_PARASITES = set("#@&|~^\\*_}{][<>¢£¤§±µ¶")

PONCTUATION_BORD = ".,;:!?()«»\"'—-"
LETTRES_ISOLEES_LEGITIMES = frozenset("aàyounjldcmstq")
# En dessous, la mesure n'a pas de sens statistique : une page de garde ou
# un intercalaire ne contient que quelques mots, tous parfaitement lisibles.
MOTS_MINIMUM = 15
SEUIL_ILLISIBILITE = 0.35


def _lettres(mot: str) -> int:
    return sum(1 for c in mot if RE_LETTRE.match(c))


def score_illisibilite(texte: str) -> float:
    """Renvoie entre 0.0 (texte de forme normale) et 1.0 (texte dégradé).

    Mesuré sur un dossier d'essai : pages en texte natif 0.01, pages scannées
    proprement reconnues 0.00, pages manuscrites illisibles 0.72 et 0.86."""
    mots = [t.strip(PONCTUATION_BORD) for t in RE_TOKEN.findall(texte)]
    mots = [m for m in mots if m and RE_LETTRE.search(m)]
    n = len(mots)
    if n < MOTS_MINIMUM:
        return 0.0

    casse_interne = sum(1 for m in mots if RE_CASSE_INTERNE.search(m)) / n
    chiffre_dans_mot = sum(1 for m in mots if RE_CHIFFRE_DANS_MOT.search(m)) / n
    suite_consonnes = sum(1 for m in mots if RE_SUITE_CONSONNES.search(m)) / n
    parasites = sum(1 for m in mots if CARACTERES_PARASITES & set(m)) / n
    # Un mot de trois LETTRES ou plus sans aucune voyelle n'existe
    # pratiquement pas en français. Compter les caractères plutôt que les
    # lettres condamnerait « 17h45 », « D.1 » ou « N°2026-481 » — qui
    # remplissent chaque page d'un dossier pénal.
    sans_voyelle = sum(1 for m in mots if _lettres(m) >= 3 and not (VOYELLES & set(m))) / n
    # Lettres isolées, hors celles qui forment de vrais mots ("à", "y", "a")
    # ou le début d'une élision dont l'apostrophe a sauté à la
    # reconnaissance ("j'étais" lu « J etais ») : c'est banal sur un scan
    # correct et ne dit rien de la lisibilité.
    isoles = sum(1 for m in mots if len(m) == 1 and m.lower() not in LETTRES_ISOLEES_LEGITIMES) / n

    return min(
        1.0,
        casse_interne * 3
        + parasites * 5
        + sans_voyelle * 4
        + isoles * 4
        + chiffre_dans_mot * 5
        + suite_consonnes * 4,
    )


def pages_peu_lisibles(
    db: sqlite3.Connection, seuil: float = SEUIL_ILLISIBILITE
) -> list[tuple[int, float, bool]]:
    """Liste les pages à relire sur l'original : (numéro de page, score, OCR
    appliqué par nous)."""
    resultats = []
    for page in db.execute("SELECT numero_global, texte, ocr_applique FROM pages ORDER BY numero_global"):
        score = score_illisibilite(page["texte"])
        if score >= seuil:
            resultats.append((page["numero_global"], score, bool(page["ocr_applique"])))
    return resultats


def grouper_en_plages(numeros: list[int]) -> list[tuple[int, int]]:
    """Regroupe des numéros de page consécutifs : [3, 4, 5, 9] -> [(3, 5), (9, 9)].
    Un avocat lit « pages 12 à 17 », pas une liste de six numéros."""
    plages: list[tuple[int, int]] = []
    for numero in sorted(numeros):
        if plages and numero == plages[-1][1] + 1:
            plages[-1] = (plages[-1][0], numero)
        else:
            plages.append((numero, numero))
    return plages
