"""Étape build (complément) — résumé de l'affaire en un court paragraphe.

Le seul texte que l'avocat lit avant tout le reste : il doit être juste
phrase par phrase. Même construction que le résumé détaillé : le modèle ne
reçoit QUE des éléments déjà extraits ET vérifiés au caractère près
(faits, actes de procédure, déclarations de la personne défendue), chacun
numéroté et accompagné de sa citation ; il rédige 3 ou 4 phrases et indique
pour chacune les éléments qu'elle reprend. Chaque phrase n'est retenue que
si elle ne contient aucun nom, heure, durée, nombre, infraction ou acte de
procédure absent de SES éléments, ni qualification, ni jugement.

Pourquoi phrase par phrase : un paragraphe libre, contrôlé contre
l'ensemble des éléments, laissait passer un mélange de deux pièces exactes
chacune — observé sur Mistral Large : « Paul LEROUX déclare avoir vu une
Clio blanche immatriculée GH-482-KL », alors que le témoin ne donne aucune
plaque (elle vient du PV d'interpellation). Et il faisait 150 à 300 mots
pour 110 demandés ; un nombre de phrases borné tient la longueur.

Ce que l'avocat cherche en premier en ouvrant un dossier : ce qui a
déclenché l'enquête, la situation de son client, ce que celui-ci reconnaît
et ce qu'il conteste.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from rich.console import Console

from .config import Config
from .contradictions import discordances_connues
from .garde_fous import avec_noms_completes, bloc_discordances, noms_identifies, problemes_redaction, versions_tues
from .llm import ErreurModeOffline, extraire_json, obtenir_provider
from .resume_detaille import BUDGET_ELEMENTS_CARACTERES, _dans_le_budget, _tous_les_elements, sans_references

MAX_PHRASES = 4
# 110 demandés ; au-delà de 160, un second essai. Entre les deux, quatre
# phrases restent lisibles, et un appel de plus pour raccourcir coûte sans
# rien apporter (mesuré sur Mistral Large : 105 à 150 mots).
MOTS_MAX = 160

PROMPT_RESUME = (
    "Tu rédiges le résumé d'un dossier de procédure pénale française pour un avocat qui "
    "l'ouvre pour la première fois : 3 ou 4 phrases courtes, 110 mots au total au plus. "
    "Tu disposes UNIQUEMENT d'éléments déjà extraits du dossier, chacun précédé de son "
    "numéro entre crochets (F = fait, P = acte de procédure, D = déclaration de la "
    "personne défendue) et terminé par sa citation exacte entre « », qui fait foi. "
    "« [pièce du …] » est la date de la pièce qui rapporte le fait, pas forcément celle "
    "de l'événement : la date d'un événement est celle que donne sa citation. "
    "Dans l'ordre : ce qui a déclenché l'enquête et quand ; la situation procédurale de "
    "la personne défendue (interpellation, garde à vue, stade actuel) ; ce qu'elle "
    "déclare, reconnaît ou conteste — si elle a varié d'une audition à l'autre, donne "
    "chaque version, sans trancher. Ne détaille ni les constatations ni les témoignages : "
    "ils figurent dans la chronologie et l'onglet des contradictions. "
    "Chaque phrase s'appuie sur un ou plusieurs éléments et indique leurs numéros dans "
    "« sources » ; elle ne contient rien — nom, date, heure, durée, chiffre, lieu, "
    "véhicule — qui ne figure dans les éléments qu'elle cite. Ne prête jamais à une "
    "pièce ou à une personne un détail qui vient d'une autre. "
    "Reprends heures, durées et dates telles qu'elles sont écrites, sans les arrondir ni "
    "les convertir ; reformule, sans recopier de citation entre guillemets ni écrire de "
    "numéro d'élément dans le texte. "
    "N'écris jamais « violences », « vol », « plainte », « réquisitoire » ou tout autre nom "
    "d'infraction ou d'acte de procédure qui ne figure pas dans les éléments cités. "
    "Écris chaque nom exactement comme dans la liste des personnes (NOM en capitales) et "
    "respecte le rôle indiqué pour chacune ; jamais d'élément entre crochets. "
    "Aucune qualification juridique, aucune appréciation sur la culpabilité, la "
    "sincérité, la solidité des charges, la régularité des actes ou la stratégie de "
    "défense ; jamais les mots « nullité » ou « irrégularité ». "
    'Réponds uniquement en JSON : {"phrases": [{"texte": "...", "sources": ["F3", "D7"]}]}.'
)


def _bloc_client(personnes: list[sqlite3.Row]) -> str:
    """« Votre client » pour la personne que l'avocat a lui-même désignée.
    À défaut, seulement s'il n'y a qu'UN mis en cause : avec plusieurs
    coauteurs, l'outil ne peut pas savoir lequel l'avocat défend, et le
    deviner ferait dire au résumé l'inverse de la réalité."""
    client = next((p["nom"] for p in personnes if p["est_client"]), None)
    if client:
        return f"L'avocat défend {client} : désigne cette personne comme « votre client, {client} »."
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


