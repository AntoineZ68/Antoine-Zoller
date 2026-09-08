"""CLI `depouille`. Chaque commande affiche la bannière réseau avant toute
action, conformément à la règle de confidentialité (PLAN.md / cahier des
charges)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .config import Config, charger_config
from .db import ouvrir_db

app = typer.Typer(add_completion=False, help="Dépouillement local de procédure pénale.")
console = Console()

OptionOffline = typer.Option(False, "--offline", help="Aucun appel réseau : extractions déterministes uniquement.")
OptionConfig = typer.Option(None, "--config", help="Chemin du fichier de configuration (défaut : ./config.toml).")
OptionForce = typer.Option(False, "--force", help="Retraiter les lignes déjà marquées comme faites.")


def _charger(config_path: Optional[Path], offline: bool) -> Config:
    cfg = charger_config(config_path, offline)
    console.print(cfg.resume_reseau())
    return cfg


def _db_affaire(affaire: str) -> Path:
    return Path(affaire) / "depouille.db"


@app.command()
def ingest(
    sources: list[Path] = typer.Argument(..., help="PDF source(s) du dossier de procédure."),
    affaire: str = typer.Option(..., "--affaire", help="Nom de l'affaire (dossier de travail)."),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
    force: bool = OptionForce,
) -> None:
    """Étape 1 : découpage en pages, extraction texte, OCR si nécessaire."""
    _charger(config_path, offline)
    from .ingest import lancer_ingestion

    db = ouvrir_db(_db_affaire(affaire))
    lancer_ingestion(db, sources, Path(affaire), force=force, console=console)


@app.command()
def classify(
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
    force: bool = OptionForce,
) -> None:
    """Étape 2 : détection des pièces et classification."""
    cfg = _charger(config_path, offline)
    from .classify import lancer_classification

    db = ouvrir_db(_db_affaire(affaire))
    lancer_classification(db, cfg, force=force, console=console)


@app.command()
def index(
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
) -> None:
    """Étape 3 : construction du sommaire (01_index.xlsx)."""
    _charger(config_path, offline)
    from .index_builder import construire_index

    db = ouvrir_db(_db_affaire(affaire))
    construire_index(db, Path(affaire), console=console)


@app.command()
def chrono(
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
    force: bool = OptionForce,
) -> None:
    """Étape 4 : chronologies de procédure et des faits."""
    cfg = _charger(config_path, offline)
    from .chrono import lancer_chrono

    db = ouvrir_db(_db_affaire(affaire))
    lancer_chrono(db, cfg, force=force, console=console)


@app.command()
def decl(
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
    force: bool = OptionForce,
) -> None:
    """Étape 5 : tableau des déclarations et divergences."""
    cfg = _charger(config_path, offline)
    from .declarations import lancer_declarations

    db = ouvrir_db(_db_affaire(affaire))
    lancer_declarations(db, cfg, force=force, console=console)


@app.command()
def build(
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
) -> None:
    """Étape 6 : génération des 6 livrables dans <affaire>/out/."""
    cfg = _charger(config_path, offline)
    from .build_deliverables import construire_livrables

    db = ouvrir_db(_db_affaire(affaire))
    construire_livrables(db, Path(affaire), cfg, console=console)


@app.command()
def run(
    sources: list[Path] = typer.Argument(..., help="PDF source(s) du dossier de procédure."),
    affaire: str = typer.Option(..., "--affaire"),
    config_path: Optional[Path] = OptionConfig,
    offline: bool = OptionOffline,
    force: bool = OptionForce,
) -> None:
    """Enchaîne ingest -> classify -> index -> chrono -> decl -> build."""
    cfg = _charger(config_path, offline)
    from .build_deliverables import construire_livrables
    from .chrono import lancer_chrono
    from .classify import lancer_classification
    from .declarations import lancer_declarations
    from .index_builder import construire_index
    from .ingest import lancer_ingestion

    db = ouvrir_db(_db_affaire(affaire))
    lancer_ingestion(db, sources, Path(affaire), force=force, console=console)
    lancer_classification(db, cfg, force=force, console=console)
    construire_index(db, Path(affaire), console=console)
    lancer_chrono(db, cfg, force=force, console=console)
    lancer_declarations(db, cfg, force=force, console=console)
    construire_livrables(db, Path(affaire), cfg, console=console)


if __name__ == "__main__":
    app()
