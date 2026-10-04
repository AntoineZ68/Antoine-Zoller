"""Gardes à vue, personne par personne.

Un dossier à plusieurs mis en cause compte plusieurs gardes à vue, chacune
avec son placement, sa notification des droits, son avocat, son médecin,
sa fin. Calculer « le » délai de notification en prenant le premier
placement et la première notification du dossier pouvait apparier le
placement de l'un avec la notification de l'autre — un chiffre faux, et
présenté comme exact. Ici, chaque acte est rattaché à la personne de sa
pièce, et chaque garde à vue est calculée avec ses seuls actes.

Un acte dont la personne n'a pas pu être identifiée n'est rattaché à une
garde à vue que s'il n'y en a qu'UNE dans le dossier — il ne peut alors
concerner personne d'autre. Avec plusieurs gardes à vue, il n'est rattaché
à aucune : mieux vaut un délai « non retrouvé » qu'un délai inventé.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

NATURES_GAV = (
    "interpellation",
    "placement_garde_a_vue",
    "notification_droits",
    "demande_examen_medical",
    "realisation_examen_medical",
    "demande_entretien_avocat",
    "realisation_entretien_avocat",
    "debut_audition",
    "fin_audition",
    "debut_perquisition",
    "fin_perquisition",
    "prolongation_garde_a_vue",
    "fin_garde_a_vue",
)
# Fenêtre autour de la mesure dans laquelle un acte de la personne est
# montré sur sa ligne : l'interpellation précède le placement, et une
# garde à vue sans fin retrouvée ne dure pas indéfiniment.
AVANT_PLACEMENT = timedelta(hours=6)
SANS_FIN_RETROUVEE = timedelta(hours=96)


def _instant(date: str | None, heure: str | None) -> datetime | None:
    if not date or not heure:
        return None
    try:
        j, m, a = date.split("/")
        h, mn = heure.split("h")
        return datetime(int(a), int(m), int(j), int(h), int(mn))
    except ValueError:
        return None


def _minutes(debut: dict | None, fin: dict | None) -> int | None:
    if not debut or not fin or debut["instant"] is None or fin["instant"] is None:
        return None
    ecart = (fin["instant"] - debut["instant"]).total_seconds() / 60
    return round(ecart) if ecart >= 0 else None


def _premier(evenements: list[dict], nature: str, apres: datetime | None = None) -> dict | None:
    candidats = [e for e in evenements if e["nature"] == nature]
    if apres is not None:
        suivants = [e for e in candidats if e["instant"] is not None and e["instant"] >= apres]
        if suivants:
            return suivants[0]
    return candidats[0] if candidats else None


def gardes_a_vue(db: sqlite3.Connection) -> list[dict]:
    """Une entrée par personne placée en garde à vue, dans l'ordre des
    placements. Durées en minutes, None quand un des deux actes manque."""
    colonnes = {r[1] for r in db.execute("PRAGMA table_info(personnes)")}
    champ_client = "pe.est_client" if "est_client" in colonnes else "0"
    lignes = db.execute(
        f"""SELECT e.nature, e.date, e.heure, e.page, e.citation, e.personne_id,
                   pe.nom, {champ_client} AS est_client
            FROM evenements_procedure e LEFT JOIN personnes pe ON pe.id = e.personne_id
            WHERE e.statut_verif = 'verifie' AND e.nature IN ({",".join("?" * len(NATURES_GAV))})
            ORDER BY e.id""",
        NATURES_GAV,
    ).fetchall()
    evenements = [dict(l, instant=_instant(l["date"], l["heure"])) for l in lignes]
    evenements.sort(key=lambda e: (e["instant"] is None, e["instant"] or datetime.min))

    placements: dict[int | None, dict] = {}
    for e in evenements:
        if e["nature"] == "placement_garde_a_vue" and e["personne_id"] not in placements:
            placements[e["personne_id"]] = e
    une_seule = len(placements) == 1

    resultat = []
    for personne_id, placement in placements.items():
        siens = [
            e for e in evenements
            if e["personne_id"] == personne_id or (une_seule and e["personne_id"] is None)
        ]
        debut = placement["instant"]
        fin = _premier(siens, "fin_garde_a_vue", apres=debut)
        borne_fin = fin["instant"] if fin and fin["instant"] else (debut + SANS_FIN_RETROUVEE if debut else None)
        visibles = [
            e for e in siens
            if debut is None or e["instant"] is None or (debut - AVANT_PLACEMENT <= e["instant"] <= borne_fin)
        ]
        demande_medecin = _premier(siens, "demande_examen_medical", apres=debut)
        demande_avocat = _premier(siens, "demande_entretien_avocat", apres=debut)
        resultat.append({
            "personne_id": personne_id,
            "nom": placement["nom"],
            "est_client": bool(placement["est_client"]),
            "placement": placement,
            "fin": fin,
            "duree_minutes": _minutes(placement, fin),
            "delai_notification_minutes": _minutes(placement, _premier(siens, "notification_droits", apres=debut)),
            "delai_examen_medical_minutes": _minutes(
                demande_medecin, _premier(siens, "realisation_examen_medical", apres=demande_medecin and demande_medecin["instant"])
            ),
            "delai_entretien_avocat_minutes": _minutes(
                demande_avocat, _premier(siens, "realisation_entretien_avocat", apres=demande_avocat and demande_avocat["instant"])
            ),
            "evenements": visibles,
            "actes_sans_personne_rattaches": une_seule and any(e["personne_id"] is None for e in siens),
        })
    return resultat


def formater_duree(minutes: int | None) -> str:
    """« 23 h 42 », « 52 min », « NON TROUVÉ »."""
    if minutes is None:
        return "NON TROUVÉ"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"
