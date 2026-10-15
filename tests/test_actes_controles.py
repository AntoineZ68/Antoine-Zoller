"""Actes lus par le modèle, vérifiés par le code (actes.py), et contrôles
de procédure appliqués par des règles fixes (controles.py). Cas repris des
deux dossiers de test fournis par l'utilisateur (gendarmerie, police)."""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from depouille import actes
from depouille.actes import fiche_verifiee
from depouille.config import Config
from depouille.conformite import detecter_signalements
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM

AUDITION = (
    "À 14h30, extrayons le mis en cause. L'interprète en langue roumaine n'étant pas disponible avant 18h00, "
    "et vu l'urgence des nécessités de l'enquête, nous décidons de procéder à l'audition en français."
)


def test_fiche_appuyee_par_sa_citation() -> None:
    fiche = fiche_verifiee({"nature": "debut_audition", "page": 18, "citation": "À 14h30, extrayons le mis en cause.",
                            "heure": "14h30", "interprete": "absent"}, None, AUDITION)
    assert fiche["heure"] == "14h30"
    assert fiche["details"] == {"interprete": "absent"}, "prouvé par la phrase qui suit la citation"


@pytest.mark.parametrize("fiche, raison", [
    ({"nature": "avis_parquet", "page": 1, "citation": "Poursuivons les investigations."}, "nature sans appui"),
    ({"nature": "realisation_examen_medical", "page": 20,
      "citation": "la mesure de garde à vue se poursuit sans examen médical."}, "réalisation à la négative"),
    ({"nature": "inventee", "page": 1, "citation": "x"}, "nature hors liste"),
])
def test_fiche_ecartee(fiche, raison) -> None:
    assert fiche_verifiee(fiche, None) is None, raison


def test_precisions_et_heure_non_appuyees_retirees() -> None:
    fiche = fiche_verifiee({"nature": "debut_audition", "page": 18, "citation": "À 14h30, extrayons le mis en cause.",
                            "heure": "15h30", "interprete": "absent", "agent": "OPJ", "date": "12/01/2026"},
                           None, "À 14h30, extrayons le mis en cause. Puis rien.")
    assert fiche["heure"] is None and fiche["details"] == {} and fiche["date"] is None


def test_agent_douteux_quand_un_magistrat_est_nomme() -> None:
    fiche = fiche_verifiee({"nature": "prolongation_garde_a_vue", "page": 2, "agent": "OPJ",
                            "citation": "La procureure autorise, à la demande de l'officier de police judiciaire, la prolongation."}, None)
    assert "agent" not in fiche["details"]


