"""Contradictions entre pièces : ce qui ne concorde pas d'une pièce à
l'autre est montré, sourcé, jamais qualifié — et rien n'est inventé."""

from __future__ import annotations

import json
import sqlite3

import pytest
from rich.console import Console

import depouille.contradictions as module
from depouille.config import Config
from depouille.contradictions import (
    _un_caractere_d_ecart,
    contradictions_par_regles,
    generer_contradictions,
    proposition_retenue,
    toutes_les_contradictions,
)
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return ouvrir_db(tmp_path / "test.db")


def _page(db, numero: int, texte: str, ocr: int = 0) -> None:
    db.execute(
        "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, ocr_applique, empreinte_sha256) "
        "VALUES (?, 'dossier.pdf', ?, ?, ?, 'x')",
        (numero, numero, texte, ocr),
    )


def _acte(db, piece_id, nature, date, heure, page, citation, personne_id=1) -> None:
    db.execute(
        "INSERT INTO evenements_procedure (piece_id, date, heure, nature, personne_id, page, citation, statut_verif) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'verifie')",
        (piece_id, date, heure, nature, personne_id, page, citation),
    )


def _deux_pieces(db) -> None:
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'FONTANEL Yannick', 'mis_en_cause')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, cote) VALUES (1, \"PV d'interpellation\", 1, 1, 'D3')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin, cote) VALUES (2, 'Rapport de synthèse', 2, 2, 'D40')")


# --- Règles fixes : actes datés différemment ---


def test_meme_acte_deux_heures_selon_les_pieces(db) -> None:
    _deux_pieces(db)
    _acte(db, 1, "interpellation", "10/03/2026", "06h05", 1, "interpellé à 06h05")
    _acte(db, 2, "interpellation", "10/03/2026", "06h30", 2, "interpellé vers 06h30")
    db.commit()

    [c] = contradictions_par_regles(db)
    assert c.domaine == "procedure"
    assert c.titre == "Interpellation de FONTANEL Yannick : 06h05 ou 06h30"
    assert "PV d'interpellation (D3) : 06h05" in c.description and "à vérifier" in c.description.lower()
    assert [s["page"] for s in c.sources] == [1, 2]


def test_date_differente_affichee_quand_elle_change(db) -> None:
    _deux_pieces(db)
    _acte(db, 1, "notification_droits", "11/03/2026", "06h00", 1, "notifiée à 06h00")
    _acte(db, 2, "notification_droits", "12/03/2026", "06h00", 2, "notification le 12/03 à 06h00")
    db.commit()
    [c] = contradictions_par_regles(db)
    assert c.titre.endswith(": 11/03 à 06h00 ou 12/03 à 06h00")


def test_heures_concordantes_ou_meme_piece_rien_a_signaler(db) -> None:
    _deux_pieces(db)
    _acte(db, 1, "interpellation", "10/03/2026", "06h05", 1, "interpellé à 06h05")
    _acte(db, 2, "interpellation", None, "06h05", 2, "à 06h05")  # date absente : pas une discordance
    # Deux mentions différentes dans la MÊME pièce : rapprochement interne, pas entre pièces.
    _acte(db, 1, "placement_garde_a_vue", "10/03/2026", "06h10", 1, "placé à 06h10")
    _acte(db, 1, "placement_garde_a_vue", "10/03/2026", "06h15", 1, "placement 06h15")
    # Plusieurs prolongations sont normales.
    _acte(db, 1, "prolongation_garde_a_vue", "11/03/2026", "06h00", 1, "prolongée")
    _acte(db, 2, "prolongation_garde_a_vue", "12/03/2026", "06h00", 2, "prolongée à nouveau")
    db.commit()
    assert contradictions_par_regles(db) == []


def test_personnes_differentes_jamais_comparees(db) -> None:
    _deux_pieces(db)
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (2, 'MARTINON Lucas', 'mis_en_cause')")
    _acte(db, 1, "interpellation", "10/03/2026", "06h05", 1, "interpellé à 06h05", personne_id=1)
    _acte(db, 2, "interpellation", "10/03/2026", "06h30", 2, "interpellé à 06h30", personne_id=2)
    db.commit()
    assert contradictions_par_regles(db) == []


# --- Règles fixes : identifiants presque identiques ---


def test_un_caractere_d_ecart() -> None:
    assert _un_caractere_d_ecart("ZV553RT", "ZV535RT"), "inversion de deux voisins"
    assert _un_caractere_d_ecart("0612345678", "0612345679")
    assert not _un_caractere_d_ecart("ZV553RT", "ZV553RT")
    assert not _un_caractere_d_ecart("ZV553RT", "AB123CD")
    assert not _un_caractere_d_ecart("ZV553RT", "ZV355RT"), "deux caractères non voisins"


def test_plaque_ecrite_differemment_dans_deux_pv(db) -> None:
    _page(db, 10, "Véhicule immatriculé ZV-553-RT, utilisé par FONTANEL Yannick.")
    _page(db, 11, "Le même véhicule ZV-553-RT est garé rue des Alpes.")
    _page(db, 12, "Véhicule Skoda Octavia gris immatriculé ZV-535-RT, moteur coupé.", ocr=1)
    db.commit()

    [c] = contradictions_par_regles(db)
    assert c.domaine == "fond"
    assert c.titre == "Plaque d'immatriculation : ZV-535-RT ou ZV-553-RT"
    assert "pages 10, 11" in c.description and "page 12" in c.description
    assert "numérisée" in c.description, "une erreur de lecture du scan est possible : le dire"
    assert {s["page"] for s in c.sources} == {10, 12}


