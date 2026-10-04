"""« Interroger le dossier » : l'avocat pose une question en langage courant,
l'outil répond à partir des pièces — et seulement d'elles.

Même contrat que le reste de l'outil, appliqué à une réponse libre :
- le modèle ne lit que des pages du dossier, jamais rien d'autre ;
- chaque citation qu'il avance est vérifiée au caractère près dans la page
  annoncée (même vérification que la chronologie) ; une citation
  introuvable est écartée ;
- une réponse sans AUCUNE citation vérifiée n'est pas affichée : on répond
  que le dossier ne permet pas de répondre, plutôt que de laisser passer une
  affirmation que rien ne permet de contrôler ;
- aucune qualification juridique : si la réponse du modèle en contient
  malgré la consigne, son texte est retiré et seuls les passages cités sont
  rendus. De même si elle avance un élément (heure, nom, durée…) absent
  des pages qu'elle cite, ou porte un jugement. Ce filtre est déterministe
  — il ne dépend pas de la bonne volonté du modèle.
"""

from __future__ import annotations

import math
import re
import sqlite3
import unicodedata

from .config import Config
from .garde_fous import RE_QUALIFICATION, problemes_redaction
from .llm import ErreurModeOffline, extraire_json, obtenir_provider
from .verification import verifier_citation

# Budget de texte soumis au modèle par question (~10 000 tokens). Un petit
# dossier y tient en entier ; au-delà, on ne soumet que les pages les plus
# pertinentes pour la question.
BUDGET_CARACTERES = 40_000
LONGUEUR_MAX_QUESTION = 500

MOTS_VIDES = frozenset(
    """le la les un une des du de d l au aux et ou en dans sur sous par pour avec sans
    que qui quoi quel quelle quels quelles dont est sont a ont été etre être avoir fait
    il elle ils elles on se sa son ses leur leurs ce cet cette ces y ne pas plus
    comment pourquoi quand combien lui eux nous vous je tu me te mon ma mes votre vos
    notre nos t il-y dossier piece pieces page pages""".split()
)


MESSAGE_INTROUVABLE = (
    "Le dossier ne permet pas de répondre à cette question avec un passage vérifiable."
)
MESSAGE_QUALIFICATION = (
    "Je ne qualifie pas juridiquement les éléments du dossier. "
    "Voici les passages des pièces qui se rapportent à votre question."
)
MESSAGE_PASSAGES = "Voici les passages des pièces qui se rapportent à votre question."

PROMPT_SYSTEME = (
    "Tu réponds à la question d'un avocat pénaliste sur le dossier de procédure dont "
    "les pages te sont fournies, chacune précédée de « [page N] ». "
    "Réponds UNIQUEMENT à partir de ces pages : n'utilise aucune connaissance extérieure, "
    "ne déduis rien qui n'y soit pas écrit, n'invente rien. "
    "Appuie chaque élément de ta réponse sur au moins une citation EXACTE, recopiée mot "
    "pour mot depuis une page, avec son numéro de page. "
    "Si les pages ne permettent pas de répondre, réponds {\"reponse\": null, \"citations\": []}. "
    "Ne formule JAMAIS de qualification juridique (nullité, irrégularité, grief, qualité à "
    "agir, légalité d'un acte), ni d'appréciation sur la culpabilité, la solidité des "
    "charges ou la stratégie de défense : si la question le demande, rapporte seulement "
    "les faits datés et sourcés qui s'y rapportent, sans conclure. "
    "Si les pages divergent sur un point (heure, date, couleur, description d'une "
    "personne), donne chaque version avec sa page et cite chacune, sans trancher. "
    "Réponse courte : quelques phrases au plus, en français. "
    "Réponds uniquement en JSON : "
    "{\"reponse\": \"...\", \"citations\": [{\"page\": N, \"citation\": \"...\"}]}."
)


def _normaliser(texte: str) -> str:
    forme = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in forme if not unicodedata.combining(c))


def _termes(texte: str) -> set[str]:
    return {m for m in re.findall(r"[a-z0-9]+", _normaliser(texte)) if len(m) >= 3 and m not in MOTS_VIDES}


