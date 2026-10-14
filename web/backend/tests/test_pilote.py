"""Ce dont un avocat du pilote a besoin quand quelque chose se passe mal :
un message qu'il comprend, relancer sans renvoyer ses fichiers, et un moyen
de signaler le problème. Supabase est remplacé par une doublure en mémoire."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "cle-anon-de-test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "cle-service-de-test")

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.pipeline import message_pour_l_avocat

UTILISATEUR = "u1"


class _Requete:
    def __init__(self, base: "_FauxSupabase", table: str) -> None:
        self.base, self.table, self.op, self.valeurs, self.filtres = base, table, "select", None, []

    def select(self, *_a, **_k):
        return self

    def insert(self, valeurs):
        self.op, self.valeurs = "insert", valeurs
        return self

    def update(self, valeurs):
        self.op, self.valeurs = "update", valeurs
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, colonne, valeur):
        self.filtres.append((colonne, valeur))
        return self

    def order(self, *_a, **_k):
        return self

    def _retenue(self, ligne: dict) -> bool:
        return all(str(ligne.get(c)) == str(v) for c, v in self.filtres)

    def execute(self):
        if self.table not in self.base.tables:
            raise RuntimeError(f'relation "{self.table}" does not exist')
        lignes = self.base.tables[self.table]
        if self.op == "select":
            data = [dict(l) for l in lignes if self._retenue(l)]
        elif self.op == "insert":
            lignes.append(dict(self.valeurs))
            data = [dict(self.valeurs)]
        elif self.op == "update":
            data = []
            for ligne in lignes:
                if self._retenue(ligne):
                    ligne.update(self.valeurs)
                    data.append(dict(ligne))
        else:
            data = [dict(l) for l in lignes if self._retenue(l)]
            self.base.tables[self.table] = [l for l in lignes if not self._retenue(l)]
        return SimpleNamespace(data=data)


class _Seau:
    def __init__(self, fichiers: dict[str, bytes]) -> None:
        self.fichiers = fichiers

    def list(self, prefixe=None, *_a, **_k):
        prefixe = (prefixe or "").rstrip("/") + "/"
        return [{"name": c[len(prefixe):], "id": c} for c in self.fichiers if c.startswith(prefixe)]

    def download(self, chemin):
        return self.fichiers[chemin]


class _FauxSupabase:
    def __init__(self, tables: dict[str, list[dict]], fichiers: dict[str, bytes]) -> None:
        self.tables = tables
        self.storage = SimpleNamespace(from_=lambda _nom: _Seau(fichiers))

    def table(self, nom):
        return _Requete(self, nom)


def _dossier(statut: str) -> dict:
    maintenant = datetime.now(timezone.utc).isoformat()
    return {
        "id": "d1", "owner_id": UTILISATEUR, "nom": "Dupont", "reference": None, "statut": statut,
        "etape_courante": "chrono" if statut == "erreur" else None, "message_erreur": "échec",
        "nb_pages": None, "fichier_source_path": f"{UTILISATEUR}/d1/", "cree_le": maintenant, "mis_a_jour_le": maintenant,
    }


@pytest.fixture
def appli(monkeypatch):
    def preparer(statut: str = "erreur", avec_table_retours: bool = True, fichiers: dict | None = None):
        tables = {
            "dossiers": [_dossier(statut)],
            "traitement_etapes": [{"dossier_id": "d1", "etape": "chrono", "statut": "erreur"}],
        }
        if avec_table_retours:
            tables["retours"] = []
        base = _FauxSupabase(tables, {f"{UTILISATEUR}/d1/tome1.pdf": b"%PDF-1"} if fichiers is None else fichiers)
        traitements: list = []
        monkeypatch.setattr(main, "client_service", lambda: base)
        monkeypatch.setattr(main, "traiter_dossier", lambda dossier_id, chemins, offline=False: traitements.append(
            (dossier_id, [c.name for c in chemins], [c.read_bytes() for c in chemins])
        ))
        main.app.dependency_overrides[main._contexte_utilisateur] = lambda: (base, UTILISATEUR)
        return TestClient(main.app), base, traitements

    yield preparer
    main.app.dependency_overrides.clear()


def test_message_d_erreur_lisible_sans_trace_technique() -> None:
    try:
        raise RuntimeError("Tesseract n'a pas produit de calque de texte.\nligne 2")
    except RuntimeError as exc:
        message = message_pour_l_avocat(exc, "ingest")
    assert message.startswith("Le traitement s'est arrêté pendant la lecture des PDF")
    assert "relancez le traitement" in message
    assert "Traceback" not in message and "ligne 2" not in message
    assert "RuntimeError — Tesseract n'a pas produit de calque de texte." in message


def test_memoire_saturee_conseille_de_decouper() -> None:
    assert "plusieurs tomes" in message_pour_l_avocat(MemoryError(), "ingest")


def test_relancer_reprend_les_fichiers_deja_transmis(appli) -> None:
    client, base, traitements = appli()
    reponse = client.post("/api/dossiers/d1/relancer")
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["statut"] == "en_attente"
    assert base.tables["dossiers"][0]["message_erreur"] is None
    assert base.tables["traitement_etapes"] == [], "les étapes en erreur de l'essai précédent sont effacées"
    assert traitements == [("d1", ["tome1.pdf"], [b"%PDF-1"])]


def test_relancer_refuse_un_dossier_qui_n_est_pas_en_erreur(appli) -> None:
    client, _, traitements = appli(statut="termine")
    reponse = client.post("/api/dossiers/d1/relancer")
    assert reponse.status_code == 409
    assert traitements == []


def test_relancer_sans_fichiers_dit_quoi_faire(appli) -> None:
    client, _, traitements = appli(fichiers={})
    reponse = client.post("/api/dossiers/d1/relancer")
    assert reponse.status_code == 409 and "renvoyez" in reponse.json()["detail"]
    assert traitements == []


def test_signalement_enregistre(appli) -> None:
    client, base, _ = appli()
    reponse = client.post("/api/dossiers/d1/retours", json={"message": "Le PV D12 est mal daté.", "contexte": "Chronologie"})
    assert reponse.status_code == 201
    assert base.tables["retours"] == [{
        "dossier_id": "d1", "owner_id": UTILISATEUR, "message": "Le PV D12 est mal daté.",
        "contexte": "Chronologie", "statut_dossier": "erreur",
    }]


def test_signalement_sans_table_ne_plante_pas(appli) -> None:
    """Migration 0005 pas encore appliquée : l'avocat est prévenu."""
    client, _, _ = appli(avec_table_retours=False)
    reponse = client.post("/api/dossiers/d1/retours", json={"message": "Rien ne s'affiche."})
    assert reponse.status_code == 503 and "pas pu être enregistré" in reponse.json()["detail"]


def test_infos_publiques(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.delenv("EMAIL_CONTACT", raising=False)
    infos = TestClient(main.app).get("/api/infos").json()
    assert infos == {
        "fournisseur_ia": "Mistral AI", "limite_pages": main.LIMITE_PAGES,
        "limite_mo_par_fichier": main.LIMITE_MO_PAR_FICHIER, "email_contact": None,
    }


def test_serveur_garde_eveille_pendant_un_traitement(monkeypatch) -> None:
    """Render endort une instance gratuite après 15 min sans requête : le
    traitement s'appelle lui-même par son adresse publique."""
    import urllib.request

    from app import pipeline

    appels: list = []

    class _Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: appels.append(url) or _Reponse())
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    pipeline.garder_eveille()
    assert appels == [], "hors de Render, rien"
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://lytis.onrender.com/")
    pipeline.garder_eveille()
    assert appels == ["https://lytis.onrender.com/health"]
