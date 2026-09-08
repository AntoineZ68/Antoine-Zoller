"""Étape 4 — Chronologies.

Chronologie de procédure : entièrement déterministe (regex sur les
formulations standard des PV français). Chronologie des faits : narrative,
via appel au modèle (jamais en --offline), toujours suivie de la passe de
vérification déterministe — un fait dont la citation n'est pas retrouvée
sur la page annoncée ne survit pas.

Les durées sont calculées uniquement à partir d'événements dont la citation
a été vérifiée. Un événement manquant ou non vérifié rend la durée
correspondante NON TROUVÉ — jamais une estimation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table

from .classify import _est_titre
from .config import Config
from .llm import ErreurModeOffline, extraire_json, obtenir_provider
from .regex_patterns import (
    FRAGMENT_DATE,
    FRAGMENT_HEURE,
    normaliser_date,
    normaliser_heure,
    phrase_contenant,
    texte_sans_entete,
)
from .verification import verifier_table

RE_DATE_HEURE_ACTE = re.compile(rf"\bLe\s+{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}\b", re.IGNORECASE)
RE_INTERPELLATION = re.compile(
    rf"[Ii]nterpellation\s+effectuée\s+le\s+{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE
)
# Une garde à vue notifiée après coup peut prendre effet "rétroactivement" à
# l'heure de l'interpellation (article 63 CPP) : quand le texte le dit
# explicitement, c'est cette date/heure-là qui compte comme début réel de la
# mesure, pas l'heure de rédaction du PV de notification.
RE_RETROACTIF = re.compile(
    rf"rétroactivement.{{0,80}}?{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}",
    re.IGNORECASE | re.DOTALL,
)
RE_DEMANDE_MEDECIN = re.compile(rf"réquisition\s+du\s+{FRAGMENT_DATE}\s+reçue\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE)
RE_REALISATION_MEDECIN = re.compile(rf"examiné\s+ce\s+jour\s+{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE)
RE_DEMANDE_AVOCAT = re.compile(
    rf"[Dd]emande\s+d'entretien\s+formulée\s+le\s+{FRAGMENT_DATE}\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE
)
RE_REALISATION_AVOCAT = re.compile(
    rf"réalisé\s+le\s+{FRAGMENT_DATE}\s+de\s+{FRAGMENT_HEURE}\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE
)
RE_PERQUISITION = re.compile(rf"[Ll]e\s+{FRAGMENT_DATE}\s+de\s+{FRAGMENT_HEURE}\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE)
RE_AUDITION_DEBUT_FIN = re.compile(
    rf"[Ll]e\s+{FRAGMENT_DATE},?\s*[Aa]udition\s+débutée\s+à\s+{FRAGMENT_HEURE},?\s*close\s+à\s+{FRAGMENT_HEURE}",
    re.IGNORECASE,
)
RE_AUDITION_DEBUT = re.compile(
    rf"[Ll]e\s+{FRAGMENT_DATE},?\s*[Aa]udition\s+débutée\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE
)
RE_AUDITION_FIN = re.compile(rf"[Aa]udition\s+close\s+à\s+{FRAGMENT_HEURE}", re.IGNORECASE)

NATURE_SIMPLE_PAR_TYPE = {
    "PV d'interpellation": "interpellation",
    "PV de notification de placement en garde à vue": "placement_garde_a_vue",
    "PV de notification des droits": "notification_droits",
    "PV de prolongation de garde à vue": "prolongation_garde_a_vue",
    "PV de fin de garde à vue": "fin_garde_a_vue",
}


def _personne_par_nom(db: sqlite3.Connection, nom_libre: str) -> int | None:
    """Résout un nom en texte libre (renvoyé par le modèle, ex. dans
    "personne_source") vers une personne déjà identifiée dans le dossier —
    jamais en créant une nouvelle entrée : un fait narratif ne doit pas
    faire apparaître une personne qui n'a pas été identifiée par un canal
    vérifié (tag d'en-tête, première mention fiable, ou identification LLM
    déjà vérifiée pendant la classification)."""
    if not nom_libre or not nom_libre.strip():
        return None
    row = db.execute("SELECT id FROM personnes WHERE lower(nom) = lower(?)", (nom_libre.strip(),)).fetchone()
    return row["id"] if row else None


def _chercher_sur_pages(pages: list[sqlite3.Row], motif: re.Pattern) -> tuple[int, re.Match, str] | None:
    for page in pages:
        bloc = texte_sans_entete(page["texte"], _est_titre)
        m = motif.search(bloc)
        if m:
            return page["numero_global"], m, phrase_contenant(bloc, m.start())
    return None


def _extraire_evenements_piece(db: sqlite3.Connection, piece: sqlite3.Row, pages: list[sqlite3.Row]) -> list[dict]:
    type_ = piece["type"]
    # Calculé une seule fois pendant la classification (identifier_personne_principale)
    # et réutilisé ici pour ne jamais recalculer ni repayer un appel au modèle.
    personne_id = piece["personne_principale_id"]
    evenements: list[dict] = []

    def ajouter(nature: str, resultat, date_idx: int | None, heure_idx: int, personne: int | None = None) -> None:
        page, m, citation = resultat
        date_brute = m.group(date_idx) if date_idx else None
        evenements.append(
            {
                "nature": nature,
                "date": normaliser_date(date_brute) if date_brute else None,
                "heure": normaliser_heure(m.group(heure_idx)),
                "page": page,
                "citation": citation,
                "personne_id": personne if personne is not None else personne_id,
            }
        )

    if type_ in NATURE_SIMPLE_PAR_TYPE:
        nature = NATURE_SIMPLE_PAR_TYPE[type_]
        r = _chercher_sur_pages(pages, RE_DATE_HEURE_ACTE)
        if r:
            ajouter(nature, r, 1, 2)

        if type_ == "PV de notification de placement en garde à vue":
            r_retro = _chercher_sur_pages(pages, RE_RETROACTIF)
            if r_retro:
                # La mesure prend effet rétroactivement à l'heure indiquée,
                # explicitement écrite dans le texte (art. 63 CPP) : c'est
                # cet horodatage qui remplace celui de l'acte de
                # notification comme début réel de la garde à vue.
                evenements[:] = [e for e in evenements if e["nature"] != "placement_garde_a_vue"]
                ajouter("placement_garde_a_vue", r_retro, 1, 2)

            r_interp = _chercher_sur_pages(pages, RE_INTERPELLATION)
            if r_interp:
                ajouter("interpellation", r_interp, 1, 2)

    elif type_ == "Certificat médical":
        r = _chercher_sur_pages(pages, RE_DEMANDE_MEDECIN)
        if r:
            ajouter("demande_examen_medical", r, 1, 2)
        r = _chercher_sur_pages(pages, RE_REALISATION_MEDECIN)
        if r:
            ajouter("realisation_examen_medical", r, 1, 2)

    elif type_ == "PV d'entretien avocat":
        r = _chercher_sur_pages(pages, RE_DEMANDE_AVOCAT)
        if r:
            ajouter("demande_entretien_avocat", r, 1, 2)
        r = _chercher_sur_pages(pages, RE_REALISATION_AVOCAT)
        if r:
            ajouter("realisation_entretien_avocat", r, 1, 2)

    elif type_ == "PV de perquisition":
        r = _chercher_sur_pages(pages, RE_PERQUISITION)
        if r:
            page, m, citation = r
            date = normaliser_date(m.group(1))
            evenements.append(
                {"nature": "debut_perquisition", "date": date, "heure": normaliser_heure(m.group(2)), "page": page, "citation": citation, "personne_id": personne_id}
            )
            evenements.append(
                {"nature": "fin_perquisition", "date": date, "heure": normaliser_heure(m.group(3)), "page": page, "citation": citation, "personne_id": personne_id}
            )

    elif type_ in ("PV d'audition", "PV d'audition libre"):
        r = _chercher_sur_pages(pages, RE_AUDITION_DEBUT_FIN)
        if r:
            page, m, citation = r
            date = normaliser_date(m.group(1))
            evenements.append(
                {"nature": "debut_audition", "date": date, "heure": normaliser_heure(m.group(2)), "page": page, "citation": citation, "personne_id": personne_id}
            )
            evenements.append(
                {"nature": "fin_audition", "date": date, "heure": normaliser_heure(m.group(3)), "page": page, "citation": citation, "personne_id": personne_id}
            )
        else:
            r = _chercher_sur_pages(pages, RE_AUDITION_DEBUT)
            if r:
                ajouter("debut_audition", r, 1, 2)
            r = _chercher_sur_pages(pages, RE_AUDITION_FIN)
            if r:
                ajouter("fin_audition", r, None, 1)

    return evenements


def _combiner_date_heure(date_str: str | None, heure_str: str | None) -> datetime | None:
    if not date_str or not heure_str:
        return None
    try:
        j, m, a = date_str.split("/")
        h, mn = heure_str.split("h")
        return datetime(int(a), int(m), int(j), int(h), int(mn))
    except ValueError:
        return None


def _evenement_verifie(db: sqlite3.Connection, nature: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM evenements_procedure WHERE nature = ? AND statut_verif = 'verifie' ORDER BY id LIMIT 1",
        (nature,),
    ).fetchone()


def calculer_durees(db: sqlite3.Connection) -> dict[str, str]:
    """Calcule les délais requis, en minutes ou en heures. NON TROUVÉ si l'un
    des deux événements manque ou n'a pas été vérifié."""

    def delai(nature_debut: str, nature_fin: str) -> str:
        e1, e2 = _evenement_verifie(db, nature_debut), _evenement_verifie(db, nature_fin)
        d1 = _combiner_date_heure(e1["date"], e1["heure"]) if e1 else None
        d2 = _combiner_date_heure(e2["date"], e2["heure"]) if e2 else None
        if d1 is None or d2 is None:
            return "NON TROUVÉ"
        delta: timedelta = d2 - d1
        minutes = delta.total_seconds() / 60
        return f"{minutes:.0f} min"

    def duree_heures(nature_debut: str, nature_fin: str) -> str:
        e1, e2 = _evenement_verifie(db, nature_debut), _evenement_verifie(db, nature_fin)
        d1 = _combiner_date_heure(e1["date"], e1["heure"]) if e1 else None
        d2 = _combiner_date_heure(e2["date"], e2["heure"]) if e2 else None
        if d1 is None or d2 is None:
            return "NON TROUVÉ"
        return f"{(d2 - d1).total_seconds() / 3600:.1f} h"

    return {
        "duree_totale_garde_a_vue": duree_heures("placement_garde_a_vue", "fin_garde_a_vue"),
        "delai_placement_notification_droits": delai("placement_garde_a_vue", "notification_droits"),
        "delai_demande_realisation_examen_medical": delai("demande_examen_medical", "realisation_examen_medical"),
        "delai_demande_realisation_entretien_avocat": delai("demande_entretien_avocat", "realisation_entretien_avocat"),
    }


