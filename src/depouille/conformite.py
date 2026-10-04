"""Signalements de conformité procédurale — strictement structurels.

Ce module ne fait AUCUNE analyse juridique. Il signale des faits vérifiables
de deux natures seulement :
1. Une absence structurelle : une pièce ou un événement normalement attendu
   n'a pas été retrouvé dans le dossier (ce qui peut vouloir dire qu'il
   manque réellement, ou seulement qu'il n'a pas été identifié — l'outil ne
   tranche pas).
2. Un dépassement des deux seuils numériques de garde à vue qui font
   consensus et ne varient pas selon l'interprétation (24h puis 48h en
   régime de droit commun, articles 63 et suivants du code de procédure
   pénale) — jamais un délai qualitatif ("sans délai", "dès le début") pour
   lequel la loi ne fixe pas de chiffre, et qu'il serait malhonnête de
   remplacer par un seuil inventé.

Chaque signalement dit "à vérifier", jamais "nullité" ni "irrégularité" :
la qualification juridique reste entièrement à l'avocat. Les régimes
dérogatoires (terrorisme, criminalité organisée, mineurs) prévoient des
durées différentes du droit commun — l'outil le rappelle systématiquement
plutôt que de les ignorer silencieusement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SEUIL_GAV_DROIT_COMMUN_H = 24.0
SEUIL_GAV_PROLONGATION_MAX_H = 48.0

RAPPEL_REGIME_DEROGATOIRE = (
    "Rappel : les régimes dérogatoires (terrorisme, criminalité organisée, mineurs) "
    "prévoient des durées de garde à vue différentes du droit commun. Ce signalement "
    "suppose le droit commun et doit être écarté si un régime dérogatoire s'applique."
)


@dataclass
class Signalement:
    titre: str
    description: str
    page_reference: int | None = None
    citation_reference: str | None = None


def _evenement_verifie(db: sqlite3.Connection, nature: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM evenements_procedure WHERE nature = ? AND statut_verif = 'verifie' ORDER BY id LIMIT 1",
        (nature,),
    ).fetchone()


def _piece_existe(db: sqlite3.Connection, type_piece: str, personne_id: int | None = None) -> bool:
    if personne_id is None:
        return db.execute("SELECT COUNT(*) FROM pieces WHERE type = ?", (type_piece,)).fetchone()[0] > 0
    return db.execute(
        "SELECT COUNT(*) FROM pieces WHERE type = ? AND personne_principale_id = ?", (type_piece, personne_id)
    ).fetchone()[0] > 0


def _signalement_asymetrie(
    db: sqlite3.Connection, nature_demande: str, nature_realisation: str, titre: str
) -> Signalement | None:
    """Contrôle à l'échelle du dossier, utilisé seulement quand aucune garde
    à vue n'a été identifiée (sinon, contrôle personne par personne)."""
    demande = _evenement_verifie(db, nature_demande)
    realisation = _evenement_verifie(db, nature_realisation)
    if demande is not None and realisation is None:
        return Signalement(
            titre=titre,
            description=(
                f"Une demande a été identifiée ({demande['date'] or 'date NON TROUVÉE'} "
                f"{demande['heure'] or ''}) mais aucune réalisation correspondante n'a été "
                "retrouvée dans le dossier — à vérifier."
            ),
            page_reference=demande["page"],
            citation_reference=demande["citation"],
        )
    return None


