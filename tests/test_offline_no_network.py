"""--offline doit se limiter aux extractions déterministes : aucune requête
réseau, quelle qu'elle soit, ne doit être émise pendant tout le pipeline."""

from __future__ import annotations

import socket

import pytest
from rich.console import Console

from depouille.chrono import lancer_chrono
from depouille.classify import lancer_classification
from depouille.config import Config, charger_config
from depouille.db import ouvrir_db
from depouille.declarations import lancer_declarations
from depouille.ingest import lancer_ingestion
from depouille.llm import ErreurModeOffline, obtenir_provider
from depouille.llm.base import OfflineProvider

from .fixtures.generate_fixture import generer_dossier_fictif


def test_config_offline_ne_necessite_aucun_fichier() -> None:
    """--offline doit fonctionner même sans config.toml : c'est le filet de
    sécurité minimal, il ne doit pas dépendre d'une configuration présente."""
    config = charger_config(None, offline=True)
    assert config.offline is True
    assert config.provider == "offline"


def test_provider_offline_refuse_tout_appel() -> None:
    config = Config(offline=True, provider="offline")
    provider = obtenir_provider(config)
    assert isinstance(provider, OfflineProvider)
    with pytest.raises(ErreurModeOffline):
        provider.appeler(systeme="x", prompt="y", modele="z")


def test_pipeline_complet_offline_sans_aucune_connexion_reseau(tmp_path, monkeypatch) -> None:
    def _bloque(*args, **kwargs):
        raise AssertionError("tentative de connexion réseau alors que --offline est actif")

    monkeypatch.setattr(socket.socket, "connect", _bloque)
    monkeypatch.setattr(socket, "create_connection", _bloque)

    dossier_fixture = tmp_path / "fixture"
    verite = generer_dossier_fictif(dossier_fixture)

    affaire_dir = tmp_path / "affaire"
    db = ouvrir_db(affaire_dir / "depouille.db")
    console = Console(quiet=True)
    config = Config(offline=True, provider="offline")

    lancer_ingestion(db, [dossier_fixture / "dossier_fictif.pdf"], affaire_dir, force=False, console=console)
    lancer_classification(db, config, force=False, console=console)
    lancer_chrono(db, config, force=False, console=console)
    lancer_declarations(db, config, force=False, console=console)

    assert db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == verite.nb_pages
    assert db.execute("SELECT COUNT(*) FROM pieces").fetchone()[0] > 0


def test_chronologie_des_faits_non_generee_en_offline(dossier_traite) -> None:
    """La chronologie des faits nécessite le modèle : en --offline, elle
    doit rester vide plutôt que d'être produite par un mécanisme de repli
    non déterministe."""
    nb = dossier_traite.db.execute("SELECT COUNT(*) FROM evenements_faits").fetchone()[0]
    assert nb == 0
