"""Résumé de l'affaire : trois ou quatre phrases tirées des seuls faits,
actes et déclarations déjà vérifiés, chacune contrôlée contre ses propres
sources — sans jamais deviner qui est le client, inventer, mélanger deux
pièces ni qualifier."""

from __future__ import annotations

import json
import sqlite3

import pytest
from rich.console import Console

import depouille.resume as resume
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd.pdf', 1, 'x', 'x')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, 'PV d''audition', 1, 1)")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Lucas MARTINON', 'mis_en_cause'), (2, 'Odile SERMET', 'victime')")
    db.execute(
        "INSERT INTO evenements_faits (id, piece_id, page, citation, description, statut_verif) "
        "VALUES (1, 1, 1, 'Un pavillon a été cambriolé à Biviers.', 'Cambriolage d''un pavillon à Biviers le 12/02/2026', 'verifie'), "
        "(2, 1, 1, 'c', 'Fait non vérifié qui ne doit jamais apparaître', 'rejete'), "
        "(3, 1, 1, 'Une Skoda grise est relevée par la LAPI.', 'Véhicule Skoda grise relevé', 'verifie')"
    )
    db.execute(
        "INSERT INTO declarations (id, personne_id, piece_id, page, citation, point_factuel, statut_verif) "
        "VALUES (1, 1, 1, 1, 'J''ai attendu dans la voiture.', 'Reconnaît avoir attendu dans la voiture le 12/02', 'verifie'), "
        "(2, 2, 1, 1, 'c', 'La victime a quitté son domicile à 13h30', 'verifie')"
    )
    db.commit()
    return db


def _json(*phrases: tuple[str, list[str]]) -> str:
    return json.dumps({"phrases": [{"texte": t, "sources": s} for t, s in phrases]}, ensure_ascii=False)


BON = _json(
    ("Un cambriolage est commis à Biviers le 12/02/2026.", ["F1"]),
    ("Votre client, Lucas MARTINON, reconnaît avoir attendu dans la voiture.", ["D1"]),
)


def _modele(monkeypatch, *reponses: str) -> list[dict]:
    appels: list[dict] = []

    class Faux:
        def appeler(self, systeme, prompt, modele):
            appels.append({"systeme": systeme, "prompt": prompt})
            return ReponseLLM(texte=reponses[min(len(appels), len(reponses)) - 1], tokens_in=10, tokens_out=10)

    monkeypatch.setattr(resume, "obtenir_provider", lambda config: Faux())
    return appels


def _resume_en_base(db) -> str | None:
    ligne = db.execute("SELECT texte FROM resume_affaire WHERE id = 1").fetchone()
    return ligne["texte"] if ligne else None


def _generer(db) -> None:
    resume.generer_resume(db, Config(offline=False), Console(quiet=True))


def test_nourri_des_seuls_elements_verifies_avec_leur_citation(monkeypatch, db) -> None:
    appels = _modele(monkeypatch, BON)
    _generer(db)

    prompt = appels[0]["prompt"]
    assert "Reconnaît avoir attendu dans la voiture" in prompt, "les déclarations du mis en cause nourrissent le résumé"
    assert "« J'ai attendu dans la voiture. »" in prompt, "avec leur citation : le point factuel n'est souvent que le sujet"
    assert "La victime a quitté" not in prompt, "seules celles du mis en cause"
    assert "jamais apparaître" not in prompt, "jamais un fait non vérifié"
    assert "3 ou 4 phrases" in appels[0]["systeme"]
    assert len(appels) == 1, "un résumé entièrement retenu ne coûte qu'un appel"
    assert _resume_en_base(db) == (
        "Un cambriolage est commis à Biviers le 12/02/2026. "
        "Votre client, Lucas MARTINON, reconnaît avoir attendu dans la voiture."
    )


def test_votre_client_seulement_avec_un_seul_mis_en_cause(monkeypatch, db) -> None:
    appels = _modele(monkeypatch, BON)
    _generer(db)
    assert "« votre client, Lucas MARTINON »" in appels[0]["prompt"]


def test_plusieurs_mis_en_cause_jamais_votre_client(monkeypatch, db) -> None:
    """Avec des coauteurs, l'outil ne sait pas lequel l'avocat défend."""
    db.execute("INSERT INTO personnes (nom, role) VALUES ('Yannick FONTANEL', 'mis_en_cause')")
    db.commit()
    appels = _modele(monkeypatch, BON)
    _generer(db)
    assert "sans jamais écrire « votre client »" in appels[0]["prompt"]
    assert "désigne-la comme" not in appels[0]["prompt"]


def test_phrase_qui_qualifie_ecartee(monkeypatch, db) -> None:
    _modele(monkeypatch, _json(("La géolocalisation est entachée de nullité.", ["F1"])))
    _generer(db)
    assert _resume_en_base(db) is None


def test_pas_de_resume_hors_ligne(db) -> None:
    resume.generer_resume(db, Config(offline=True), Console(quiet=True))
    assert _resume_en_base(db) is None


