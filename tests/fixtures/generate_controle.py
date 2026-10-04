"""Dossier de contrôle — entièrement fictif — dont on connaît d'avance ce
que l'outil doit trouver. Sert au banc d'essai IA (scripts/banc_essai_ia.py)
et aux vérifications de bout en bout.

Par rapport au jeu d'essai de base (generate_fixture.py) :
- une cote numérique apposée en tête de CHAQUE page, scans compris, comme
  le fait le logiciel du greffe ;
- trois discordances connues :
  1. l'interpellation à 07h50 selon le PV de placement, 08h05 selon le PV
     d'interpellation (règle fixe) ;
  2. la plaque GH-482-KL (PV d'interpellation, scanné) et GH-428-KL (PV de
     surveillance) (règle fixe) ;
  3. le véhicule « de couleur blanche » selon un témoin, « de couleur
     grise » selon le PV de surveillance (seul le modèle peut la relever).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from . import generate_fixture as base
from .dossiers_complexes import QuestionAttendue, VeriteDossier


def _verite(chemin: Path, nb_pages: int, pages_scan: list[int]) -> VeriteDossier:
    return VeriteDossier(
        chemin_pdf=chemin, nb_pages=nb_pages, pages_scan=pages_scan,
        contradictions_par_regles=(
            ("Interpellation de Julien MORVANNEC", "07h50", "08h05"),
            ("Plaque d'immatriculation", "GH-428-KL", "GH-482-KL"),
        ),
        # Couleur du véhicule : seul le modèle peut la relever.
        contradictions_modele=(("couleur", "blanc", "gris"),),
        durees_gav={"Julien MORVANNEC": 36 * 60},
        mis_en_cause=("Julien MORVANNEC",),
        # Le procureur qui autorise la prolongation n'est jamais en cause.
        jamais_mis_en_cause=("Antoine ROQUIER",),
        questions=(
            # Les deux heures d'interpellation : une réponse qui n'en donne
            # qu'une cache à l'avocat une discordance du dossier.
            QuestionAttendue("À quelle heure Julien MORVANNEC a-t-il été interpellé ?", heures=((7, 50), (8, 5))),
            QuestionAttendue("Quel véhicule a été vu devant le domicile, et de quelle couleur ?", mots=("grise", "blanche")),
            QuestionAttendue("Que déclare Julien MORVANNEC sur son emploi du temps ?", mots=("22", "23")),
        ),
    )


PIECES_AJOUTEES = [
    (3, {
        "type": "PV d'interpellation", "scan": True,
        "pages": [[
            "## PROCÈS-VERBAL D'INTERPELLATION",
            "Le 14/03/2031 à 08h05, nous, adjudant Marc TESSIER, agent de police judiciaire, "
            "procédons à l'interpellation de Julien MORVANNEC au 14 rue des Tanneurs à Vaucressin.",
            "L'intéressé quitte son domicile à bord du véhicule Renault Clio immatriculé GH-482-KL.",
        ]],
    }),
    (4, {
        "type": "PV de surveillance", "scan": False,
        "pages": [[
            "## PROCÈS-VERBAL DE SURVEILLANCE",
            "Le 13/03/2031 à 21h10, nous constatons la présence du véhicule Renault Clio immatriculé "
            "GH-428-KL stationné devant le 14 rue des Tanneurs à Vaucressin.",
            "Le véhicule est de couleur grise. Aucun occupant n'est visible.",
        ]],
    }),
    (5, {
        "type": "PV d'audition de témoin", "scan": False,
        "pages": [[
            "## PROCÈS-VERBAL D'AUDITION DE TÉMOIN",
            "Le 14/03/2031 à 15h00, nous entendons Paul LEROUX, voisin, demeurant 16 rue des "
            "Tanneurs à Vaucressin.",
            "Paul LEROUX déclare avoir vu, le 13/03/2031 vers 21h10, une Renault Clio de couleur "
            "blanche garée devant le 14 rue des Tanneurs, et un homme en descendre.",
            "Il précise qu'il faisait nuit mais que le réverbère éclairait la rue.",
        ]],
    }),
]


def generer_dossier_controle(dossier_sortie: Path) -> VeriteDossier:
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    origine = base._pieces

    def pieces() -> list[dict]:
        liste = origine()
        for position, piece in PIECES_AJOUTEES:
            liste.insert(position, piece)
        return liste

    base._pieces = pieces
    try:
        verite = base.generer_dossier_fictif(dossier_sortie)
    finally:
        base._pieces = origine

    chemin = dossier_sortie / "dossier_controle.pdf"
    with pymupdf.open(dossier_sortie / "dossier_fictif.pdf") as doc:
        for i, page in enumerate(doc):
            page.insert_text((500, 24), f"D{i + 1}", fontsize=9)
        doc.save(chemin)
    return _verite(chemin, verite.nb_pages, verite.pages_scan)
