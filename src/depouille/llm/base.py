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


TAILLE_LOT_CARACTERES = 8000


def lots_de_pages(pages, taille_max: int = TAILLE_LOT_CARACTERES) -> list[list]:
    """Découpe les pages d'une pièce en lots tenant chacun dans un appel au
    modèle, au lieu de tronquer la pièce à la taille d'un seul appel.

    Une pièce longue (audition de garde à vue sur huit ou neuf pages) dépasse
    largement la taille d'un prompt : la couper en gardant le début revenait
    à ne jamais analyser la fin de l'interrogatoire — c'est-à-dire souvent
    l'endroit où se trouvent les aveux, les rétractations ou les
    contradictions. Et cette perte était SILENCIEUSE : ni message, ni trace.
    Mesuré sur un dossier d'essai de 47 pages : 8 929 caractères de deux
    auditions purement ignorés (32 % et 40 % de la pièce).

    Le découpage se fait sur des frontières de pages, jamais au milieu : les
    citations renvoyées par le modèle sont ancrées à un numéro de page, et
    couper une page en deux produirait des citations à cheval, invérifiables.
    Une page qui dépasse à elle seule la taille maximale forme son propre lot
    (elle sera tronquée par l'appelant, faute de mieux — mais c'est alors une
    page entière, pas la moitié d'une pièce)."""
    lots: list[list] = []
    lot_courant: list = []
    taille_courante = 0
    for page in pages:
        taille_page = len(page["texte"])
        if lot_courant and taille_courante + taille_page > taille_max:
            lots.append(lot_courant)
            lot_courant, taille_courante = [], 0
        lot_courant.append(page)
        taille_courante += taille_page
    if lot_courant:
        lots.append(lot_courant)
    return lots


def tranches_de_texte(texte: str, taille_max: int = TAILLE_LOT_CARACTERES) -> list[str]:
    """Découpe un texte en tranches sur des frontières de lignes.

    Sert là où on ne dispose que du texte concaténé d'une pièce et pas de ses
    pages : couper en plein milieu d'un mot (`texte[:4000]`) pouvait amputer
    précisément la ligne d'identité qu'on cherchait à lire."""
    tranches: list[str] = []
    courante: list[str] = []
    taille = 0
    for ligne in texte.splitlines(keepends=True):
        if courante and taille + len(ligne) > taille_max:
            tranches.append("".join(courante))
            courante, taille = [], 0
        courante.append(ligne)
        taille += len(ligne)
    if courante:
        tranches.append("".join(courante))
    return tranches


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
