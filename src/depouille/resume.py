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
from .garde_fous import problemes_redaction
from .llm import ErreurModeOffline, obtenir_provider
from .resume_detaille import _elements

MAX_FAITS, MAX_ACTES, MAX_DECLARATIONS = 100, 40, 40

PROMPT_RESUME = (
    "Tu rédiges le résumé factuel d'un dossier de procédure pénale française, pour un "
    "avocat qui l'ouvre pour la première fois. Un seul paragraphe de 3 à 5 phrases, 110 "
    "mots au maximum, sans titre, sans liste, sans guillemets ni préambule. "
    "Dans l'ordre : ce qui s'est passé et quand ; les personnes en cause et leur rôle tel "
    "qu'il apparaît dans le dossier ; le stade de la procédure ; puis, s'il y a des "
    "déclarations de la personne défendue (à défaut, de la personne mise en cause), ce "
    "qu'elle reconnaît et ce qu'elle conteste. "
    "Chaque élément fourni se termine par sa citation exacte entre « » : c'est elle qui "
    "fait foi. Reprends heures, durées, dates et chiffres tels qu'ils y sont écrits, sans "
    "les arrondir ni les convertir, mais reformule : ne recopie aucune citation entre "
    "guillemets dans le paragraphe. Si des éléments divergent (une heure, une couleur) "
    "ou si une personne a varié d'une audition à l'autre, donne chaque version, sans "
    "trancher. "
    "Décris les faits avec les mots des éléments fournis : n'écris jamais « violences », "
    "« vol », « plainte », « réquisitoire » ou tout autre nom d'infraction ou d'acte de "
    "procédure qui n'y figure pas. Une qualification pénale ne peut être mentionnée que "
    "si un élément la cite, en l'attribuant à la pièce qui la retient. "
    "Écris les dates comme dans les éléments (14/03/2031) ; n'écris de période que si ses "
    "deux bornes diffèrent. "
    "Écris chaque nom exactement comme dans la liste des personnes (NOM en capitales) et "
    "respecte le rôle indiqué pour chacune. N'écris jamais d'élément entre crochets "
    "(« [adresse] ») : omets plutôt ce que tu ne connais pas. "
    "Base-toi UNIQUEMENT sur les personnes et éléments fournis — n'ajoute, ne déduis et "
    "n'invente rien. "
    "Jamais d'appréciation sur la culpabilité, la sincérité, la solidité des charges, la "
    "régularité des actes ou la stratégie de défense ; jamais les mots « nullité » ou "
    "« irrégularité »."
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


MOTS_MAX = 140  # 110 demandés, avec une marge


def defauts_de_forme(texte: str) -> list[str]:
    defauts = []
    mots = len(texte.split())
    if mots > MOTS_MAX:
        defauts.append(f"{mots} mots, pour 110 au plus")
    if "«" in texte or "»" in texte:
        defauts.append("citations recopiées entre guillemets : reformule")
    return defauts


def _elements_du_resume(
    db: sqlite3.Connection, personnes: list[sqlite3.Row]
) -> tuple[list[str], list[str], list[str]]:
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
    elements = _elements(db)
    faits = [e["ligne"] for cle, e in elements.items() if cle[0] == "F"][:MAX_FAITS]
    actes = [e["ligne"] for cle, e in elements.items() if cle[0] == "P"][:MAX_ACTES]
    declarations = [e["ligne"] for cle, e in elements.items() if cle in declarations_retenues][:MAX_DECLARATIONS]
    return faits, actes, declarations


def generer_resume(db: sqlite3.Connection, config: Config, console: Console) -> None:
    if config.offline:
        console.print("  [build] --offline actif : pas de résumé (nécessite un appel au modèle).")
        return

    personnes = db.execute("SELECT nom, role, est_client FROM personnes ORDER BY role, nom").fetchall()
    faits, actes, declarations = _elements_du_resume(db, personnes)
    if not faits:
        console.print("  [build] aucun fait vérifié en base — pas de résumé généré.")
        return

    bloc_personnes = "\n".join(f"- {p['nom']} ({p['role']})" for p in personnes) or "Aucune personne identifiée."
    prompt = (
        f"{_bloc_client(personnes)}\n\n"
        f"Personnes identifiées :\n{bloc_personnes}\n\n"
        "Faits établis :\n" + "\n".join(f"- {f}" for f in faits) + "\n\n"
        "Actes de procédure :\n" + ("\n".join(f"- {a}" for a in actes) or "Aucun acte vérifié.") + "\n\n"
        "Déclarations vérifiées de la personne défendue (ou des mises en cause) :\n"
        + ("\n".join(f"- {d}" for d in declarations) or "Aucune déclaration vérifiée.")
    )

    debut = datetime.now(timezone.utc)
    provider = obtenir_provider(config)
    tokens_in = tokens_out = 0
    texte, problemes = "", []
    # Deux essais au plus : un résumé écarté l'est pour une raison précise
    # (une heure absente des sources, un mot d'infraction inventé), que le
    # modèle corrige presque toujours quand on la lui donne. Au-delà, mieux
    # vaut aucun résumé : l'avocat garde la chronologie et les autres onglets.
    #
    # La forme (longueur, citations recopiées) déclenche aussi le second
    # essai, mais n'écarte jamais un résumé fidèle : mieux vaut un résumé un
    # peu long que pas de résumé. Observé sur Mistral Large : 200 mots
    # entrecoupés de citations, pour 110 demandés.
    defauts: list[str] = []
    for essai in range(2):
        consigne = prompt if essai == 0 else (
            f"{prompt}\n\nTa proposition précédente a été écartée :\n« {texte} »\n"
            f"Motif : {' ; '.join(problemes + defauts)}. Réécris le paragraphe en corrigeant "
            "ces points, sans rien ajouter qui ne figure dans les éléments fournis."
        )
        try:
            reponse = provider.appeler(systeme=PROMPT_RESUME, prompt=consigne, modele=config.modele_analyse)
        except ErreurModeOffline:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [build] échec de la génération du résumé ({exc}).")
            return
        tokens_in += reponse.tokens_in
        tokens_out += reponse.tokens_out
        texte = reponse.texte.strip().strip('"').strip()
        if not texte:
            return
        problemes = problemes_redaction(texte, prompt)
        defauts = defauts_de_forme(texte)
        if not problemes and (not defauts or essai == 1):
            break
        console.print(f"  [build] résumé écarté (essai {essai + 1}/2) : {' ; '.join(problemes + defauts)}.")
    if problemes:
        return

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
