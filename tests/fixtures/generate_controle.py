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

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from . import generate_fixture as base


@dataclass
class VeriteControle:
    chemin_pdf: Path
    nb_pages: int
    pages_scan: list[int]
    contradictions_par_regles: tuple[str, ...] = (
        "Interpellation de Julien MORVANNEC : 07h50 ou 08h05",
        "Plaque d'immatriculation : GH-428-KL ou GH-482-KL",
    )
    # Mots dont l'un au moins doit figurer dans la contradiction relevée par
    # le modèle sur la couleur du véhicule.
    contradiction_modele_mots: tuple[str, ...] = ("couleur", "blanc", "gris")
    duree_gav_minutes: int = 36 * 60
    mis_en_cause: str = "Julien MORVANNEC"
    # Le procureur qui autorise la prolongation n'est jamais en cause.
    jamais_mis_en_cause: tuple[str, ...] = ("Antoine ROQUIER",)
    # Les deux heures d'interpellation : une réponse qui n'en donne qu'une
    # cache à l'avocat une discordance du dossier.
    question_interpellation: str = "À quelle heure Julien MORVANNEC a-t-il été interpellé ?"
    heures_interpellation: tuple[tuple[int, int], ...] = ((7, 50), (8, 5))


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


def generer_dossier_controle(dossier_sortie: Path) -> VeriteControle:
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
    return VeriteControle(chemin_pdf=chemin, nb_pages=verite.nb_pages, pages_scan=verite.pages_scan)
