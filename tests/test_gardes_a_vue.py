"""Gardes à vue personne par personne : un délai n'apparie jamais l'acte
d'une personne avec celui d'une autre."""

from __future__ import annotations

import sqlite3

import pytest

from depouille.conformite import detecter_signalements
from depouille.db import ouvrir_db
from depouille.gardes_a_vue import formater_duree, gardes_a_vue


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'MARTINON Lucas', 'mis_en_cause'), (2, 'FONTANEL Yannick', 'mis_en_cause')")
    db.commit()
    return db


def _acte(db, nature, date, heure, personne, page=1):
    db.execute(
        "INSERT INTO evenements_procedure (nature, date, heure, personne_id, page, citation, statut_verif) "
        "VALUES (?, ?, ?, ?, ?, ?, 'verifie')",
        (nature, date, heure, personne, page, f"{nature} {heure}"),
    )
    db.commit()


def test_deux_gardes_a_vue_jamais_melangees(db) -> None:
    """Avant : le premier placement du dossier (FONTANEL, 05h15) était
    apparié avec la première notification (MARTINON, 09h30) — 4 h 15 de
    délai pour FONTANEL, faux."""
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h15", 2)
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h38", 1)
    _acte(db, "notification_droits", "10/03/2026", "09h30", 1)
    _acte(db, "notification_droits", "10/03/2026", "08h55", 2)
    _acte(db, "fin_garde_a_vue", "11/03/2026", "05h20", 1)

    par_nom = {g["nom"]: g for g in gardes_a_vue(db)}
    assert par_nom["FONTANEL Yannick"]["delai_notification_minutes"] == 220   # 05h15 -> 08h55
    assert par_nom["MARTINON Lucas"]["delai_notification_minutes"] == 232     # 05h38 -> 09h30
    assert par_nom["MARTINON Lucas"]["duree_minutes"] == 23 * 60 + 42
    assert par_nom["FONTANEL Yannick"]["duree_minutes"] is None, "sa fin n'est pas celle de MARTINON"


def test_acte_sans_personne_rattache_seulement_si_une_seule_garde_a_vue(db) -> None:
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h38", 1)
    _acte(db, "notification_droits", "10/03/2026", "09h30", None)
    (seule,) = gardes_a_vue(db)
    assert seule["delai_notification_minutes"] == 232
    assert seule["actes_sans_personne_rattaches"]

    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h15", 2)
    for g in gardes_a_vue(db):
        assert g["delai_notification_minutes"] is None, "avec deux gardes à vue, l'acte orphelin n'est à personne"


def test_client_designe_signale(db) -> None:
    db.execute("UPDATE personnes SET est_client = 1 WHERE id = 1")
    db.commit()
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h38", 1)
    assert gardes_a_vue(db)[0]["est_client"] is True


def test_actes_hors_de_la_mesure_non_montres(db) -> None:
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h38", 1)
    _acte(db, "fin_garde_a_vue", "11/03/2026", "05h20", 1)
    _acte(db, "debut_audition", "20/03/2026", "10h00", 1)   # audition libre dix jours plus tard
    (g,) = gardes_a_vue(db)
    assert "debut_audition" not in {e["nature"] for e in g["evenements"]}


def test_points_de_controle_par_personne(db) -> None:
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h15", 2)
    _acte(db, "placement_garde_a_vue", "10/03/2026", "05h38", 1)
    _acte(db, "notification_droits", "10/03/2026", "09h30", 1)
    titres = [s.titre for s in detecter_signalements(db)]
    assert "Notification des droits non identifiée — FONTANEL Yannick" in titres
    assert not any(t.startswith("Notification des droits non identifiée — MARTINON") for t in titres)


@pytest.mark.parametrize("minutes, attendu", [(None, "NON TROUVÉ"), (52, "52 min"), (1422, "23 h 42"), (60, "1 h 00")])
def test_format_duree(minutes, attendu) -> None:
    assert formater_duree(minutes) == attendu
