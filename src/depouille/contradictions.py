"""Contradictions entre pièces : des passages qui ne concordent pas d'une
pièce à l'autre, montrés côte à côte, chacun avec sa citation exacte.

Deux origines, dans cet ordre de confiance :

1. Des règles fixes, sans appel au modèle, sur ce qui se compare
   mécaniquement :
   - un même acte (interpellation, placement en garde à vue, notification
     des droits, fin de garde à vue) d'une même personne, daté différemment
     selon les pièces ;
   - un même identifiant (plaque, téléphone) écrit de deux façons qui ne
     diffèrent que d'un caractère.
   Calculées à la volée : elles valent aussi pour les dossiers déjà traités.

2. Le modèle, pour ce qui demande de lire le sens (couleur d'un véhicule
   selon un témoin et selon un autre PV, horaire de travail et horaire des
   faits). Il ne reçoit que des éléments déjà extraits ET vérifiés au
   caractère près, numérotés ; il propose des paires, et une proposition
   n'est retenue que si :
   - elle s'appuie sur au moins deux éléments existants, sur deux pages
     différentes ;
   - son titre et sa description ne contiennent aucun nom, lieu ou nombre
     absent de ces éléments ;
   - elle ne contient ni qualification juridique, ni jugement sur la
     sincérité ou la culpabilité de quiconque.

Une contradiction n'est jamais une conclusion : « à vérifier ». Une erreur
de plume ou de lecture du scan en explique souvent une ; l'avocat juge.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from rich.console import Console

from .config import Config
from .garde_fous import RE_JUGEMENT, contient_qualification, elements_absents
from .llm import ErreurModeOffline, extraire_json, obtenir_provider
from .recoupements import occurrences_identifiants
from .resume_detaille import _elements, sans_references

# Actes qui n'ont lieu qu'une fois par personne et par mesure : deux heures
# différentes pour l'un d'eux, c'est deux pièces qui ne concordent pas. La
# prolongation (il peut y en avoir plusieurs) et les auditions en sont
# exclues.
ACTES_UNIQUES = {
    "interpellation": "Interpellation",
    "placement_garde_a_vue": "Placement en garde à vue",
    "notification_droits": "Notification des droits",
    "fin_garde_a_vue": "Fin de garde à vue",
}
# Identifiants pour lesquels une différence d'un seul caractère mérite
# d'être signalée. Une adresse ou un IBAN se comparent trop mal ainsi.
TYPES_IDENTIFIANTS_PROCHES = ("Plaque d'immatriculation", "Téléphone")

MAX_PROPOSITIONS_MODELE = 8



@dataclass
class Contradiction:
    domaine: str  # "procedure" ou "fond"
    titre: str
    description: str
    sources: list[dict] = field(default_factory=list)  # {libelle, page, citation}
    origine: str = "regle"  # "regle" ou "modele"


def _libelle_piece(type_piece: str | None, cote: str | None) -> str:
    if type_piece and cote:
        return f"{type_piece} ({cote})"
    return type_piece or (f"Cote {cote}" if cote else "Pièce")


def _moment(date: str | None, heure: str | None, avec_date: bool) -> str:
    if avec_date and date:
        return f"{date[:5]} à {heure}" if heure else date[:5]
    return heure or (date[:5] if date else "?")


def _actes_dates_differemment(db: sqlite3.Connection) -> list[Contradiction]:
    lignes = db.execute(
        f"""SELECT e.nature, e.date, e.heure, e.page, e.citation, e.piece_id,
                   pe.nom, pi.type AS type_piece, pi.cote
            FROM evenements_procedure e
            JOIN personnes pe ON pe.id = e.personne_id
            LEFT JOIN pieces pi ON pi.id = e.piece_id
            WHERE e.statut_verif = 'verifie'
              AND e.nature IN ({",".join("?" * len(ACTES_UNIQUES))})
            ORDER BY e.page""",
        tuple(ACTES_UNIQUES),
    ).fetchall()

    groupes: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for ligne in lignes:
        groupes.setdefault((ligne["nature"], ligne["nom"]), []).append(ligne)

    resultats = []
    for (nature, nom), actes in groupes.items():
        # Une valeur par moment distinct ; deux moments ne s'opposent que
        # s'ils diffèrent sur une donnée présente des deux côtés.
        retenus: list[sqlite3.Row] = []
        for acte in actes:
            if not any(
                (acte["heure"] is None or r["heure"] is None or acte["heure"] == r["heure"])
                and (acte["date"] is None or r["date"] is None or acte["date"] == r["date"])
                for r in retenus
            ):
                retenus.append(acte)
        if len(retenus) < 2 or len({r["piece_id"] for r in retenus}) < 2:
            continue
        avec_date = len({r["date"] for r in retenus if r["date"]}) > 1
        moments = [_moment(r["date"], r["heure"], avec_date) for r in retenus]
        libelle = ACTES_UNIQUES[nature]
        detail = " ; ".join(
            f"{_libelle_piece(r['type_piece'], r['cote'])} : {m}" for r, m in zip(retenus, moments)
        )
        resultats.append(Contradiction(
            domaine="procedure",
            titre=f"{libelle} de {nom} : {' ou '.join(moments)}",
            description=f"Le même acte n'est pas daté de la même façon selon les pièces — {detail}. À vérifier.",
            sources=[
                {"libelle": _libelle_piece(r["type_piece"], r["cote"]), "page": r["page"], "citation": r["citation"]}
                for r in retenus
            ],
        ))
    return resultats


def _un_caractere_d_ecart(a: str, b: str) -> bool:
    """Une substitution, ou deux caractères voisins inversés (553 / 535)."""
    if len(a) != len(b) or a == b:
        return False
    ecarts = [i for i in range(len(a)) if a[i] != b[i]]
    if len(ecarts) == 1:
        return True
    return len(ecarts) == 2 and ecarts[1] == ecarts[0] + 1 and a[ecarts[0]] == b[ecarts[1]] and a[ecarts[1]] == b[ecarts[0]]


def _identifiants_proches(db: sqlite3.Connection) -> list[Contradiction]:
    occurrences = occurrences_identifiants(db)
    pages_ocr = {r[0] for r in db.execute("SELECT numero_global FROM pages WHERE ocr_applique = 1")}
    resultats = []
    for type_entite in TYPES_IDENTIFIANTS_PROCHES:
        valeurs = sorted((cle, occ) for (t, cle), occ in occurrences.items() if t == type_entite)
        for i, (cle_a, occ_a) in enumerate(valeurs):
            for cle_b, occ_b in valeurs[i + 1:]:
                if not _un_caractere_d_ecart(cle_a, cle_b):
                    continue
                # Deux numéros voisins sur une même page, c'est une liste
                # (lignes d'une même famille, flotte d'un même parc) : écrits
                # côte à côte, ils sont voulus distincts.
                if {o.page for o in occ_a} & {o.page for o in occ_b}:
                    continue
                a, b = occ_a[0], occ_b[0]
                description = (
                    f"Deux écritures qui ne diffèrent que d'un caractère : « {a.valeur_brute} » "
                    f"(page{'s' if len(occ_a) > 1 else ''} {', '.join(str(o.page) for o in occ_a)}) et "
                    f"« {b.valeur_brute} » (page{'s' if len(occ_b) > 1 else ''} "
                    f"{', '.join(str(o.page) for o in occ_b)}). Erreur matérielle, ou deux "
                    "identifiants distincts — à vérifier."
                )
                if a.page in pages_ocr or b.page in pages_ocr:
                    description += " L'une des pages est numérisée : relire l'original, la lecture automatique a pu se tromper."
                resultats.append(Contradiction(
                    domaine="fond",
                    titre=f"{type_entite} : {a.valeur_brute} ou {b.valeur_brute}",
                    description=description,
                    sources=[
                        # Le badge de page suffit : pas de libellé qui le répète.
                        {"libelle": "", "page": a.page, "citation": a.citation},
                        {"libelle": "", "page": b.page, "citation": b.citation},
                    ],
                ))
    return resultats


def contradictions_par_regles(db: sqlite3.Connection) -> list[Contradiction]:
    return _actes_dates_differemment(db) + _identifiants_proches(db)


# --- Propositions du modèle ------------------------------------------------

PROMPT = (
    "Tu aides un avocat pénaliste français à repérer, dans un dossier de procédure, les "
    "passages qui ne concordent pas d'une pièce à l'autre. Tu disposes UNIQUEMENT "
    "d'éléments déjà extraits du dossier, chacun précédé de son numéro entre crochets "
    "(F = fait, P = acte de procédure, D = déclaration) et de sa page. "
    "Relève au plus " + str(MAX_PROPOSITIONS_MODELE) + " discordances CONCRÈTES entre "
    "éléments de pages différentes portant sur le même fait : heure, date, lieu, "
    "véhicule, couleur, description d'une personne, présence de quelqu'un, montant, "
    "déroulé. N'en relève pas pour une simple dénégation opposée à une accusation, ni "
    "pour une différence de point de vue sans détail vérifiable. "
    "Titre court et factuel (ex. « Couleur du véhicule : blanc selon le témoin, gris "
    "selon le PV de surveillance »), description en une ou deux phrases neutres. "
    "N'écris aucun nom, lieu, date ou chiffre qui ne figure pas dans les éléments cités. "
    "Aucune qualification juridique, aucun jugement sur la sincérité, la culpabilité ou "
    "la valeur probante. domaine : \"procedure\" si la discordance porte sur un acte de "
    "procédure, \"fond\" sinon. "
    'Réponds uniquement en JSON : {"contradictions": [{"titre": "...", "description": '
    '"...", "domaine": "fond", "sources": ["F3", "D7"]}]}. Liste vide si aucune.'
)


def proposition_retenue(proposition: dict, elements: dict[str, dict]) -> Contradiction | None:
    if not isinstance(proposition, dict):
        return None
    titre = sans_references(re.sub(r"\s+", " ", str(proposition.get("titre") or "")).strip(), elements)
    description = sans_references(re.sub(r"\s+", " ", str(proposition.get("description") or "")).strip(), elements)
    sources = [s for s in dict.fromkeys(str(x) for x in proposition.get("sources") or []) if s in elements]
    if not titre or not description or len(sources) < 2:
        return None
    if len({elements[s]["page"] for s in sources}) < 2:
        return None
    texte = f"{titre} {description}"
    if contient_qualification(texte) or RE_JUGEMENT.search(texte):
        return None
    if elements_absents(texte, "\n".join(f"p. {elements[s]['page']} {elements[s]['ligne']}" for s in sources)):
        return None
    domaine = proposition.get("domaine") if proposition.get("domaine") in ("procedure", "fond") else "fond"
    return Contradiction(
        domaine=domaine,
        titre=titre,
        description=description,
        sources=[{"libelle": elements[s].get("libelle", ""), "page": elements[s]["page"], "citation": elements[s]["citation"]} for s in sources],
        origine="modele",
    )


def generer_contradictions(db: sqlite3.Connection, config: Config, console: Console) -> int:
    """Remplace en base les contradictions proposées par le modèle. Renvoie
    le nombre retenu. Les contradictions par règles ne sont pas stockées :
    elles se recalculent à l'affichage."""
    db.execute("DELETE FROM contradictions")
    db.commit()
    if config.offline:
        return 0
    elements = _elements(db)
    if len({e["page"] for e in elements.values()}) < 2:
        return 0

    texte = "\n".join(f"[{cle}] (p. {e['page']}) {e['ligne']}" for cle, e in elements.items())
    try:
        reponse = obtenir_provider(config).appeler(systeme=PROMPT, prompt=texte, modele=config.modele_analyse)
        propositions = extraire_json(reponse.texte).get("contradictions", [])
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001 — sans elles, le reste du dossier est intact
        console.print(f"  [build] contradictions non générées ({exc}).")
        return 0

    retenues = [c for c in map(lambda p: proposition_retenue(p, elements), propositions if isinstance(propositions, list) else []) if c]
    for ordre, c in enumerate(retenues[:MAX_PROPOSITIONS_MODELE], 1):
        db.execute(
            "INSERT INTO contradictions (ordre, domaine, titre, description, sources_json) VALUES (?, ?, ?, ?, ?)",
            (ordre, c.domaine, c.titre, c.description, json.dumps(c.sources, ensure_ascii=False)),
        )
    db.commit()
    console.print(f"  [build] contradictions : {len(retenues)}/{len(propositions) if isinstance(propositions, list) else 0} proposition(s) retenue(s).")
    return len(retenues)


def toutes_les_contradictions(db: sqlite3.Connection) -> list[Contradiction]:
    """Règles fixes d'abord, puis propositions du modèle (table absente sur
    un dossier traité avant cette fonction : règles seules)."""
    resultats = contradictions_par_regles(db)
    # Le modèle peut retrouver une discordance déjà établie par les règles
    # (deux heures d'interpellation) : la même ne s'affiche pas deux fois.
    deja = [{(src["page"], src["citation"]) for src in c.sources} for c in resultats]
    try:
        for ligne in db.execute("SELECT * FROM contradictions ORDER BY ordre"):
            sources = json.loads(ligne["sources_json"])
            cles = {(src["page"], src["citation"]) for src in sources}
            if any(len(cles & d) >= 2 for d in deja):
                continue
            deja.append(cles)
            resultats.append(Contradiction(
                domaine=ligne["domaine"], titre=ligne["titre"], description=ligne["description"],
                sources=sources, origine="modele",
            ))
    except sqlite3.OperationalError:
        pass
    return resultats