def test_numeros_voisins_listes_sur_une_meme_page_ignores(db) -> None:
    _page(db, 1, "Lignes de la famille : 06 12 34 56 78 et 06 12 34 56 79.")
    _page(db, 2, "Appel depuis le 06 12 34 56 78.")
    _page(db, 3, "Appel depuis le 06 12 34 56 79.")
    db.commit()
    assert contradictions_par_regles(db) == []


# --- Propositions du modèle : garde-fous ---


ELEMENTS = {
    "D1": {"page": 4, "citation": "un break de couleur blanche", "libelle": "COLLOMB Nadège",
           "ligne": "COLLOMB Nadège déclare : véhicule blanc — « un break de couleur blanche »"},
    "F2": {"page": 12, "citation": "Skoda Octavia gris métallisé", "libelle": "",
           "ligne": "Véhicule de FONTANEL relevé par la LAPI — « Skoda Octavia gris métallisé »"},
    "F3": {"page": 12, "citation": "moteur coupé", "libelle": "", "ligne": "Moteur coupé — « moteur coupé »"},
}


def _proposition(**champs) -> dict:
    base = {
        "titre": "Couleur du véhicule : blanc selon COLLOMB, gris selon la LAPI",
        "description": "COLLOMB décrit un break de couleur blanche ; la LAPI relève une Skoda Octavia gris métallisé.",
        "domaine": "fond",
        "sources": ["D1", "F2"],
    }
    return {**base, **champs}


def test_proposition_conforme_retenue() -> None:
    c = proposition_retenue(_proposition(), ELEMENTS)
    assert c is not None and c.origine == "modele"
    assert [s["page"] for s in c.sources] == [4, 12]
    assert c.sources[0]["libelle"] == "COLLOMB Nadège"


@pytest.mark.parametrize("modification, raison", [
    ({"sources": ["D1"]}, "une seule source"),
    ({"sources": ["D1", "X9"]}, "source inexistante"),
    ({"sources": ["F2", "F3"]}, "deux éléments de la même page"),
    ({"description": "Le témoin COLLOMB ment sur la couleur du véhicule."}, "jugement de sincérité"),
    ({"description": "Cette discordance rend le PV de surveillance irrégulier."}, "qualification juridique"),
    ({"titre": "Couleur du véhicule le 12/02 : blanc ou gris"}, "date absente des sources"),
    ({"description": "COLLOMB, voisine de DURAND, décrit un break blanc."}, "nom absent des sources"),
])
def test_proposition_ecartee(modification, raison) -> None:
    assert proposition_retenue(_proposition(**modification), ELEMENTS) is None, raison


def _dossier_pour_le_modele(db) -> None:
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'COLLOMB Nadège', 'temoin')")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, \"PV d'audition\", 4, 4)")
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (2, 'PV de surveillance', 12, 12)")
    db.execute(
        "INSERT INTO declarations (id, personne_id, piece_id, page, citation, point_factuel, statut_verif) "
        "VALUES (1, 1, 1, 4, 'un break de couleur blanche', 'véhicule blanc', 'verifie')"
    )
    db.execute(
        "INSERT INTO evenements_faits (id, piece_id, page, citation, description, statut_verif) "
        "VALUES (2, 2, 12, 'Skoda Octavia gris métallisé', 'Véhicule relevé par la LAPI', 'verifie')"
    )
    db.commit()


def test_generation_stocke_seulement_les_propositions_retenues(monkeypatch, db) -> None:
    _dossier_pour_le_modele(db)
    reponse = {"contradictions": [
        {"titre": "Couleur du véhicule : blanc ou gris", "description": "Un break de couleur blanche ; une Skoda Octavia gris métallisé.",
         "domaine": "fond", "sources": ["D1", "F2"]},
        {"titre": "Le témoin ment", "description": "COLLOMB ment.", "domaine": "fond", "sources": ["D1", "F2"]},
    ]}

    class Provider:
        def appeler(self, systeme, prompt, modele):
            assert "[D1]" in prompt and "[F2]" in prompt
            return ReponseLLM(texte=json.dumps(reponse), tokens_in=1, tokens_out=1)

    monkeypatch.setattr(module, "obtenir_provider", lambda config: Provider())
    assert generer_contradictions(db, Config(offline=False), Console(quiet=True)) == 1
    [c] = toutes_les_contradictions(db)
    assert c.titre == "Couleur du véhicule : blanc ou gris" and c.origine == "modele"


def test_hors_ligne_aucun_appel(db) -> None:
    _dossier_pour_le_modele(db)
    assert generer_contradictions(db, Config(offline=True), Console(quiet=True)) == 0


def test_dossier_traite_avant_cette_fonction_regles_seules(db) -> None:
    db.execute("DROP TABLE contradictions")
    _page(db, 10, "immatriculé ZV-553-RT")
    _page(db, 12, "immatriculé ZV-535-RT")
    db.commit()
    assert [c.origine for c in toutes_les_contradictions(db)] == ["regle"]


def test_discordance_trouvee_par_les_regles_et_le_modele_affichee_une_fois(db) -> None:
    _deux_pieces(db)
    _acte(db, 1, "interpellation", "10/03/2026", "06h05", 1, "interpellé à 06h05")
    _acte(db, 2, "interpellation", "10/03/2026", "06h30", 2, "interpellé vers 06h30")
    sources = [{"libelle": "", "page": 1, "citation": "interpellé à 06h05"}, {"libelle": "", "page": 2, "citation": "interpellé vers 06h30"}]
    db.execute(
        "INSERT INTO contradictions (ordre, domaine, titre, description, sources_json) VALUES (1, 'procedure', 'Heure d''interpellation', 'd', ?)",
        (json.dumps(sources),),
    )
    db.commit()
    assert [c.origine for c in toutes_les_contradictions(db)] == ["regle"]
