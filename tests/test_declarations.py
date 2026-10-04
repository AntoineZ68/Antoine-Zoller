from __future__ import annotations

from .conftest import DossierTraite


def test_declarant_correctement_attribue_pas_le_premier_nom_croise(dossier_traite: DossierTraite) -> None:
    """Régression : l'audition de Julien MORVANNEC cite Camille ARZANO dans
    une question ("Connaissiez-vous Camille ARZANO auparavant ?"). Le
    déclarant de cette pièce doit rester Julien MORVANNEC, pas la personne
    citée en passant."""
    db = dossier_traite.db
    lignes = db.execute(
        """SELECT p.nom FROM declarations d JOIN personnes p ON p.id = d.personne_id
           WHERE d.page = 8 AND d.statut_verif = 'verifie'"""
    ).fetchall()
    assert lignes, "aucune déclaration vérifiée trouvée page 8"
    assert all(l["nom"] == "Julien MORVANNEC" for l in lignes)


def test_divergence_detectee_sur_heure_arrivee(dossier_traite: DossierTraite) -> None:
    db = dossier_traite.db
    verite = dossier_traite.verite

    divergences = db.execute(
        """SELECT dv.*, p.nom FROM divergences dv LEFT JOIN personnes p ON p.id = dv.personne_id
           WHERE dv.point_factuel = ?""",
        (verite.point_factuel_divergent,),
    ).fetchall()
    assert len(divergences) == 1
    assert divergences[0]["nom"] == verite.mis_en_cause

    dv = divergences[0]
    da = db.execute("SELECT * FROM declarations WHERE id = ?", (dv["declaration_id_a"],)).fetchone()
    db_ = db.execute("SELECT * FROM declarations WHERE id = ?", (dv["declaration_id_b"],)).fetchone()
    citations = {da["citation"], db_["citation"]}
    assert citations == {verite.citation_audition_1, verite.citation_audition_2}


def test_divergence_ne_qualifie_jamais(dossier_traite: DossierTraite) -> None:
    """L'outil ne doit jamais écrire de mot de qualification ("contradiction",
    "mensonge", "faux") dans les données de divergence — seulement présenter
    les deux citations côte à côte."""
    mots_interdits = ("contradiction", "mensonge", "faux", "menti")
    for row in dossier_traite.db.execute("SELECT point_factuel FROM divergences"):
        texte = row["point_factuel"].lower()
        assert not any(mot in texte for mot in mots_interdits)


def test_pas_de_divergence_entre_personnes_differentes(dossier_traite: DossierTraite) -> None:
    """Une divergence ne doit jamais rapprocher les déclarations de deux
    personnes différentes — seulement les auditions d'une même personne."""
    db = dossier_traite.db
    for row in db.execute("SELECT * FROM divergences"):
        da = db.execute("SELECT personne_id FROM declarations WHERE id = ?", (row["declaration_id_a"],)).fetchone()
        db_ = db.execute("SELECT personne_id FROM declarations WHERE id = ?", (row["declaration_id_b"],)).fetchone()
        assert da["personne_id"] == db_["personne_id"] == row["personne_id"]


def test_confrontation_chaque_reponse_rattachee_a_son_locuteur(tmp_path) -> None:
    """Une confrontation fait répondre deux personnes tour à tour : chaque
    réponse va à celle qui la donne (observé : pièce « Non identifié »,
    déclarations perdues, sur un dossier d'essai de 46 pages)."""
    from rich.console import Console

    from depouille.config import Config
    from depouille.db import ouvrir_db
    from depouille.declarations import lancer_declarations

    texte = (
        "PROCÈS-VERBAL DE CONFRONTATION\n"
        "Question à Yanis BOUCHARD : Maintenez-vous que Karim TALBI organisait les chargements ?\n"
        "Réponse de Yanis BOUCHARD : Oui. C'est lui qui avait la clé.\n"
        "Question à Karim TALBI : Qu'avez-vous à répondre ?\n"
        "Réponse de Karim TALBI : Il ment pour se protéger.\n"
        "Question à Paul INCONNU : Et vous ?\n"
        "Réponse de Paul INCONNU : Rien à dire.\n"
    )
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (1, 'd.pdf', 1, ?, 'x')", (texte,))
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, 'PV de confrontation', 1, 1)")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Karim TALBI', 'mis_en_cause'), (2, 'Yanis BOUCHARD', 'mis_en_cause')")
    db.commit()

    lancer_declarations(db, Config(offline=True), force=False, console=Console(quiet=True))

    lignes = db.execute(
        "SELECT pe.nom, d.citation, d.statut_verif FROM declarations d JOIN personnes pe ON pe.id = d.personne_id ORDER BY d.id"
    ).fetchall()
    assert [(r[0], r[1]) for r in lignes] == [
        ("Yanis BOUCHARD", "Oui. C'est lui qui avait la clé."),
        ("Karim TALBI", "Il ment pour se protéger."),
    ], "une personne non identifiée au dossier n'est jamais créée"
    assert all(r[2] == "verifie" for r in lignes)
