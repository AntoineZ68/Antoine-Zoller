from __future__ import annotations

from ..config import Config
from .base import ErreurModeOffline, LLMProvider, OfflineProvider, ReponseLLM


def obtenir_provider(config: Config) -> LLMProvider:
    if config.offline or config.provider == "offline":
        return OfflineProvider()
    if config.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=config.api_key)
    raise ValueError(
        f"Provider LLM inconnu : {config.provider!r}. "
        "Valeurs supportées dans cette version : 'anthropic', 'offline'."
    )


__all__ = ["obtenir_provider", "LLMProvider", "ReponseLLM", "ErreurModeOffline", "OfflineProvider"]
