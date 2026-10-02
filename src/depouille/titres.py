"""Intitulés d'index : « Réquisition ORBIS (ligne 14 52) et réponse » plutôt
que « Réquisition ». Un classeur papier se feuillette par sa table : le
type seul ne permet pas de distinguer trois réquisitions ou quatre PV
d'audition.

Le modèle propose ; une règle déterministe dispose. Ce qu'un intitulé peut
inventer de dangereux, ce sont les éléments qui identifient : un nom, un
opérateur, un numéro de ligne, une date. Chaque mot en capitales, chaque
mot à majuscule initiale (hors premier mot) et chaque nombre de
l'intitulé doit donc figurer dans le texte de la pièce. Un seul manque, et
l'intitulé est écarté : l'index retombe sur le type de la pièce, exact à
défaut d'être précis."""

from __future__ import annotations

import sqlite3

from rich.console import Console

from .config import Config
from .garde_fous import contient_qualification, elements_absents
from .llm import ErreurModeOffline, extraire_json, obtenir_provider

PIECES_PAR_APPEL = 8
EXTRAIT_CARACTERES = 900
LONGUEUR_MAX_TITRE = 90

PROMPT_TITRES = (
    "Tu rédiges l'intitulé de chaque pièce d'un dossier pénal français, tel qu'il "
    "figurerait dans la table d'un classeur d'avocat. Un intitulé court (90 caractères "
    "au plus) : la nature de l'acte, puis ce qui le distingue des autres actes de même "
    "nature — la personne entendue ou visée, l'opérateur, la ligne, le lieu, l'objet. "
    "Exemples de forme : « Procès-verbal de plainte — SERMET Odile », « Réquisition "
    "ORBIS (ligne 14 52) et réponse », « Ordonnance autorisant la géolocalisation », "
    "« Audition de témoin — CHABERT René ». "
    "N'utilise QUE des noms, nombres et lieux écrits dans l'extrait de la pièce, "
    "orthographiés exactement comme dans l'extrait. Aucune appréciation, aucune "
    "qualification juridique. Si l'extrait ne permet pas mieux que sa nature, donne "
    "simplement sa nature. "
    'Réponds uniquement en JSON : {"titres": [{"id": N, "titre": "..."}]}.'
)

def intitule_verifie(titre: str, texte_piece: str) -> bool:
    """Vrai si chaque élément identifiant de l'intitulé figure dans la pièce."""
    if not titre or len(titre) > LONGUEUR_MAX_TITRE or contient_qualification(titre):
        return False
    return not elements_absents(titre, texte_piece)


def titrer_pieces(db: sqlite3.Connection, config: Config, console: Console, compteur: dict[str, int]) -> int:
    """Renseigne `pieces.titre` pour les pièces dont l'intitulé proposé est
    vérifié. Renvoie le nombre d'intitulés retenus."""
    if config.offline:
        return 0
    pieces = db.execute("SELECT id, type, page_debut, page_fin FROM pieces ORDER BY page_debut").fetchall()
    textes = {}
    for p in pieces:
        pages = db.execute(
            "SELECT texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
            (p["page_debut"], p["page_fin"]),
        ).fetchall()
        textes[p["id"]] = "\n".join(pg["texte"] for pg in pages)

    provider = obtenir_provider(config)
    retenus = 0
    for i in range(0, len(pieces), PIECES_PAR_APPEL):
        lot = pieces[i : i + PIECES_PAR_APPEL]
        prompt = "\n\n".join(
            f"[pièce {p['id']}] (nature déjà identifiée : {p['type']})\n{textes[p['id']][:EXTRAIT_CARACTERES]}"
            for p in lot
        )
        try:
            reponse = provider.appeler(systeme=PROMPT_TITRES, prompt=prompt, modele=config.modele_classification)
            compteur["tokens_in"] += reponse.tokens_in
            compteur["tokens_out"] += reponse.tokens_out
            propositions = extraire_json(reponse.texte).get("titres", [])
        except ErreurModeOffline:
            raise
        except Exception as exc:  # noqa: BLE001 — sans intitulé, l'index garde le type
            console.print(f"  [classify] intitulés non générés pour un lot de pièces ({exc}).")
            continue
        ids_du_lot = {p["id"] for p in lot}
        for proposition in propositions:
            if not isinstance(proposition, dict):
                continue
            piece_id, titre = proposition.get("id"), (proposition.get("titre") or "").strip()
            if piece_id in ids_du_lot and intitule_verifie(titre, textes[piece_id]):
                db.execute("UPDATE pieces SET titre = ? WHERE id = ?", (titre, piece_id))
                retenus += 1
    db.commit()
    console.print(f"  [classify] intitulés d'index retenus : {retenus}/{len(pieces)}")
    return retenus
