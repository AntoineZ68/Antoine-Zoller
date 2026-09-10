"""Étape build (complément) — résumé en une phrase de l'affaire.

Le seul module du pipeline qui synthétise plutôt qu'extrait : contrairement
au reste de l'outil, sa sortie n'est pas une citation vérifiable page par
page. Le garde-fou est ailleurs — il ne relit jamais les PDF bruts, unique-
ment les personnes et les faits déjà extraits ET vérifiés par les étapes
précédentes, pour qu'une hallucination du modèle ne puisse pas introduire un
élément qui n'existe nulle part ailleurs dans le dossier. Jamais de
qualification juridique, jamais "nullité"/"irrégularité" : uniquement qui
est concerné et pour quoi, au stade de constat.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from rich.console import Console

from .config import Config
from .llm import ErreurModeOffline, obtenir_provider


def generer_resume(db: sqlite3.Connection, config: Config, console: Console) -> None:
    if config.offline:
        console.print("  [build] --offline actif : pas de résumé en une phrase (nécessite un appel au modèle).")
        return

    personnes = db.execute("SELECT nom, role FROM personnes ORDER BY role, nom").fetchall()
    faits = db.execute(
        "SELECT description FROM evenements_faits WHERE statut_verif = 'verifie' ORDER BY page LIMIT 100"
    ).fetchall()

    if not faits:
        console.print("  [build] aucun fait vérifié en base — pas de résumé généré.")
        return

    bloc_personnes = "\n".join(f"- {p['nom']} ({p['role']})" for p in personnes) or "Aucune personne identifiée."
    bloc_faits = "\n".join(f"- {f['description']}" for f in faits)

    debut = datetime.now(timezone.utc)
    provider = obtenir_provider(config)
    try:
        reponse = provider.appeler(
            systeme=(
                "Tu rédiges UNE SEULE phrase COURTE (30 mots maximum) de résumé factuel "
                "d'un dossier de procédure pénale française, à l'usage d'un avocat qui vient "
                "d'ouvrir le dossier — un aperçu au premier coup d'œil, pas un paragraphe. "
                "Ne retiens QUE l'essentiel : qui est mis en cause, pour quels faits "
                "principaux, et à quel stade de la procédure (ex. garde à vue). Omets les "
                "détails secondaires (témoignages annexes, circonstances mineures, éléments "
                "de contexte). "
                "Base-toi UNIQUEMENT sur les personnes et faits déjà extraits ci-dessous — "
                "n'ajoute, ne déduis et n'invente aucune information qui n'y figure pas. "
                "Reste strictement factuel et neutre : jamais de qualification juridique, "
                "jamais les mots \"nullité\" ou \"irrégularité\", jamais d'appréciation sur "
                "la culpabilité ou la solidité de l'accusation. Réponds uniquement avec cette "
                "phrase, sans guillemets ni préambule."
            ),
            prompt=f"Personnes identifiées :\n{bloc_personnes}\n\nFaits établis :\n{bloc_faits}",
            modele=config.modele_analyse,
        )
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [build] échec de la génération du résumé ({exc}).")
        return

    texte = reponse.texte.strip().strip('"').strip()
    if not texte:
        return

    maintenant = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO resume_affaire (id, texte, genere_le) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET texte = excluded.texte, genere_le = excluded.genere_le",
        (texte, maintenant),
    )

    fin = datetime.now(timezone.utc)
    cout = config.cout(config.modele_analyse, reponse.tokens_in, reponse.tokens_out)
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin, tokens_in, tokens_out, cout_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("build", "termine", debut.isoformat(), fin.isoformat(), reponse.tokens_in, reponse.tokens_out, cout),
    )
    db.commit()

    console.print(f'  [build] résumé généré : "{texte}"')
