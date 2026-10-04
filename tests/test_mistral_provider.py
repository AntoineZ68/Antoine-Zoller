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


class _Reponse:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}


def _requetes(monkeypatch, issues: list) -> list[dict]:
    """Remplace requests.post : chaque appel consomme une issue (réponse
    ou exception). Aucune attente réelle."""
    import requests

    import depouille.llm.mistral_provider as module

    envois: list[dict] = []

    def post(url, headers, json, timeout):
        envois.append(json)
        issue = issues[len(envois) - 1]
        if isinstance(issue, Exception):
            raise issue
        return issue

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    return envois


def test_refus_temporaire_reessaye(monkeypatch) -> None:
    import requests

    envois = _requetes(monkeypatch, [
        _Reponse(429, {"Retry-After": "1"}),
        requests.ConnectionError("coupure"),
        _Reponse(503),
        _Reponse(200),
    ])
    reponse = MistralProvider("cle").appeler("systeme", "prompt", "mistral-large-latest")
    assert reponse.texte == "ok" and reponse.tokens_in == 3
    assert len(envois) == 4
    assert envois[0]["temperature"] <= 0.2, "peu d'aléa : deux passages, même résultat"


def test_refus_definitif_non_reessaye(monkeypatch) -> None:
    import requests

    envois = _requetes(monkeypatch, [_Reponse(401)])
    with pytest.raises(requests.HTTPError):
        MistralProvider("cle").appeler("systeme", "prompt", "mistral-large-latest")
    assert len(envois) == 1, "une clé invalide ne se corrige pas en réessayant"


def test_abandon_apres_le_nombre_maximal_de_tentatives(monkeypatch) -> None:
    import requests

    from depouille.llm.mistral_provider import TENTATIVES_MAX

    envois = _requetes(monkeypatch, [_Reponse(429)] * TENTATIVES_MAX)
    with pytest.raises(requests.HTTPError):
        MistralProvider("cle").appeler("systeme", "prompt", "mistral-large-latest")
    assert len(envois) == TENTATIVES_MAX