def _signalements_garde_a_vue(db: sqlite3.Connection, gav: dict, suffixe: str, une_seule: bool) -> list[Signalement]:
    """Contrôles d'UNE garde à vue, avec ses seuls actes (voir
    gardes_a_vue.py). Une pièce sans personne identifiée ne vaut pour cette
    garde à vue que s'il n'y en a qu'une dans le dossier."""
    signalements: list[Signalement] = []
    placement = gav["placement"]
    natures = {e["nature"] for e in gav["evenements"]}
    personne = None if une_seule else gav["personne_id"]
    reference = {"page_reference": placement["page"], "citation_reference": placement["citation"]}

    if gav["fin"] is None:
        signalements.append(Signalement(
            titre=f"Fin de garde à vue non identifiée{suffixe}",
            description=(
                "Un placement en garde à vue a été identifié mais aucun acte de fin "
                "de garde à vue n'a été retrouvé dans le dossier : la durée totale "
                "n'a pas pu être calculée — à vérifier."
            ),
            **reference,
        ))
    elif gav["duree_minutes"] is not None:
        duree = gav["duree_minutes"] / 60
        prolongation = "prolongation_garde_a_vue" in natures or _piece_existe(
            db, "PV de prolongation de garde à vue", personne
        )
        if duree > SEUIL_GAV_PROLONGATION_MAX_H:
            signalements.append(Signalement(
                titre=f"Durée de garde à vue de {duree:.1f} h — dépasse 48 h{suffixe}",
                description=(
                    f"La durée calculée ({duree:.1f} h) dépasse le maximum du "
                    f"régime de droit commun (24 h + 24 h de prolongation = 48 h) "
                    f"même en tenant compte d'une prolongation. {RAPPEL_REGIME_DEROGATOIRE}"
                ),
                **reference,
            ))
        elif duree > SEUIL_GAV_DROIT_COMMUN_H and not prolongation:
            signalements.append(Signalement(
                titre=f"Durée de garde à vue de {duree:.1f} h — aucune prolongation identifiée{suffixe}",
                description=(
                    f"La durée calculée ({duree:.1f} h) dépasse les 24 h du régime "
                    "de droit commun, et aucune pièce de type \"PV de prolongation "
                    "de garde à vue\" n'a été identifiée dans le dossier : soit elle "
                    "est absente, soit elle n'a pas été reconnue par la "
                    f"classification — à vérifier. {RAPPEL_REGIME_DEROGATOIRE}"
                ),
                **reference,
            ))

    if "notification_droits" not in natures:
        signalements.append(Signalement(
            titre=f"Notification des droits non identifiée{suffixe}",
            description=(
                "Un placement en garde à vue a été identifié mais aucune notification "
                "des droits n'a été retrouvée dans le dossier — à vérifier."
            ),
            **reference,
        ))

    if not _piece_existe(db, "PV d'entretien avocat", personne):
        signalements.append(Signalement(
            titre=f"Entretien avec l'avocat non identifié{suffixe}",
            description=(
                "Aucune pièce de type \"PV d'entretien avocat\" n'a été identifiée dans "
                "le dossier. Cela peut correspondre à une renonciation expresse de la "
                "personne gardée à vue, ou à une pièce absente ou non reconnue — à vérifier."
            ),
            **reference,
        ))

    for demande, realisation, titre in (
        ("demande_examen_medical", "realisation_examen_medical", "Examen médical demandé mais non réalisé (au dossier)"),
        ("demande_entretien_avocat", "realisation_entretien_avocat", "Entretien avocat demandé mais non réalisé (au dossier)"),
    ):
        evt = next((e for e in gav["evenements"] if e["nature"] == demande), None)
        if evt is not None and realisation not in natures:
            signalements.append(Signalement(
                titre=f"{titre}{suffixe}",
                description=(
                    f"Une demande a été identifiée ({evt['date'] or 'date NON TROUVÉE'} "
                    f"{evt['heure'] or ''}) mais aucune réalisation correspondante n'a été "
                    "retrouvée dans le dossier — à vérifier."
                ),
                page_reference=evt["page"],
                citation_reference=evt["citation"],
            ))
    return signalements


def detecter_signalements(db: sqlite3.Connection) -> list[Signalement]:
    from .gardes_a_vue import gardes_a_vue

    toutes = gardes_a_vue(db)
    if not toutes:
        return [
            s for s in (
                _signalement_asymetrie(
                    db, "demande_examen_medical", "realisation_examen_medical", "Examen médical demandé mais non réalisé (au dossier)"
                ),
                _signalement_asymetrie(
                    db, "demande_entretien_avocat", "realisation_entretien_avocat", "Entretien avocat demandé mais non réalisé (au dossier)"
                ),
            ) if s is not None
        ]
    une_seule = len(toutes) == 1
    signalements: list[Signalement] = []
    for gav in toutes:
        # Le nom distingue les gardes à vue entre elles ; inutile s'il n'y en a qu'une.
        suffixe = f" — {gav['nom']}" if not une_seule and gav["nom"] else ""
        signalements.extend(_signalements_garde_a_vue(db, gav, suffixe, une_seule))
    return signalements
