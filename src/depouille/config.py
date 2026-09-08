"""Chargement de la configuration et des garde-fous réseau.

Aucune valeur par défaut ne doit permettre un appel réseau silencieux :
si aucune configuration n'est trouvée et que --offline n'est pas précisé,
le provider LLM refuse de s'initialiser plutôt que de deviner une clé API.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TarifModele:
    entree_par_million: float = 0.0
    sortie_par_million: float = 0.0


@dataclass
class Config:
    offline: bool = False
    provider: str = "offline"
    api_key: str = ""
    modele_classification: str = ""
    modele_analyse: str = ""
    tarifs: dict[str, TarifModele] = field(default_factory=dict)
    seuil_confiance: float = 0.7
    seuil_flou_ocr: int = 97
    chemin_config: Path | None = None

    def cout(self, modele: str, tokens_in: int, tokens_out: int) -> float:
        tarif = self.tarifs.get(modele)
        if tarif is None:
            return 0.0
        return (tokens_in / 1_000_000) * tarif.entree_par_million + (tokens_out / 1_000_000) * tarif.sortie_par_million

    def resume_reseau(self) -> str:
        """Ligne affichée au démarrage : ce qui sort de la machine, et où."""
        if self.offline or self.provider == "offline":
            return "[réseau] --offline actif : aucun appel sortant."
        return (
            f"[réseau] appels LLM activés -> provider={self.provider}, "
            f"modèle classification={self.modele_classification or '?'}, "
            f"modèle analyse={self.modele_analyse or '?'}"
        )


def charger_config(chemin: Path | None, offline: bool) -> Config:
    if offline:
        return Config(offline=True, provider="offline")

    if chemin is None:
        chemin = Path("config.toml")

    if not chemin.exists():
        raise FileNotFoundError(
            f"Fichier de configuration introuvable : {chemin}. "
            "Copie config.example.toml en config.toml et renseigne tes valeurs, "
            "ou relance avec --offline pour n'utiliser que les extractions déterministes."
        )

    with chemin.open("rb") as f:
        data = tomllib.load(f)

    llm = data.get("llm", {})
    api_key = llm.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    provider = llm.get("provider", "offline")

    tarifs = {
        nom: TarifModele(
            entree_par_million=t.get("entree_par_million", 0.0),
            sortie_par_million=t.get("sortie_par_million", 0.0),
        )
        for nom, t in llm.get("tarifs", {}).items()
    }

    classification = data.get("classification", {})
    verification = data.get("verification", {})

    return Config(
        offline=False,
        provider=provider,
        api_key=api_key,
        modele_classification=llm.get("modele_classification", ""),
        modele_analyse=llm.get("modele_analyse", ""),
        tarifs=tarifs,
        seuil_confiance=classification.get("seuil_confiance", 0.7),
        seuil_flou_ocr=verification.get("seuil_flou_ocr", 97),
        chemin_config=chemin,
    )