TYPES_NARRATIFS = ("PV d'audition", "PV d'audition libre", "PV de constatations", "PV de synthèse")


def _extraire_faits_llm(
    db: sqlite3.Connection, config: Config, pieces: list[sqlite3.Row], console: Console, compteur: dict[str, int]
) -> int:
    provider = obtenir_provider(config)
    nb = 0
    for piece in pieces:
        if piece["type"] not in TYPES_NARRATIFS:
            continue
        pages = db.execute(
            "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
            (piece["page_debut"], piece["page_fin"]),
        ).fetchall()
        texte = "\n".join(f"[page {p['numero_global']}]\n{p['texte']}" for p in pages)
        try:
            reponse = provider.appeler(
                systeme=(
                    "Tu extrais des affirmations factuelles d'une pièce de procédure pénale "
                    "française. Pour chaque affirmation, cite le texte EXACT (mot pour mot, "
                    "sans reformuler) et le numéro de page où il apparaît. N'invente rien, ne "
                    "déduis rien. Réponds en JSON : une liste d'objets "
                    '{"page": int, "citation": "...", "description": "...", "personne_source": "..."}.'
                ),
                prompt=texte[:8000],
                modele=config.modele_analyse,
            )
            compteur["tokens_in"] += reponse.tokens_in
            compteur["tokens_out"] += reponse.tokens_out
            faits = extraire_json(reponse.texte)
        except ErreurModeOffline:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [chrono] échec extraction des faits pour la pièce {piece['id']} ({exc}), ignorée.")
            continue

        for fait in faits:
            personne_id = _personne_par_nom(db, fait.get("personne_source", ""))
            db.execute(
                """INSERT INTO evenements_faits
                   (piece_id, page, citation, personne_id_source, description, statut_verif)
                   VALUES (?, ?, ?, ?, ?, 'a_faire')""",
                (piece["id"], fait["page"], fait["citation"], personne_id, fait.get("description", "")),
            )
            nb += 1
    db.commit()
    return nb


