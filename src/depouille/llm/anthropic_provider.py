"""Provider Anthropic direct (API hébergée aux Etats-Unis, voir PLAN.md 1.1).

Import différé du SDK Anthropic : ce module ne doit jamais être importé,
et le SDK jamais chargé, quand --offline est actif.
"""

from __future__ import annotations

from .base import LLMProvider, ReponseLLM


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "Clé API Anthropic manquante. Renseigne-la dans config.toml "
                "(section [llm].api_key) ou via la variable d'environnement "
                "ANTHROPIC_API_KEY, ou relance avec --offline."
            )
        import anthropic  # import différé : jamais chargé en mode offline

        # Sans timeout explicite, le SDK attend par défaut plusieurs minutes
        # avant d'abandonner un appel qui ne répond plus — observé en réel :
        # une étape du pipeline restait bloquée "en cours" en apparence sans
        # jamais échouer ni aboutir. 60 s (même valeur que MistralProvider)
        # borne l'attente à une durée raisonnable pour une réponse dont le
        # nombre de tokens de sortie reste modéré (max_tokens=4096).
        self._client = anthropic.Anthropic(api_key=api_key, timeout=60.0)

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        reponse = self._client.messages.create(
            model=modele,
            max_tokens=4096,
            system=systeme,
            messages=[{"role": "user", "content": prompt}],
        )
        texte = "".join(bloc.text for bloc in reponse.content if bloc.type == "text")
        return ReponseLLM(
            texte=texte,
            tokens_in=reponse.usage.input_tokens,
            tokens_out=reponse.usage.output_tokens,
        )
