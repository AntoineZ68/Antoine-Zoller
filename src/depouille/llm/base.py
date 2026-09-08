"""Interface abstraite pour les appels au modèle de langage.

Découplée du SDK Anthropic pour permettre l'ajout futur d'un provider
Bedrock/Vertex (résidence des données UE, voir PLAN.md section 1.1) sans
toucher au pipeline.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ReponseLLM:
    texte: str
    tokens_in: int
    tokens_out: int


_RE_BLOC_CODE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extraire_json(texte: str):
    """Les modèles enveloppent souvent leur réponse JSON dans un bloc de
    code markdown et y ajoutent une justification en prose, même quand la
    consigne dit explicitement de répondre uniquement en JSON — c'est un
    comportement réel et courant, pas une anomalie à ignorer. On isole le
    JSON avant de le parser plutôt que d'échouer sur un texte qui n'en est
    pas un dans son intégralité."""
    texte = texte.strip()
    m = _RE_BLOC_CODE.search(texte)
    candidat = m.group(1).strip() if m else texte
    try:
        return json.loads(candidat)
    except json.JSONDecodeError:
        pass

    for ouvrant, fermant in (("{", "}"), ("[", "]")):
        debut = candidat.find(ouvrant)
        fin = candidat.rfind(fermant)
        if debut != -1 and fin != -1 and fin > debut:
            try:
                return json.loads(candidat[debut : fin + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Impossible d'extraire du JSON valide de la réponse du modèle : {texte[:200]!r}")


class LLMProvider(ABC):
    @abstractmethod
    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        """Envoie un prompt et renvoie la réponse brute (texte, pas de parsing)."""
        raise NotImplementedError


class ErreurModeOffline(RuntimeError):
    """Levée si le pipeline tente un appel réseau alors que --offline est actif."""


class OfflineProvider(LLMProvider):
    """Provider no-op : toute tentative d'appel est une erreur de programmation,
    pas un back-off silencieux. Le pipeline doit vérifier `config.offline` avant
    d'invoquer un provider, cette classe est un filet de sécurité en profondeur."""

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        raise ErreurModeOffline(
            "Tentative d'appel LLM alors que le mode --offline est actif. "
            "Ceci est un bug : le code appelant doit contourner le LLM en mode offline."
        )
