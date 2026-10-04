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


def test_frise_triee_par_vraie_date_et_actes_sans_date_a_leur_place() -> None:
    """Trier « JJ/MM/AAAA » comme du texte plaçait le 15/03 avant le 16/02,
    et un acte sans date (« Audition close à 11h15 ») en tête de frise."""
    from depouille.chrono import trier_actes_procedure

    actes = [
        {"nature": "fin_audition", "date": None, "heure": "11h15", "page": 10, "piece_id": 9},
        {"nature": "fin_garde_a_vue", "date": "15/03/2031", "heure": "20h15", "page": 16, "piece_id": 14},
        {"nature": "debut_audition", "date": "14/03/2031", "heure": "10h00", "page": 9, "piece_id": 9},
        {"nature": "plainte", "date": "16/02/2031", "heure": "09h00", "page": 1, "piece_id": 1},
        {"nature": "interpellation", "date": "14/03/2031", "heure": "7h50", "page": 3, "piece_id": 3},
        {"nature": "sans_rien", "date": None, "heure": None, "page": 2, "piece_id": 2},
    ]
    ordre = [a["nature"] for a in trier_actes_procedure(actes)]
    assert ordre == ["plainte", "interpellation", "debut_audition", "fin_audition", "fin_garde_a_vue", "sans_rien"]
    assert actes[0]["date"] is None, "la date empruntée ne sert qu'à ranger, l'acte n'est pas modifié"