PIECES = [
    ("PV de notification de placement en garde à vue", "P.08 - PLACEMENT EN GARDE À VUE\nÀ 09h00, notifions à M. Youssef "
     "TARKAN son placement en garde à vue. Droits notifiés à 09h05. L'intéressé déclare : 'Je ne comprends pas bien le "
     "français, je veux un traducteur roumain et je veux voir un médecin car j'ai mal au dos.' Nous actons ces demandes."),
    ("PV de constatations", "P.12 - PROCÈS-VERBAL DE FOUILLE À CORPS INTÉGRALE\nMoi, Maréchal des Logis Chef, Agent de "
     "Police Judiciaire (APJ), je procède à une fouille à corps intégrale avec mise à nu de l'intéressé."),
    ("PV d'audition", "P.18 - PROCÈS-VERBAL D'AUDITION\n" + AUDITION),
    ("PV de prolongation de garde à vue", "P.25 - PROLONGATION DE GARDE À VUE\nLe 16 novembre à 08h30. Nous, Officier de "
     "Police Judiciaire, décidons de notre propre chef de prolonger la garde à vue de M. Youssef TARKAN. L'intéressé n'a "
     "pas été présenté au Procureur de la République."),
]
REPONSES = [
    {"actes": [
        {"nature": "placement_garde_a_vue", "page": 1, "heure": "09h00", "personne": "Youssef TARKAN",
         "citation": "À 09h00, notifions à M. Youssef TARKAN son placement en garde à vue."},
        {"nature": "notification_droits", "page": 1, "heure": "09h05", "citation": "Droits notifiés à 09h05."},
        {"nature": "demande_interprete", "page": 1,
         "citation": "Je ne comprends pas bien le français, je veux un traducteur roumain"},
    ]},
    {"actes": [{"nature": "fouille_integrale", "page": 2, "agent": "APJ",
                "citation": "Moi, Maréchal des Logis Chef, Agent de Police Judiciaire (APJ), je procède à une fouille à "
                            "corps intégrale avec mise à nu de l'intéressé."}]},
    {"actes": [{"nature": "debut_audition", "page": 3, "heure": "14h30", "interprete": "absent",
                "citation": "À 14h30, extrayons le mis en cause."}]},
    {"actes": [{"nature": "prolongation_garde_a_vue", "page": 4, "heure": "08h30", "agent": "OPJ", "presentation": "non",
                "citation": "Nous, Officier de Police Judiciaire, décidons de notre propre chef de prolonger la garde à "
                            "vue de M. Youssef TARKAN."}]},
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Youssef TARKAN', 'mis_en_cause')")
    for numero, (type_, texte) in enumerate(PIECES, start=1):
        db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
                   "VALUES (?, 'd.pdf', ?, ?, ?)", (numero, numero, texte, str(numero)))
        db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, personne_principale_id) VALUES (?, ?, ?, ?, 1)",
                   (numero, type_, numero, numero))
    db.commit()

    class Faux:
        def appeler(self, systeme, prompt, modele):
            numero = int(prompt.split("[page ")[1].split("]")[0])
            return ReponseLLM(texte=json.dumps(REPONSES[numero - 1], ensure_ascii=False), tokens_in=1, tokens_out=1)

    monkeypatch.setattr(actes, "obtenir_provider", lambda config: Faux())
    pieces = db.execute("SELECT * FROM pieces").fetchall()
    actes.extraire_actes(db, Config(offline=False), pieces, Console(quiet=True), {"tokens_in": 0, "tokens_out": 0})
    db.execute("UPDATE evenements_procedure SET statut_verif = 'verifie'")
    db.commit()
    return db


def test_controles_sur_le_dossier_gendarmerie(db) -> None:
    titres = [s.titre for s in detecter_signalements(db)]
    for attendu in (
        "Audition menée sans interprète",
        "Interprète demandé — aucun interprète mentionné à la notification des droits",
        "Fouille intégrale décidée par un agent de police judiciaire",
        "Prolongation de garde à vue décidée par un enquêteur",
        "Prolongation sans présentation au magistrat",
        "Avis au procureur non identifié",
    ):
        assert attendu in titres, (attendu, titres)


def test_mineur_signale(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Lucas MARTIN', 'mis_en_cause')")
    texte = "Le 10/03/2031 à 08h00, notifions à Lucas MARTIN, né le 02/05/2014 à Lyon, son placement en garde à vue."
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd', 1, ?, 'x')", (texte,))
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, personne_principale_id) "
               "VALUES (1, 'PV de notification de placement en garde à vue', 1, 1, 1)")
    db.execute("INSERT INTO evenements_procedure (piece_id, nature, date, heure, personne_id, page, citation, statut_verif) "
               "VALUES (1, 'placement_garde_a_vue', '10/03/2031', '08h00', 1, 1, ?, 'verifie')", (texte,))
    db.commit()
    assert any(s.titre == "Personne mineure lors de la garde à vue" for s in detecter_signalements(db))


def test_perquisition_preliminaire_sans_assentiment(tmp_path) -> None:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO evenements_procedure (nature, date, heure, page, citation, details_json, statut_verif) "
               "VALUES ('debut_perquisition', '10/03/2031', '09h00', 3, 'x', ?, 'verifie')",
               (json.dumps({"cadre": "preliminaire", "assentiment": "non"}),))
    db.execute("INSERT INTO evenements_procedure (nature, date, heure, page, citation, details_json, statut_verif) "
               "VALUES ('debut_perquisition', '11/03/2031', '09h00', 4, 'y', ?, 'verifie')",
               (json.dumps({"cadre": "flagrance", "assentiment": "non"}),))
    db.commit()
    titres = [s for s in detecter_signalements(db) if "préliminaire" in s.titre]
    assert len(titres) == 1 and titres[0].page_reference == 3, "en flagrance, l'assentiment n'est pas requis"