def pages_pertinentes(pages: list, question: str, budget: int = BUDGET_CARACTERES) -> list:
    """Sélectionne les pages à soumettre au modèle.

    Un dossier qui tient dans le budget est soumis EN ENTIER : c'est le cas le
    plus sûr, rien ne peut échapper au modèle. Au-delà, les pages sont
    classées par les mots de la question qu'elles contiennent, pondérés par
    leur rareté dans le dossier (un nom propre compte plus que « audition »),
    puis rendues dans l'ordre du dossier."""
    if sum(len(p["texte"]) for p in pages) <= budget:
        return list(pages)

    termes = _termes(question)
    termes_par_page = [_termes(p["texte"]) for p in pages]
    n = len(pages)
    poids = {t: math.log((n + 1) / (1 + sum(t in tp for tp in termes_par_page))) + 1 for t in termes}
    scores = [sum(poids[t] for t in termes if t in tp) for tp in termes_par_page]

    retenues, taille = [], 0
    for i in sorted(range(n), key=lambda i: -scores[i]):
        if scores[i] <= 0:
            break
        longueur = len(pages[i]["texte"])
        if taille + longueur > budget:
            continue
        retenues.append(i)
        taille += longueur
    return [pages[i] for i in sorted(retenues)]


def repondre_question(db: sqlite3.Connection, config: Config, question: str) -> dict:
    """Renvoie {"statut", "reponse", "citations"} :
    - "sourcee" : réponse du modèle + citations toutes vérifiées ;
    - "passages" : qualification retirée, seules les citations vérifiées restent ;
    - "introuvable" : aucune citation vérifiable, pas de réponse affichée."""
    question = question.strip()[:LONGUEUR_MAX_QUESTION]
    introuvable = {"statut": "introuvable", "reponse": MESSAGE_INTROUVABLE, "citations": []}
    if not question:
        return introuvable
    if config.offline:
        raise ErreurModeOffline("Interroger le dossier nécessite un appel au modèle.")

    pages = db.execute("SELECT numero_global, texte, ocr_applique FROM pages ORDER BY numero_global").fetchall()
    selection = pages_pertinentes(pages, question)
    if not selection:
        return introuvable

    texte = "\n".join(f"[page {p['numero_global']}]\n{p['texte']}" for p in selection)
    # « Mon client » n'a de sens que si l'avocat a désigné la personne qu'il
    # défend ; sinon le modèle n'a pas à le deviner.
    client = db.execute("SELECT nom FROM personnes WHERE est_client = 1 LIMIT 1").fetchone()
    contexte_client = (
        f"L'avocat défend {client['nom']} : « mon client » désigne cette personne.\n\n"
        if client else
        "L'avocat n'a pas désigné son client : si la question parle de « mon client », "
        "réponds {\"reponse\": null, \"citations\": []}.\n\n"
    )
    reponse_modele = obtenir_provider(config).appeler(
        systeme=PROMPT_SYSTEME,
        prompt=f"{contexte_client}Question de l'avocat : {question}\n\nPages du dossier :\n{texte}",
        modele=config.modele_analyse,
    )
    try:
        donnees = extraire_json(reponse_modele.texte)
    except ValueError:
        return introuvable
    if not isinstance(donnees, dict):
        return introuvable

    pages_par_numero = {p["numero_global"]: p for p in pages}
    citations_verifiees = []
    for c in donnees.get("citations") or []:
        if not isinstance(c, dict):
            continue
        page = pages_par_numero.get(c.get("page"))
        citation = (c.get("citation") or "").strip()
        if page is None or not citation:
            continue
        valide, _ = verifier_citation(page["texte"], citation, bool(page["ocr_applique"]), config.seuil_flou_ocr)
        if valide:
            citations_verifiees.append({"page": page["numero_global"], "citation": citation})

    texte_reponse = (donnees.get("reponse") or "").strip()
    if not citations_verifiees or not texte_reponse:
        return introuvable
    if RE_QUALIFICATION.search(texte_reponse):
        return {"statut": "passages", "reponse": MESSAGE_QUALIFICATION, "citations": citations_verifiees}
    # Les citations sont vérifiées, mais le texte de la réponse est rédigé :
    # une heure, un nom ou une durée qui ne figure sur aucune des pages
    # citées (un détail venu d'ailleurs, ou de la connaissance du modèle)
    # n'est pas affiché — seuls les passages vérifiés le sont.
    pages_citees = {c["page"] for c in citations_verifiees}
    reference = "\n".join(
        [question, client["nom"] if client else ""]
        + [pages_par_numero[n]["texte"] for n in sorted(pages_citees)]
    )
    if problemes_redaction(texte_reponse, reference):
        return {"statut": "passages", "reponse": MESSAGE_PASSAGES, "citations": citations_verifiees}
    return {"statut": "sourcee", "reponse": texte_reponse, "citations": citations_verifiees}
