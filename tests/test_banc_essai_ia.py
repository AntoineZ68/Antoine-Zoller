"""Le banc d'essai IA doit rester utilisable : on le fait tourner hors
ligne (sans clé), sur le dossier de contrôle, et il doit retrouver ce qu'on
sait s'y trouver."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


def test_banc_hors_ligne_retrouve_les_contradictions_attendues(tmp_path) -> None:
    r = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "banc_essai_ia.py"), "--hors-ligne", "--sortie", str(tmp_path)],
        capture_output=True, text=True, timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    rapport = (tmp_path / "rapport.md").read_text()
    assert "ÉCHEC" not in rapport and "KO " not in rapport, rapport
    assert "Interpellation de Julien MORVANNEC : 07h50 ou 08h05" in rapport
    assert "GH-428-KL ou GH-482-KL" in rapport


VARIABLES_LLM = ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "MODELE_CLASSIFICATION", "MODELE_ANALYSE")


def _sans_cle(**ajouts: str) -> dict:
    """Environnement sans aucune clé : une clé présente sur la machine (celle
    d'un autre provider comprise) lançait de vrais appels, payants."""
    import os

    env = {k: v for k, v in os.environ.items() if k not in VARIABLES_LLM}
    env.update(ajouts)
    return env


def test_banc_refuse_de_tourner_sans_cle(tmp_path) -> None:
    r = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "banc_essai_ia.py"), "--sortie", str(tmp_path)],
        capture_output=True, text=True, timeout=60, env=_sans_cle(),
    )
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr


def test_banc_mistral_refuse_de_tourner_sans_sa_cle(tmp_path) -> None:
    r = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "banc_essai_ia.py"), "--sortie", str(tmp_path)],
        capture_output=True, text=True, timeout=60, env=_sans_cle(LLM_PROVIDER="mistral", ANTHROPIC_API_KEY="x"),
    )
    assert r.returncode == 2 and "MISTRAL_API_KEY" in r.stderr


def test_banc_refuse_un_provider_inconnu(tmp_path) -> None:
    r = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "banc_essai_ia.py"), "--sortie", str(tmp_path)],
        capture_output=True, text=True, timeout=60, env=_sans_cle(LLM_PROVIDER="mistrall", MISTRAL_API_KEY="x"),
    )
    assert r.returncode == 2 and "mistrall" in r.stderr