def lancer_chrono(db: sqlite3.Connection, config: Config, force: bool, console: Console) -> None:
    debut = datetime.now(timezone.utc)

    if force:
        db.execute("DELETE FROM evenements_procedure")
        db.execute("DELETE FROM evenements_faits")
        db.commit()
    elif db.execute("SELECT COUNT(*) FROM evenements_procedure").fetchone()[0] > 0:
        console.print("  [chrono] des événements existent déjà, ignoré (utilise --force pour retraiter).")
        return

    pieces = db.execute("SELECT * FROM pieces ORDER BY page_debut").fetchall()
    if not pieces:
        console.print("  [chrono] aucune pièce en base — lance d'abord `depouille classify`.")
        return

    for piece in pieces:
        pages = db.execute(
            "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
            (piece["page_debut"], piece["page_fin"]),
        ).fetchall()
        for ev in _extraire_evenements_piece(db, piece, pages):
            db.execute(
                """INSERT INTO evenements_procedure
                   (piece_id, date, heure, nature, personne_id, service, page, citation, statut_verif)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'a_faire')""",
                (piece["id"], ev["date"], ev["heure"], ev["nature"], ev["personne_id"], None, ev["page"], ev["citation"]),
            )
    db.commit()

    compteur = {"tokens_in": 0, "tokens_out": 0}
    if config.offline:
        console.print(
            "  [chrono] --offline actif : la chronologie des faits (narrative, LLM) n'est pas "
            "générée. Seule la chronologie de procédure (déterministe) est produite."
        )
        nb_faits = 0
    else:
        nb_faits = _extraire_faits_llm(db, config, pieces, console, compteur)

    resume_procedure = verifier_table(db, "evenements_procedure", config.seuil_flou_ocr)
    resume_faits = verifier_table(db, "evenements_faits", config.seuil_flou_ocr)

    fin = datetime.now(timezone.utc)
    cout = config.cout(config.modele_analyse, compteur["tokens_in"], compteur["tokens_out"])
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin, tokens_in, tokens_out, cout_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("chrono", "termine", debut.isoformat(), fin.isoformat(), compteur["tokens_in"], compteur["tokens_out"], cout),
    )
    db.commit()

    table = Table(title="Chronologie de procédure")
    table.add_column("Date")
    table.add_column("Heure")
    table.add_column("Nature")
    table.add_column("Page")
    table.add_column("Statut")
    for row in db.execute(
        "SELECT * FROM evenements_procedure WHERE statut_verif = 'verifie' ORDER BY date, heure"
    ):
        table.add_row(row["date"] or "NON TROUVÉ", row["heure"] or "NON TROUVÉ", row["nature"], str(row["page"]), row["statut_verif"])
    console.print(table)

    durees = calculer_durees(db)
    table_durees = Table(title="Durées calculées")
    table_durees.add_column("Indicateur")
    table_durees.add_column("Valeur", justify="right")
    for cle, valeur in durees.items():
        table_durees.add_row(cle, valeur)
    console.print(table_durees)

    console.print(
        f"  Événements de procédure vérifiés : {sum(resume_procedure.values()) - resume_procedure['rejetee']} "
        f"(dont {resume_procedure['floue_ocr']} par correspondance floue OCR), rejetés : {resume_procedure['rejetee']}"
    )
    console.print(
        f"  Faits narratifs extraits : {nb_faits}, vérifiés : "
        f"{sum(resume_faits.values()) - resume_faits['rejetee']}, rejetés : {resume_faits['rejetee']}"
    )
