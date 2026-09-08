"""Étape 5 — Déclarations et divergences.

Extraction déterministe pour les auditions au format Question/Réponse
(convention fréquente des PV français) : chaque paire devient une
déclaration sourcée. Pour les PV rédigés en style narratif (sans structure
Q/R explicite), l'extraction passe par le modèle — jamais en --offline,
où ces pièces sont simplement signalées comme non couvertes plutôt que
devinées.

Détection de divergence : quand une même personne aborde le même point
factuel dans deux auditions différentes avec des citations qui ne sont pas
identiques, les deux sont présentées côte à côte. Le rapprochement entre
questions différemment formulées mais portant sur le même sujet repose ici
sur une liste de mots-clés volontairement restreinte (voir TOPICS_CONNUS) :
c'est un filet de sécurité déterministe pour les cas évidents, pas une
consolidation sémantique générale — celle-ci reste le rôle du modèle en
usage réel. L'outil ne qualifie jamais la divergence de "contradiction" ou
de "mensonge" : il présente, point.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .classify import _detecter_personnes_avec_role
from .config import Config
from .llm import ErreurModeOffline, obtenir_provider
from .verification import _normaliser, verifier_table

RE_QUESTION = re.compile(r"^Question\s*:\s*(.+)$")
RE_REPONSE = re.compile(r"^Réponse\s*:\s*(.+)$")

TYPES_AUDITION = ("PV d'audition", "PV d'audition libre")

# Rapprochement déterministe minimal entre questions différemment formulées
# mais portant sur le même point factuel. Volontairement restreint : la
# consolidation générale reste le rôle du modèle en usage réel.
TOPICS_CONNUS: list[tuple[str, list[str]]] = [
    ("heure d'arrivée sur les lieux", ["arriv"]),
]


def _detecter_point_factuel(question: str, reponse: str) -> str:
    bloc = f"{question} {reponse}".lower()
    for label, mots in TOPICS_CONNUS:
        if any(mot in bloc for mot in mots):
            return label
    return question.strip().rstrip("?").strip().lower()


def _declarant_de_la_piece(db: sqlite3.Connection, page_entete: str) -> int | None:
    """Identifie le déclarant à partir du rôle explicitement tagué dans
    l'en-tête de la pièce ("(MIS EN CAUSE)", "(VICTIME)", "(TÉMOIN)") —
    jamais en cherchant le premier nom mentionné n'importe où dans le
    texte, qui attraperait aussi bien un tiers cité dans une question."""
    roles = _detecter_personnes_avec_role(page_entete)
    if not roles:
        return None
    nom, role = roles[0]
    row = db.execute("SELECT id FROM personnes WHERE nom = ? AND role = ?", (nom, role)).fetchone()
    return row["id"] if row else None


def _extraire_qr_deterministe(pages: list[sqlite3.Row]) -> list[dict]:
    resultats: list[dict] = []
    question_courante: str | None = None
    for page in pages:
        for ligne in page["texte"].splitlines():
            ligne = ligne.strip()
            m_q = RE_QUESTION.match(ligne)
            if m_q:
                question_courante = m_q.group(1).strip()
                continue
            m_r = RE_REPONSE.match(ligne)
            if m_r and question_courante:
                reponse = m_r.group(1).strip()
                resultats.append(
                    {
                        "page": page["numero_global"],
                        "citation": reponse,
                        "point_factuel": _detecter_point_factuel(question_courante, reponse),
                    }
                )
                question_courante = None
    return resultats


def _extraire_declarations_llm(config: Config, pages: list[sqlite3.Row], console: Console) -> list[dict]:
    provider = obtenir_provider(config)
    texte = "\n".join(f"[page {p['numero_global']}]\n{p['texte']}" for p in pages)
    try:
        reponse = provider.appeler(
            systeme=(
                "Tu extrais les points factuels déclarés par la personne auditionnée dans "
                "cette pièce de procédure pénale française, rédigée en style narratif "
                "(sans structure Question/Réponse explicite). Pour chaque point, cite le "
                "texte EXACT (mot pour mot) et le numéro de page. N'invente rien, ne déduis "
                "rien. Réponds en JSON : une liste d'objets "
                '{"page": int, "citation": "...", "point_factuel": "..."}.'
            ),
            prompt=texte[:8000],
            modele=config.modele_analyse,
        )
        return json.loads(reponse.texte)
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [decl] échec extraction LLM ({exc}), pièce ignorée.")
        return []


def lancer_declarations(db: sqlite3.Connection, config: Config, force: bool, console: Console) -> None:
    debut = datetime.now(timezone.utc)

    if force:
        db.execute("DELETE FROM declarations")
        db.execute("DELETE FROM divergences")
        db.commit()
    elif db.execute("SELECT COUNT(*) FROM declarations").fetchone()[0] > 0:
        console.print("  [decl] des déclarations existent déjà, ignoré (utilise --force pour retraiter).")
        return

    pieces = db.execute(
        "SELECT * FROM pieces WHERE type IN (?, ?) ORDER BY page_debut", TYPES_AUDITION
    ).fetchall()
    if not pieces:
        console.print("  [decl] aucune pièce d'audition en base — lance d'abord `depouille classify`.")
        return

    nb_deterministe = 0
    nb_llm = 0
    nb_non_couvert = 0

    for piece in pieces:
        pages = db.execute(
            "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
            (piece["page_debut"], piece["page_fin"]),
        ).fetchall()
        personne_id = _declarant_de_la_piece(db, pages[0]["texte"])
        if personne_id is None:
            console.print(
                f"  [decl] pièce {piece['id']} (pages {piece['page_debut']}-{piece['page_fin']}) : "
                "rôle du déclarant non identifiable dans l'en-tête, ignorée."
            )
            continue

        points = _extraire_qr_deterministe(pages)
        if points:
            nb_deterministe += len(points)
        elif not config.offline:
            points = _extraire_declarations_llm(config, pages, console)
            nb_llm += len(points)
        else:
            nb_non_couvert += 1
            console.print(
                f"  [decl] pièce {piece['id']} (pages {piece['page_debut']}-{piece['page_fin']}) "
                "sans structure Question/Réponse reconnue -- non couverte en --offline."
            )
            continue

        for point in points:
            db.execute(
                """INSERT INTO declarations
                   (personne_id, piece_id, page, citation, point_factuel, statut_verif)
                   VALUES (?, ?, ?, ?, ?, 'a_faire')""",
                (personne_id, piece["id"], point["page"], point["citation"], point["point_factuel"]),
            )
    db.commit()

    resume = verifier_table(db, "declarations", config.seuil_flou_ocr)

    # Détection de divergences, uniquement sur les déclarations vérifiées.
    groupes = db.execute(
        """SELECT personne_id, point_factuel, id, piece_id, page, citation
           FROM declarations WHERE statut_verif = 'verifie' AND personne_id IS NOT NULL
           ORDER BY personne_id, point_factuel, page"""
    ).fetchall()

    par_cle: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for ligne in groupes:
        par_cle.setdefault((ligne["personne_id"], ligne["point_factuel"]), []).append(ligne)

    nb_divergences = 0
    for (personne_id, point_factuel), lignes in par_cle.items():
        for a, b in zip(lignes, lignes[1:]):
            if a["piece_id"] == b["piece_id"]:
                continue
            if _normaliser(a["citation"]) == _normaliser(b["citation"]):
                continue
            db.execute(
                """INSERT INTO divergences (personne_id, point_factuel, declaration_id_a, declaration_id_b)
                   VALUES (?, ?, ?, ?)""",
                (personne_id, point_factuel, a["id"], b["id"]),
            )
            nb_divergences += 1
    db.commit()

    fin = datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin) VALUES (?, ?, ?, ?)",
        ("decl", "termine", debut.isoformat(), fin.isoformat()),
    )
    db.commit()

    table = Table(title="Déclarations vérifiées")
    table.add_column("Personne")
    table.add_column("Point factuel")
    table.add_column("Page")
    table.add_column("Citation")
    for row in db.execute(
        """SELECT d.page, d.citation, d.point_factuel, p.nom
           FROM declarations d LEFT JOIN personnes p ON p.id = d.personne_id
           WHERE d.statut_verif = 'verifie' ORDER BY p.nom, d.point_factuel, d.page"""
    ):
        table.add_row(row["nom"] or "NON TROUVÉ", row["point_factuel"], str(row["page"]), row["citation"][:70])
    console.print(table)

    if nb_divergences:
        table_div = Table(title="Divergences détectées")
        table_div.add_column("Personne")
        table_div.add_column("Point factuel")
        table_div.add_column("Version A")
        table_div.add_column("Version B")
        for row in db.execute(
            """SELECT p.nom, dv.point_factuel,
                      da.page AS page_a, da.citation AS citation_a,
                      db_.page AS page_b, db_.citation AS citation_b
               FROM divergences dv
               JOIN declarations da ON da.id = dv.declaration_id_a
               JOIN declarations db_ ON db_.id = dv.declaration_id_b
               LEFT JOIN personnes p ON p.id = dv.personne_id"""
        ):
            table_div.add_row(
                row["nom"] or "NON TROUVÉ",
                row["point_factuel"],
                f"p.{row['page_a']} : {row['citation_a']}",
                f"p.{row['page_b']} : {row['citation_b']}",
            )
        console.print(table_div)

    console.print(
        f"  Déclarations extraites par règle Q/R : {nb_deterministe} — par modèle : {nb_llm} — "
        f"pièces non couvertes (--offline, style narratif) : {nb_non_couvert}"
    )
    console.print(
        f"  Déclarations vérifiées : {sum(resume.values()) - resume['rejetee']} "
        f"(dont {resume['floue_ocr']} par correspondance floue OCR), rejetées : {resume['rejetee']}"
    )
    console.print(f"  Divergences détectées : {nb_divergences}")
