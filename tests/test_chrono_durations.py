from __future__ import annotations

from depouille.chrono import calculer_durees

from .conftest import DossierTraite


def test_duree_totale_garde_a_vue(dossier_traite: DossierTraite) -> None:
    durees = calculer_durees(dossier_traite.db)
    attendu = f"{dossier_traite.verite.duree_gav_heures:.1f} h"
    assert durees["duree_totale_garde_a_vue"] == attendu


def test_delai_placement_notification_droits(dossier_traite: DossierTraite) -> None:
    durees = calculer_durees(dossier_traite.db)
    attendu = f"{dossier_traite.verite.delai_placement_notification_minutes:.0f} min"
    assert durees["delai_placement_notification_droits"] == attendu


def test_delai_examen_medical(dossier_traite: DossierTraite) -> None:
    durees = calculer_durees(dossier_traite.db)
    attendu = f"{dossier_traite.verite.delai_demande_realisation_medecin_minutes:.0f} min"
    assert durees["delai_demande_realisation_examen_medical"] == attendu


def test_duree_non_trouvee_si_evenement_manquant(dossier_traite: DossierTraite) -> None:
    """Si l'un des deux jalons manque (ici : on simule son absence en le
    dépubliant), le délai doit sortir NON TROUVÉ — jamais une estimation
    calculée à partir de données partielles."""
    db = dossier_traite.db
    db.execute("BEGIN")
    try:
        db.execute("UPDATE evenements_procedure SET statut_verif = 'rejete' WHERE nature = 'fin_garde_a_vue'")
        durees = calculer_durees(db)
        assert durees["duree_totale_garde_a_vue"] == "NON TROUVÉ"
    finally:
        db.rollback()


def test_tous_les_evenements_de_duree_ont_ete_verifies(dossier_traite: DossierTraite) -> None:
    """Les durées affichées ne doivent reposer que sur des événements dont
    la citation source a été vérifiée sur la page annoncée."""
    natures_attendues = {
        "placement_garde_a_vue",
        "fin_garde_a_vue",
        "notification_droits",
        "demande_examen_medical",
        "realisation_examen_medical",
    }
    for nature in natures_attendues:
        row = dossier_traite.db.execute(
            "SELECT statut_verif FROM evenements_procedure WHERE nature = ?", (nature,)
        ).fetchone()
        assert row is not None, f"événement {nature} attendu introuvable"
        assert row["statut_verif"] == "verifie"
