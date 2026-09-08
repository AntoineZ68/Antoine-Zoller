"""Interface abstraite pour les appels au modèle de langage.

Découplée du SDK Anthropic pour permettre l'ajout futur d'un provider
Bedrock/Vertex (résidence des données UE, voir PLAN.md section 1.1) sans
toucher au pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ReponseLLM:
    texte: str
    tokens_in: int
    tokens_out: int


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
