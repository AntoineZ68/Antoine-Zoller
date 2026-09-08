from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console

from depouille.chrono import lancer_chrono
from depouille.classify import lancer_classification
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.declarations import lancer_declarations
from depouille.ingest import lancer_ingestion

from .fixtures.generate_fixture import VeriteTerrain, generer_dossier_fictif


@dataclass
class DossierTraite:
    verite: VeriteTerrain
    db: sqlite3.Connection
    affaire_dir: Path
    config: Config


@pytest.fixture(scope="session")
def dossier_traite(tmp_path_factory: pytest.TempPathFactory) -> DossierTraite:
    """Génère le jeu d'essai et fait tourner tout le pipeline en --offline
    une seule fois pour la session de tests (l'OCR est le poste le plus
    coûteux, pas la peine de le refaire pour chaque test)."""
    dossier_fixture = tmp_path_factory.mktemp("fixture")
    verite = generer_dossier_fictif(dossier_fixture)

    affaire_dir = tmp_path_factory.mktemp("affaire")
    db = ouvrir_db(affaire_dir / "depouille.db")
    console = Console(quiet=True)
    config = Config(offline=True, provider="offline")

    lancer_ingestion(db, [dossier_fixture / "dossier_fictif.pdf"], affaire_dir, force=False, console=console)
    lancer_classification(db, config, force=False, console=console)
    lancer_chrono(db, config, force=False, console=console)
    lancer_declarations(db, config, force=False, console=console)

    return DossierTraite(verite=verite, db=db, affaire_dir=affaire_dir, config=config)
