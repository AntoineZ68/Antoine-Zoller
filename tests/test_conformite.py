"""Le module de conformité procédurale ne doit jamais qualifier
juridiquement un acte, et ne doit signaler que des faits structurels
vérifiables (pièce absente, seuil numérique non controversé dépassé)."""

from __future__ import annotations

import shutil
import sqlite3

from depouille.conformite import detecter_signalements

from .conftest import DossierTraite

MOTS_INTERDITS = ("nullité", "irrégulier", "irrégularité", "illégal", "invalide")


def _copie_db(dossier_traite: DossierTraite, tmp_path) -> sqlite3.Connection:
    dossier_traite.db.commit()
    copie = tmp_path / "copie.db"
    shutil.copy(dossier_traite.affaire_dir / "depouille.db", copie)
    db = sqlite3.connect(copie)
    db.row_factory = sqlite3.Row
    return db


def test_dossier_complet_ne_declenche_aucun_signalement(dossier_traite: DossierTraite) -> None:
    """Le jeu d'essai est un dossier "propre" : toutes les pièces attendues
    sont présentes, la durée de GAV (36h) est couverte par la prolongation.
    Aucun signalement ne doit se déclencher à tort."""
    assert detecter_signalements(dossier_traite.db) == []


def test_absence_prolongation_declenchee_si_gav_depasse_24h(dossier_traite: DossierTraite, tmp_path) -> None:
    db = _copie_db(dossier_traite, tmp_path)
    db.execute("DELETE FROM pieces WHERE type = 'PV de prolongation de garde à vue'")
    db.commit()

    signalements = detecter_signalements(db)
    titres = [s.titre for s in signalements]
    assert any("aucune prolongation identifiée" in t for t in titres)


def test_absence_entretien_avocat_declenchee(dossier_traite: DossierTraite, tmp_path) -> None:
    db = _copie_db(dossier_traite, tmp_path)
    db.execute("DELETE FROM pieces WHERE type = \"PV d'entretien avocat\"")
    db.commit()

    signalements = detecter_signalements(db)
    assert any("Entretien avec l'avocat non identifié" == s.titre for s in signalements)


def test_asymetrie_demande_sans_realisation(dossier_traite: DossierTraite, tmp_path) -> None:
    db = _copie_db(dossier_traite, tmp_path)
    db.execute("DELETE FROM evenements_procedure WHERE nature = 'realisation_examen_medical'")
    db.commit()

    signalements = detecter_signalements(db)
    assert any("Examen médical demandé mais non réalisé" in s.titre for s in signalements)


def test_aucun_signalement_ne_qualifie_juridiquement(dossier_traite: DossierTraite, tmp_path) -> None:
    """Régression garantie : quel que soit le signalement déclenché, le texte
    ne doit jamais employer un mot de qualification juridique. C'est la
    limite explicite entre "signaler un fait" et "rendre un avis"."""
    db = _copie_db(dossier_traite, tmp_path)
    db.execute("DELETE FROM pieces WHERE type = 'PV de prolongation de garde à vue'")
    db.execute("DELETE FROM pieces WHERE type = \"PV d'entretien avocat\"")
    db.execute("DELETE FROM evenements_procedure WHERE nature = 'notification_droits'")
    db.commit()

    signalements = detecter_signalements(db)
    assert len(signalements) >= 3
    for s in signalements:
        texte = f"{s.titre} {s.description}".lower()
        for mot in MOTS_INTERDITS:
            assert mot not in texte, f"mot de qualification juridique trouvé : {mot!r} dans {s.titre!r}"


def test_signalement_avec_source_porte_une_citation_reelle(dossier_traite: DossierTraite) -> None:
    """Quand un signalement s'appuie sur un événement précis (pas une simple
    absence), il doit porter une page et une citation qui existent
    réellement dans le dossier — la même exigence de traçabilité que le
    reste de l'outil."""
    db = dossier_traite.db
    for s in detecter_signalements(db):
        if s.page_reference is not None:
            page = db.execute("SELECT texte FROM pages WHERE numero_global = ?", (s.page_reference,)).fetchone()
            assert page is not None
            assert s.citation_reference.strip().lower() in " ".join(page["texte"].lower().split())