def test_client_designe_par_l_avocat_meme_avec_coauteurs(monkeypatch, db) -> None:
    """Quand l'avocat a désigné son client, « votre client » s'applique à
    lui — même parmi plusieurs mis en cause — et ce sont SES déclarations
    qui nourrissent le résumé."""
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (3, 'Yannick FONTANEL', 'mis_en_cause')")
    db.execute(
        "INSERT INTO declarations (personne_id, piece_id, page, citation, point_factuel, statut_verif) "
        "VALUES (3, 1, 1, 'c', 'Refuse de donner aucun nom', 'verifie')"
    )
    db.execute("UPDATE personnes SET est_client = 1 WHERE nom = 'Lucas MARTINON'")
    db.commit()
    appels = _modele(monkeypatch, BON)
    _generer(db)
    assert "« votre client, Lucas MARTINON »" in appels[0]["prompt"]
    assert "Reconnaît avoir attendu" in appels[0]["prompt"]
    assert "Refuse de donner" not in appels[0]["prompt"], "pas les déclarations du coauteur"


def test_partie_civile_la_victime_peut_etre_le_client(monkeypatch, db) -> None:
    db.execute("UPDATE personnes SET est_client = 1 WHERE nom = 'Odile SERMET'")
    db.commit()
    appels = _modele(monkeypatch, BON)
    _generer(db)
    assert "« votre client, Odile SERMET »" in appels[0]["prompt"]
    assert "La victime a quitté" in appels[0]["prompt"]


def test_detail_prete_a_la_mauvaise_piece_ecarte(monkeypatch, db) -> None:
    """Observé sur Mistral Large : « le témoin déclare avoir vu une Clio
    blanche immatriculée GH-482-KL » — la plaque venait d'une autre pièce.
    Chaque phrase est contrôlée contre SES sources : la Skoda (F3) prêtée à
    la déclaration du client (D1) est écartée."""
    appels = _modele(monkeypatch, _json(
        ("Un cambriolage est commis à Biviers le 12/02/2026.", ["F1"]),
        ("Votre client, Lucas MARTINON, reconnaît avoir attendu dans la Skoda grise.", ["D1"]),
    ), BON)
    _generer(db)
    assert len(appels) == 2
    assert "Skoda" in appels[1]["prompt"] and "(sources citées : D1)" in appels[1]["prompt"], "le motif est donné au modèle"
    assert "Skoda" not in _resume_en_base(db)


def test_invention_corrigee_au_second_essai(monkeypatch, db) -> None:
    appels = _modele(monkeypatch, _json(
        ("Des violences sont reprochées à Lucas MARTINON vers 21h00.", ["F1"]),
    ), BON)
    _generer(db)
    assert len(appels) == 2
    assert "violences" in appels[1]["prompt"] and "21h00" in appels[1]["prompt"]
    assert _resume_en_base(db).startswith("Un cambriolage")


def test_le_meilleur_des_deux_essais_est_garde(monkeypatch, db) -> None:
    """Des phrases fidèles ne sont jamais perdues parce que le second essai
    fait moins bien."""
    appels = _modele(monkeypatch, _json(
        ("Un cambriolage est commis à Biviers le 12/02/2026.", ["F1"]),
        ("Lucas MARTINON est mis en examen.", ["D1"]),
    ), _json(("Entre février 2026 et février 2026, un cambriolage est commis.", ["F1"])))
    _generer(db)
    assert len(appels) == 2
    assert _resume_en_base(db) == "Un cambriolage est commis à Biviers le 12/02/2026."


def test_phrase_sans_source_ecartee(monkeypatch, db) -> None:
    _modele(monkeypatch, _json(("Un cambriolage est commis à Biviers.", [])), _json(("Un cambriolage est commis à Biviers.", ["F9"])))
    _generer(db)
    assert _resume_en_base(db) is None


def test_trop_long_reecrit_mais_jamais_perdu(monkeypatch, db) -> None:
    long = "Votre client, Lucas MARTINON, reconnaît avoir attendu " + "longuement " * 200 + "dans la voiture."
    appels = _modele(monkeypatch, _json((long, ["D1"])))
    _generer(db)
    assert len(appels) == 2 and "mots, pour 110 au plus" in appels[1]["prompt"]
    assert _resume_en_base(db) == long


def test_numero_d_element_recopie_retire(monkeypatch, db) -> None:
    _modele(monkeypatch, _json(("Un cambriolage est commis à Biviers le 12/02/2026 (F1).", ["F1"])))
    _generer(db)
    assert _resume_en_base(db) == "Un cambriolage est commis à Biviers le 12/02/2026."


def test_une_seule_version_d_une_discordance_reecrite(monkeypatch, db) -> None:
    """Observé : cinq résumés sur cinq disaient « interpellé à 07h50 » quand
    une autre pièce dit 08h05. La discordance est connue des règles fixes :
    la phrase qui n'en garde qu'une version est renvoyée au modèle."""
    monkeypatch.setattr(resume, "discordances_connues", lambda db: [("Cambriolage", ["12/02/2026", "13/02/2026"])])
    appels = _modele(monkeypatch, BON, _json(
        ("Un cambriolage est commis à Biviers le 12/02/2026 ou le 13/02/2026.", ["F1"]),
    ))
    db.execute("UPDATE evenements_faits SET description = description || ' ou le 13/02/2026' WHERE id = 1")
    db.commit()
    _generer(db)
    assert len(appels) == 2 and "donne chaque version" in appels[1]["prompt"]
    assert _resume_en_base(db) == "Un cambriolage est commis à Biviers le 12/02/2026 ou le 13/02/2026."
