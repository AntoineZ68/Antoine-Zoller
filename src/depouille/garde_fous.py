"""Garde-fous déterministes appliqués à tout texte libre produit par le
modèle (résumé, réponse à une question).

La consigne donnée au modèle ne suffit pas : un modèle peut l'ignorer. Ce
filtre, lui, ne dépend pas de sa bonne volonté — un texte qui contient une
qualification juridique n'atteint jamais l'écran."""

from __future__ import annotations

import re

RE_QUALIFICATION = re.compile(
    r"nullit|irr[ée]gul|invocable|qualit[ée] (?:pour|à) agir|\bgrief\b|vice de proc|ill[ée]gal|annulable|entach",
    re.IGNORECASE,
)


def contient_qualification(texte: str) -> bool:
    return bool(RE_QUALIFICATION.search(texte))
