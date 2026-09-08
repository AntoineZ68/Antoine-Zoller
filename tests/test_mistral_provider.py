"""Le connecteur Mistral suit la même interface que le connecteur Anthropic
et les mêmes garde-fous — aucun appel réseau réel dans ces tests."""

from __future__ import annotations

import pytest

from depouille.config import Config, charger_config
from depouille.llm import obtenir_provider
from depouille.llm.mistral_provider import MistralProvider


def test_obtenir_provider_retourne_mistral() -> None:
    config = Config(offline=False, provider="mistral", api_key="une-cle")
    provider = obtenir_provider(config)
    assert isinstance(provider, MistralProvider)


def test_mistral_provider_refuse_sans_cle() -> None:
    with pytest.raises(ValueError):
        MistralProvider(api_key="")


def test_config_lit_mistral_api_key_depuis_lenvironnement(tmp_path, monkeypatch) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text('[llm]\nprovider = "mistral"\n', encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-depuis-environnement")

    config = charger_config(config_toml, offline=False)

    assert config.provider == "mistral"
    assert config.api_key == "cle-depuis-environnement"


def test_config_ne_confond_pas_les_variables_denvironnement(tmp_path, monkeypatch) -> None:
    """Un provider "anthropic" ne doit jamais récupérer accidentellement
    MISTRAL_API_KEY, et inversement."""
    config_toml = tmp_path / "config.toml"
    config_toml.write_text('[llm]\nprovider = "anthropic"\n', encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-mistral")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    config = charger_config(config_toml, offline=False)

    assert config.api_key == ""
