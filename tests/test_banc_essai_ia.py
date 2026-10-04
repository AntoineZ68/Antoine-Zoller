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


def test_banc_refuse_de_tourner_sans_cle(tmp_path) -> None:
    import os

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    r = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "banc_essai_ia.py"), "--sortie", str(tmp_path)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr
