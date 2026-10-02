"""Étape build (complément) — résumé de l'affaire en un court paragraphe.

Le seul module du pipeline qui synthétise plutôt qu'extrait : contrairement
au reste de l'outil, sa sortie n'est pas une citation vérifiable page par
page. Le garde-fou est ailleurs — il ne relit jamais les PDF bruts, unique-
ment les personnes, les faits et les déclarations déjà extraits ET vérifiés
par les étapes précédentes, pour qu'une hallucination du modèle ne puisse
pas introduire un élément qui n'existe nulle part ailleurs dans le dossier.
Jamais de qualification juridique de notre part : un filtre déterministe
(garde_fous) écarte tout résumé qui en contiendrait malgré la consigne.

Un paragraphe plutôt qu'une phrase : une phrase de trente mots disait qui et
quoi, mais pas ce que l'avocat cherche en premier en ouvrant un dossier —
ce que son client reconnaît et ce qu'il conteste.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from rich.console import Console

from .config import Config
from .garde_fous import contient_qualification
from .llm import ErreurModeOffline, obtenir_provider

PROMPT_RESUME = (
    "Tu rédiges le résumé factuel d'un dossier de procédure pénale française, pour un "
    "avocat qui l'ouvre pour la première fois. Un seul paragraphe de 3 à 5 phrases, 110 "
    "mots au maximum, sans titre, sans liste, sans guillemets ni préambule. "
    "Dans l'ordre : la nature des faits et leur période ; les personnes en cause et leur "
    "rôle tel qu'il apparaît dans le dossier ; le stade de la procédure ; puis, s'il y a "
    "des déclarations de la personne mise en cause, ce qu'elle reconnaît et ce qu'elle "
    "conteste, en reprenant ses propres déclarations sans les interpréter. "
    "Base-toi UNIQUEMENT sur les personnes, faits et déclarations fournis — n'ajoute, ne "
    "déduis et n'invente rien. Si une qualification pénale figure dans les faits fournis, "
    "tu peux la mentionner en l'attribuant à son auteur (« sous la qualification de … "
    "retenue par le réquisitoire »), jamais comme une appréciation de ta part. "
    "Jamais d'appréciation sur la culpabilité, la solidité des charges, la régularité des "
    "actes ou la stratégie de défense ; jamais les mots « nullité » ou « irrégularité »."
)


def _bloc_mis_en_cause(personnes: list[sqlite3.Row]) -> str:
    """« Votre client » seulement s'il n'y a qu'UN mis en cause : avec
    plusieurs coauteurs, l'outil ne peut pas savoir lequel l'avocat défend,
    et le deviner ferait dire au résumé l'inverse de la réalité."""
    mis_en_cause = [p["nom"] for p in personnes if p["role"] == "mis_en_cause"]
    if len(mis_en_cause) == 1:
        return (
            f"La personne mise en cause est {mis_en_cause[0]} : désigne-la comme « votre "
            f"client, {mis_en_cause[0]} »."
        )
    if mis_en_cause:
        return (
            "Il y a plusieurs personnes mises en cause : nomme chacune par son nom, sans "
            "jamais écrire « votre client »."
        )
    return "Aucune personne mise en cause n'est identifiée : n'écris pas « votre client »."


def generer_resume(db: sqlite3.Connection, config: Config, console: Console) -> None:
    if config.offline:
        console.print("  [build] --offline actif : pas de résumé (nécessite un appel au modèle).")
        return

    personnes = db.execute("SELECT nom, role FROM personnes ORDER BY role, nom").fetchall()
    faits = db.execute(
        "SELECT description FROM evenements_faits WHERE statut_verif = 'verifie' ORDER BY page LIMIT 100"
    ).fetchall()
    declarations = db.execute(
        """SELECT pe.nom, d.point_factuel FROM declarations d
           JOIN personnes pe ON pe.id = d.personne_id
           WHERE d.statut_verif = 'verifie' AND pe.role = 'mis_en_cause'
           ORDER BY d.page LIMIT 40"""
    ).fetchall()

    if not faits:
        console.print("  [build] aucun fait vérifié en base — pas de résumé généré.")
        return

    bloc_personnes = "\n".join(f"- {p['nom']} ({p['role']})" for p in personnes) or "Aucune personne identifiée."
    bloc_faits = "\n".join(f"- {f['description']}" for f in faits)
    bloc_declarations = (
        "\n".join(f"- {d['nom']} : {d['point_factuel']}" for d in declarations)
        or "Aucune déclaration vérifiée de personne mise en cause."
    )

    debut = datetime.now(timezone.utc)
    provider = obtenir_provider(config)
    try:
        reponse = provider.appeler(
            systeme=PROMPT_RESUME,
            prompt=(
                f"{_bloc_mis_en_cause(personnes)}\n\n"
                f"Personnes identifiées :\n{bloc_personnes}\n\n"
                f"Faits établis :\n{bloc_faits}\n\n"
                f"Déclarations vérifiées des personnes mises en cause :\n{bloc_declarations}"
            ),
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
    if contient_qualification(texte):
        # Aucun résumé plutôt qu'un résumé qui qualifie : l'avocat garde
        # la chronologie et les autres onglets, qui ne dépendent pas de lui.
        console.print("  [build] résumé écarté : il contenait une qualification juridique.")
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

    console.print(f"  [build] résumé généré ({len(texte.split())} mots).")
