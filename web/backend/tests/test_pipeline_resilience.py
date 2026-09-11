"""Régression sur un incident réel : un dossier entièrement traité (les 6
étapes, tous les livrables déjà envoyés dans le Storage) est parti en
"erreur" à cause d'un simple 504 Gateway Timeout de Supabase sur une mise
à jour de statut — perdant un résultat par ailleurs complet et correct.
"""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

import pytest

from app.pipeline import _avec_retries, _televerser_resultats


def test_avec_retries_reussit_apres_des_echecs_transitoires() -> None:
    appels = {"n": 0}

    def requete_flaky():
        appels["n"] += 1
        if appels["n"] < 3:
            raise RuntimeError("504 Gateway Timeout")
        return "ok"

    resultat = _avec_retries(requete_flaky, tentatives=3, delai_s=0.01)
    assert resultat == "ok"
    assert appels["n"] == 3


def test_avec_retries_relance_la_derniere_erreur_si_toujours_en_echec() -> None:
    def requete_toujours_en_echec():
        raise RuntimeError("504 Gateway Timeout")

    with pytest.raises(RuntimeError, match="504 Gateway Timeout"):
        _avec_retries(requete_toujours_en_echec, tentatives=2, delai_s=0.01)


class _BucketFactice:
    def upload(self, *args, **kwargs) -> None:
        pass


class _TableFactice:
    def update(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        raise RuntimeError("504 Gateway Timeout")


class _StorageFactice:
    def from_(self, *args, **kwargs):
        return _BucketFactice()


class _SupabaseFactice:
    storage = _StorageFactice()

    def table(self, *args, **kwargs):
        return _TableFactice()


def test_echec_de_resultat_db_path_ne_fait_pas_echouer_le_televersement(tmp_path, monkeypatch) -> None:
    """resultat_db_path n'est relu nulle part ailleurs (donnees_dossier
    reconstruit le chemin directement à partir de dossier_id) : son échec,
    même après retries, ne doit jamais faire perdre un résultat par ailleurs
    complet."""
    import app.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_avec_retries", lambda requete, **kw: requete())
    # _televerser_resultats ne doit lever aucune exception malgré l'échec
    # systématique de la mise à jour Postgres simulée par _TableFactice.
    _televerser_resultats(_SupabaseFactice(), "dossier-test", tmp_path)