def defauts_de_forme(texte: str) -> list[str]:
    defauts = []
    mots = len(texte.split())
    if mots > MOTS_MAX:
        defauts.append(f"{mots} mots, pour 110 au plus")
    if "«" in texte or "»" in texte:
        defauts.append("citations recopiées entre guillemets : reformule")
    return defauts


def _elements_du_resume(
    db: sqlite3.Connection, personnes: list[sqlite3.Row], console: Console | None = None,
) -> dict[str, dict]:
    """Faits et actes vérifiés, et déclarations vérifiées du client désigné
    (à défaut, des mises en cause) — chacun avec sa date et sa citation.

    La citation est indispensable : pour une audition, le « point factuel »
    n'est souvent que le sujet de la question (« heure d'arrivée sur les
    lieux »). Sans la réponse elle-même, le modèle l'inventait — observé en
    réel : « arrivé vers 21h00 » pour un client qui a dit 22 h puis 23 h 30."""
    filtre = "pe.est_client = 1" if any(p["est_client"] for p in personnes) else "pe.role = 'mis_en_cause'"
    declarations_retenues = {
        f"D{r['id']}" for r in db.execute(
            f"""SELECT d.id FROM declarations d JOIN personnes pe ON pe.id = d.personne_id
                WHERE d.statut_verif = 'verifie' AND {filtre}"""
        )
    }
    tous = _tous_les_elements(db)
    retenus = {c: e for c, e in tous.items() if c[0] in "FP" or c in declarations_retenues}
    return _dans_le_budget(retenus, BUDGET_ELEMENTS_CARACTERES, console)


def _examiner(
    reponse_texte: str, elements: dict[str, dict], bloc_client: str,
    discordances: list[tuple[str, list[str]]] = (), noms: list[str] = (),
) -> tuple[list[str], list[str]]:
    """Renvoie (phrases retenues, motifs de rejet des autres)."""
    try:
        phrases = extraire_json(reponse_texte).get("phrases", [])
    except (ValueError, AttributeError):
        return [], ["réponse sans JSON lisible"]
    retenues, motifs = [], []
    for phrase in phrases if isinstance(phrases, list) else []:
        if not isinstance(phrase, dict):
            continue
        texte = sans_references(re.sub(r"\s+", " ", str(phrase.get("texte") or "")).strip(), elements)
        sources = [s for s in dict.fromkeys(str(x) for x in phrase.get("sources") or []) if s in elements]
        if not texte:
            continue
        if not sources:
            motifs.append(f"« {texte} » : aucune source")
            continue
        # « votre client, NOM » est une consigne : le nom est une référence.
        reference = avec_noms_completes(
            "\n".join([bloc_client] + [f"p. {elements[s]['page']} {elements[s]['ligne']}" for s in sources]), noms,
        )
        problemes = problemes_redaction(texte, reference) + versions_tues(texte, discordances)
        if problemes:
            motifs.append(f"« {texte} » : {' ; '.join(problemes)} (sources citées : {', '.join(sources)})")
        else:
            retenues.append(texte)
    return retenues[:MAX_PHRASES], motifs


