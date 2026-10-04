"""Provider Mistral (France, option de résidence des données en UE — voir
PLAN.md section 1.1). API compatible OpenAI (endpoint /v1/chat/completions),
appelée directement en HTTP pour ne pas ajouter de SDK supplémentaire."""

from __future__ import annotations

import random
import time

from .base import LLMProvider, ReponseLLM

URL_API = "https://api.mistral.ai/v1/chat/completions"

# Refus temporaires (429 : débit du compte dépassé ; 5xx : surcharge) et
# coupures réseau : réessayés avec une attente croissante, en respectant
# Retry-After si l'API l'indique. Sans cela, un appel refusé est perdu :
# mesuré sur le banc d'essai, 7 appels d'analyse sur 22 refusés (429), et
# avec eux le résumé et les quatre réponses aux questions.
TENTATIVES_MAX = 6
CODES_TEMPORAIRES = {429, 500, 502, 503, 504}
ATTENTE_MAX_S = 60.0
# Connexion, puis lecture : un résumé détaillé de Mistral Large dépasse
# parfois la minute de génération sur un gros dossier.
DELAIS_S = (10, 180)


def _attente(tentative: int, retry_after: str | None) -> float:
    try:
        return min(float(retry_after), ATTENTE_MAX_S)
    except (TypeError, ValueError):
        # Un peu d'aléa : les appels menés de front ne repartent pas tous
        # à la même seconde, ce qui reproduirait le dépassement.
        return min(2.0 * 2**tentative, ATTENTE_MAX_S) * random.uniform(0.75, 1.25)


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

        for tentative in range(TENTATIVES_MAX):
            derniere = tentative == TENTATIVES_MAX - 1
            try:
                reponse = self._poster(requests, systeme, prompt, modele)
            except (requests.ConnectionError, requests.Timeout):
                if derniere:
                    raise
                time.sleep(_attente(tentative, None))
                continue
            if reponse.status_code not in CODES_TEMPORAIRES or derniere:
                break
            time.sleep(_attente(tentative, reponse.headers.get("Retry-After")))
        reponse.raise_for_status()
        data = reponse.json()
        texte = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ReponseLLM(
            texte=texte,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )

    def _poster(self, requests, systeme: str, prompt: str, modele: str):
        return requests.post(
            URL_API,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": modele,
                # Extraction et synthèse factuelles : peu d'aléa, pour que deux
                # passages sur le même dossier donnent le même résultat.
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": systeme},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=DELAIS_S,
        )
