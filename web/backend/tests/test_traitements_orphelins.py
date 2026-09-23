"""Un traitement tourne dans le processus web : si ce processus meurt
(mémoire saturée, redémarrage, mise en veille), plus rien ne peut écrire
« erreur », et le dossier restait affiché « en cours » pour toujours —
indiscernable d'un traitement simplement lent. Observé en réel sur un
dossier de 105 pages bloqué sur « Lecture du document »."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

from datetime import datetime, timedelta, timezone

import app.main as main
from app.pipeline import _Battement, _rappel_progression_ocr

MAINTENANT = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)


def _dossier(statut: str, il_y_a: timedelta) -> dict:
    return {"id": "d1", "statut": statut, "mis_a_jour_le": (MAINTENANT - il_y_a).isoformat()}


def test_traitement_vivant_nest_pas_orphelin() -> None:
    assert not main._est_orphelin(_dossier("en_cours", timedelta(seconds=45)), MAINTENANT)


def test_traitement_silencieux_depuis_dix_battements_est_orphelin() -> None:
    assert main._est_orphelin(_dossier("en_cours", timedelta(minutes=6)), MAINTENANT)


def test_dossier_termine_ou_en_erreur_jamais_orphelin() -> None:
    for statut in ("termine", "erreur"):
        assert not main._est_orphelin(_dossier(statut, timedelta(days=3)), MAINTENANT)


def test_envoi_long_des_fichiers_laisse_du_temps_au_dossier_en_attente() -> None:
    """Pas encore de battement pendant l'envoi : une connexion lente ne doit
    pas faire déclarer mort un dossier qui n'a pas encore commencé."""
    assert not main._est_orphelin(_dossier("en_attente", timedelta(minutes=10)), MAINTENANT)
    assert main._est_orphelin(_dossier("en_attente", timedelta(minutes=45)), MAINTENANT)


def test_date_illisible_ne_declare_rien() -> None:
    assert not main._est_orphelin({"id": "d1", "statut": "en_cours", "mis_a_jour_le": "n/a"}, MAINTENANT)


def test_format_horodatage_supabase_accepte() -> None:
    """Supabase renvoie des microsecondes et un fuseau explicite."""
    dossier = {"id": "d1", "statut": "en_cours", "mis_a_jour_le": "2026-09-23T19:40:12.123456+00:00"}
    assert main._est_orphelin(dossier, MAINTENANT)


class _Requete:
    def __init__(self, journal: list, table: str) -> None:
        self.journal, self.table, self.filtres, self.champs = journal, table, [], None

    def update(self, champs):
        self.champs = champs
        return self

    def upsert(self, champs, on_conflict=None):
        self.champs = champs
        return self

    def eq(self, colonne, valeur):
        self.filtres.append((colonne, valeur))
        return self

    def execute(self):
        self.journal.append((self.table, self.champs, self.filtres))
        return self


class _FauxSupabase:
    def __init__(self, journal: list) -> None:
        self.journal = journal

    def table(self, nom):
        return _Requete(self.journal, nom)


def test_orphelin_passe_en_erreur_avec_un_message_actionnable(monkeypatch) -> None:
    journal: list = []
    monkeypatch.setattr(main, "client_service", lambda: _FauxSupabase(journal))
    dossier = {"id": "d1", "statut": "en_cours",
               "mis_a_jour_le": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}

    main._cloturer_orphelins(_FauxSupabase(journal), [dossier])

    assert dossier["statut"] == "erreur"
    assert "renvoyez" in dossier["message_erreur"]
    maj_dossier = next(e for e in journal if e[0] == "dossiers")
    assert ("statut", "en_cours") in maj_dossier[2], "ne jamais écraser un dossier qui vient de se terminer"
    maj_etape = next(e for e in journal if e[0] == "traitement_etapes")
    assert maj_etape[1]["statut"] == "erreur"


def test_dossier_vivant_laisse_intact(monkeypatch) -> None:
    journal: list = []
    monkeypatch.setattr(main, "client_service", lambda: _FauxSupabase(journal))
    dossier = {"id": "d1", "statut": "en_cours", "mis_a_jour_le": datetime.now(timezone.utc).isoformat()}
    main._cloturer_orphelins(_FauxSupabase(journal), [dossier])
    assert journal == [] and dossier["statut"] == "en_cours"


def test_progression_perdue_ne_casse_jamais_le_traitement() -> None:
    """Tant que la migration 0004 n'est pas appliquée, l'écriture échoue :
    le traitement doit continuer, et ne pas réessayer à chaque tranche."""

    class SupabaseSansColonne:
        appels = 0

        def table(self, nom):
            SupabaseSansColonne.appels += 1
            raise RuntimeError("column traitement_etapes.detail does not exist")

    rappel = _rappel_progression_ocr(SupabaseSansColonne(), "d1")
    for faites in (0, 5, 10):
        rappel(faites, 105, "dossier.pdf")
    assert SupabaseSansColonne.appels == 1


def test_progression_ecrit_un_avancement_lisible() -> None:
    journal: list = []
    _rappel_progression_ocr(_FauxSupabase(journal), "d1")(23, 105, "dossier.pdf")
    table, champs, _ = journal[0]
    assert table == "traitement_etapes"
    assert champs["etape"] == "ingest"
    assert champs["detail"] == "Pages numérisées reconnues : 23 / 105"


def test_battement_arrete_sans_avoir_demarre() -> None:
    """Si la toute première mise à jour échoue, l'arrêt dans le `finally`
    ne doit pas lever à son tour et masquer l'erreur d'origine."""
    _Battement("d1").arreter()


def test_battement_rafraichit_le_dossier_tant_que_le_traitement_vit(monkeypatch) -> None:
    import time

    import app.pipeline as pipeline

    journal: list = []
    monkeypatch.setattr(pipeline, "INTERVALLE_BATTEMENT_S", 0.01)
    monkeypatch.setattr(pipeline, "client_service", lambda: _FauxSupabase(journal))

    battement = _Battement("d1")
    battement.etape = "ingest"
    battement.demarrer()
    time.sleep(0.15)
    battement.arreter()
    nb_pendant = len(journal)
    time.sleep(0.05)

    assert nb_pendant >= 3
    assert all(table == "dossiers" and champs == {"etape_courante": "ingest"} for table, champs, _ in journal)
    assert len(journal) == nb_pendant, "plus aucun battement après l'arrêt"
