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