def generer_resume(db: sqlite3.Connection, config: Config, console: Console) -> None:
    if config.offline:
        console.print("  [build] --offline actif : pas de résumé (nécessite un appel au modèle).")
        return

    personnes = db.execute("SELECT nom, role, est_client FROM personnes ORDER BY role, nom").fetchall()
    elements = _elements_du_resume(db, personnes, console)
    if not any(c[0] == "F" for c in elements):
        console.print("  [build] aucun fait vérifié en base — pas de résumé généré.")
        return

    bloc_client = _bloc_client(personnes)
    discordances = discordances_connues(db)
    noms = noms_identifies(db)
    bloc_personnes = "\n".join(f"- {p['nom']} ({p['role']})" for p in personnes) or "Aucune personne identifiée."
    prompt = (
        f"{bloc_client}\n\nPersonnes identifiées :\n{bloc_personnes}\n\nÉléments :\n"
        + "\n".join(f"[{cle}] (p. {e['page']}) {e['ligne']}" for cle, e in elements.items())
        + bloc_discordances(discordances)
    )

    debut = datetime.now(timezone.utc)
    provider = obtenir_provider(config)
    tokens_in = tokens_out = 0
    meilleures: list[str] = []
    motifs: list[str] = []
    # Deux essais au plus. Un rejet l'est pour une raison précise (une heure
    # absente des sources citées, un détail prêté à la mauvaise pièce), que
    # le modèle corrige presque toujours quand on la lui donne. La forme
    # (longueur, citations recopiées) déclenche aussi le second essai, mais
    # n'écarte jamais des phrases fidèles. On garde le meilleur des deux.
    for essai in range(2):
        consigne = prompt if essai == 0 else (
            f"{prompt}\n\nTa proposition précédente a été en partie écartée :\n"
            + "\n".join(f"- {m}" for m in motifs)
            + "\nRédige de nouveau le résumé complet en corrigeant ces points, sans rien "
            "ajouter qui ne figure dans les éléments cités par chaque phrase. Réponds "
            'uniquement en JSON : {"phrases": [{"texte": "...", "sources": ["F3"]}]}.'
        )
        try:
            reponse = provider.appeler(systeme=PROMPT_RESUME, prompt=consigne, modele=config.modele_analyse)
        except ErreurModeOffline:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [build] échec de la génération du résumé ({exc}).")
            break
        tokens_in += reponse.tokens_in
        tokens_out += reponse.tokens_out
        retenues, rejets = _examiner(reponse.texte, elements, bloc_client, discordances, noms)
        motifs = rejets + defauts_de_forme(" ".join(retenues))
        if len(retenues) >= len(meilleures):
            meilleures = retenues
        if not motifs:
            break
        console.print(f"  [build] résumé (essai {essai + 1}/2) : {len(retenues)} phrase(s) retenue(s), écarté : {' | '.join(motifs)}.")

    if not meilleures:
        console.print("  [build] aucun résumé retenu.")
        return
    texte = " ".join(meilleures)

    maintenant = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO resume_affaire (id, texte, genere_le) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET texte = excluded.texte, genere_le = excluded.genere_le",
        (texte, maintenant),
    )

    fin = datetime.now(timezone.utc)
    cout = config.cout(config.modele_analyse, tokens_in, tokens_out)
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin, tokens_in, tokens_out, cout_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("build", "termine", debut.isoformat(), fin.isoformat(), tokens_in, tokens_out, cout),
    )
    db.commit()
