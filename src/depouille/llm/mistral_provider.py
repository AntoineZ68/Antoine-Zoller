"""Provider Mistral (France, option de résidence des données en UE — voir
PLAN.md section 1.1). API compatible OpenAI (endpoint /v1/chat/completions),
appelée directement en HTTP pour ne pas ajouter de SDK supplémentaire."""

from __future__ import annotations

from .base import LLMProvider, ReponseLLM

URL_API = "https://api.mistral.ai/v1/chat/completions"


class MistralProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "Clé API Mistral manquante. Renseigne-la dans config.toml "
                "(section [llm].api_key) ou via la variable d'environnement "
                "MISTRAL_API_KEY, ou relance avec --offline."
            )
        self._api_key = api_key

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        import requests  # import différé : jamais chargé en mode offline

        reponse = requests.post(
            URL_API,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": modele,
                "messages": [
                    {"role": "system", "content": systeme},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        reponse.raise_for_status()
        data = reponse.json()
        texte = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ReponseLLM(
            texte=texte,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )
