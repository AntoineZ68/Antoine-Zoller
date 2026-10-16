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
from .garde_fous import (
    RE_JUGEMENT, _version_citee, avec_noms_completes, contient_qualification, elements_absents, noms_identifies,
)
from .llm import ErreurModeOffline, executer_en_parallele, extraire_json, obtenir_provider
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

# 12 et non 8 : un dossier de 40 pages compte plus de 8 discordances, et le
# modèle n'en retenait pas les mêmes d'un passage à l'autre (ITT 5 ou 8
# jours présente une fois sur deux). La relecture écarte le bruit ajouté.
MAX_PROPOSITIONS_MODELE = 12

# « Couleur du scooter : noir selon le PV, non précisée par la témoin » :
# qu'une pièce se taise n'est pas une discordance (observé, proposé deux
# fois sur deux par le modèle malgré la consigne). Bruit pour l'avocat.
RE_ABSENCE_N_EST_PAS_CONTRADICTION = re.compile(
    r"non\s+pr[ée]cis|ne\s+pr[ée]cise\s+pas|sans\s+pr[ée]ciser|non\s+mentionn|ne\s+mentionne\s+pas|"
    r"non\s+indiqu|n'indique\s+pas|ne\s+dit\s+rien|non\s+renseign",
    re.IGNORECASE,
)



@dataclass
class Contradiction:
    domaine: str  # "procedure" ou "fond"
    titre: str
    description: str
    sources: list[dict] = field(default_factory=list)  # {libelle, page, citation}
    origine: str = "regle"  # "regle" ou "modele"
    # Lignes complètes des éléments cités (sujet de la question compris),
    # pour la relecture seulement — jamais enregistrées ni affichées.
    contexte: list[str] = field(default_factory=list, repr=False)


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


LIBELLES_ACTES = {
    **ACTES_UNIQUES,
    "debut_audition": "Audition",
    "avis_avocat": "Avis à l'avocat",
    "avis_parquet": "Avis au procureur",
    "debut_perquisition": "Perquisition",
}


def _actes_avant_saisine(db: sqlite3.Connection) -> list[Contradiction]:
    """Un acte de procédure horodaté AVANT le PV de saisine qui ouvre
    l'enquête : interpellation à 05h52, saisine du service à 06h00 (observé
    sur un dossier de test). Le modèle ne le relevait qu'un passage sur
    deux ; c'est une comparaison d'heures, elle revient aux règles. Sans
    aucune date dans le dossier, les heures sont comparées sur une même
    journée, et la description le dit."""
    from .chrono import _chercher_ouverture_acte_sur_pages
    from .gardes_a_vue import _instant

    saisine = db.execute(
        "SELECT * FROM pieces WHERE type = 'PV de saisine' ORDER BY page_debut LIMIT 1"
    ).fetchone()
    if saisine is None:
        return []
    pages = db.execute(
        "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
        (saisine["page_debut"], saisine["page_fin"]),
    ).fetchall()
    ouverture = _chercher_ouverture_acte_sur_pages(pages)
    if ouverture is None or not ouverture[2]:
        return []
    page_s, date_s, heure_s, citation_s = ouverture
    actes = db.execute(
        f"""SELECT e.nature, e.date, e.heure, e.page, e.citation, pi.type AS type_piece, pi.cote
            FROM evenements_procedure e LEFT JOIN pieces pi ON pi.id = e.piece_id
            WHERE e.statut_verif = 'verifie' AND e.heure IS NOT NULL
              AND e.nature IN ({",".join("?" * len(LIBELLES_ACTES))})
            ORDER BY e.page""",
        tuple(LIBELLES_ACTES),
    ).fetchall()
    sans_date = not date_s and all(not a["date"] for a in actes)
    if not sans_date and not date_s:
        return []
    jour = "01/01/2000" if sans_date else None
    debut = _instant(jour or date_s, heure_s)
    libelle_s = _libelle_piece(saisine["type"], saisine["cote"])
    resultats, vus = [], set()
    for acte in actes:
        instant = _instant(jour or acte["date"], acte["heure"])
        if instant is None or debut is None or instant >= debut or acte["nature"] in vus:
            continue
        vus.add(acte["nature"])
        libelle_a = _libelle_piece(acte["type_piece"], acte["cote"])
        resultats.append(Contradiction(
            domaine="procedure",
            titre=f"{LIBELLES_ACTES[acte['nature']]} à {acte['heure']}, avant la saisine à {heure_s}",
            description=(
                f"{libelle_a} : {_moment(acte['date'], acte['heure'], True)} ; {libelle_s} : "
                f"{_moment(date_s, heure_s, True)}. Un acte placé avant la saisine qui ouvre l'enquête — "
                "erreur de plume ou chronologie à reconstituer, à vérifier."
                + (" Les pièces ne portent pas de date : heures comparées sur une même journée." if sans_date else "")
            ),
            sources=[
                {"libelle": libelle_s, "page": page_s, "citation": citation_s},
                {"libelle": libelle_a, "page": acte["page"], "citation": acte["citation"]},
            ],
        ))
    return resultats


NUMEROS_EN_LETTRES = {
    "UN": 1, "DEUX": 2, "TROIS": 3, "QUATRE": 4, "CINQ": 5, "SIX": 6, "SEPT": 7, "HUIT": 8, "NEUF": 9, "DIX": 10,
    "ONZE": 11, "DOUZE": 12, "TREIZE": 13, "QUATORZE": 14, "QUINZE": 15, "SEIZE": 16,
}
# « DERRADJI-DEUX » (nom du mis en cause et numéro en lettres, usage police)
# ou « scellé n° 3 ».
RE_SCELLE = re.compile(
    r"\b([A-Z]{3,})-(" + "|".join(NUMEROS_EN_LETTRES) + r"|\d{1,2})\b|\bscell[ée]\s+n[°o]\s*(\d{1,3})\b",
    re.IGNORECASE,
)
COULEURS = ("gris", "noir", "blanc", "bleu", "rouge", "vert", "jaune", "marron", "beige", "rose", "violet",
            "orange", "kaki", "bordeaux", "argent", "dor[ée]")
RE_COULEUR = re.compile(r"\b(" + "|".join(COULEURS) + r")(?:e|s|es)?\b", re.IGNORECASE)
RE_MESURE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(cm|mm)\b", re.IGNORECASE)


def _description_scelle(texte: str, fin: int) -> tuple[str, int]:
    """Ce qui décrit le scellé : la parenthèse qui suit son numéro, sinon la
    fin de la ligne (tableau des scellés) ; et où s'arrête le passage."""
    reste = texte[fin:]
    m = re.match(r"\s*\(([^)\n]{3,150})\)", reste)
    if m:
        return m.group(1).strip(), fin + m.end()
    ligne = reste.split("\n", 1)[0][:150]
    return ligne.strip(" .:;—-"), fin + len(ligne.rstrip())


def _scelles_decrits_differemment(db: sqlite3.Connection) -> list[Contradiction]:
    """Le même scellé décrit avec une autre couleur ou une autre mesure d'une
    pièce à l'autre : « DERRADJI-DEUX Sweat-shirt à capuche gris » au PV de
    perquisition, « Scellé DERRADJI-DEUX (sweat-shirt noir à capuche) » au
    rapport d'exploitation (observé sur un dossier de test ; le modèle ne le
    relevait pas). Erreur de plume ou objet différent : l'intégrité du
    scellé est à vérifier."""
    from .classify import _sans_accents
    from .db import pages_hors_procedure

    exclues = pages_hors_procedure(db)
    pages_ocr = {r[0] for r in db.execute("SELECT numero_global FROM pages WHERE ocr_applique = 1")}
    par_scelle: dict[tuple[str, int], list[tuple[int, str, str, str]]] = {}
    for page in db.execute("SELECT numero_global, texte FROM pages ORDER BY numero_global"):
        if page["numero_global"] in exclues:
            continue
        texte = page["texte"]
        for m in RE_SCELLE.finditer(texte):
            if m.group(3):
                cle = ("", int(m.group(3)))
            else:
                numero = m.group(2).upper()
                # Le nom est souvent mal lu (DERRADJI, DERRADIJI) : ses quatre
                # premières lettres suffisent à distinguer deux séries.
                cle = (_sans_accents(m.group(1)).upper()[:4], NUMEROS_EN_LETTRES.get(numero) or int(numero))
            description, fin = _description_scelle(texte, m.end())
            if description:
                extrait = " ".join(texte[m.start():fin].split())
                par_scelle.setdefault(cle, []).append((page["numero_global"], m.group(0), description, extrait))

    resultats = []
    for occurrences in par_scelle.values():
        for i, (page_a, nom_a, desc_a, extrait_a) in enumerate(occurrences):
            autre = None
            for page_b, _, desc_b, extrait_b in occurrences[i + 1:]:
                if page_b == page_a:
                    continue
                couleurs = [{_sans_accents(c).lower() for c in RE_COULEUR.findall(d)} for d in (desc_a, desc_b)]
                mesures = [{(v.replace(",", "."), u.lower()) for v, u in RE_MESURE.findall(d)} for d in (desc_a, desc_b)]
                ecart = next((n for n, (x, y) in (("couleur", couleurs), ("mesure", mesures)) if x and y and not x & y), None)
                if ecart:
                    autre = (page_b, desc_b, extrait_b, ecart)
                    break
            if not autre:
                continue
            page_b, desc_b, extrait_b, ecart = autre
            description = (
                f"Le même scellé est décrit « {desc_a} » (page {page_a}) puis « {desc_b} » (page {page_b}). "
                "Erreur de description, ou objet différent de celui saisi : l'intégrité du scellé est à vérifier "
                "(article 97 du code de procédure pénale)."
            )
            if page_a in pages_ocr or page_b in pages_ocr:
                description += " L'une des pages est numérisée : relire l'original, la lecture automatique a pu se tromper."
            resultats.append(Contradiction(
                domaine="procedure",
                titre=f"Scellé {nom_a} : {ecart} différente selon les pièces",
                description=description,
                sources=[{"libelle": "", "page": page_a, "citation": extrait_a},
                         {"libelle": "", "page": page_b, "citation": extrait_b}],
            ))
            break
    return resultats


def contradictions_par_regles(db: sqlite3.Connection) -> list[Contradiction]:
    return (_actes_dates_differemment(db) + _actes_avant_saisine(db) + _identifiants_proches(db)
            + _scelles_decrits_differemment(db))


def discordances_connues(db: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    """Les versions de chaque discordance établie par les règles fixes :
    [("Interpellation de Julien MORVANNEC", ["07h50", "08h05"])]. Un résumé
    qui n'en reprend qu'une tranche en silence (voir versions_tues)."""
    discordances = []
    for c in contradictions_par_regles(db):
        libelle, _, versions = c.titre.rpartition(" : ")
        if libelle and " ou " in versions:
            discordances.append((libelle, versions.split(" ou ")))
    return discordances


# --- Propositions du modèle ------------------------------------------------

PROMPT = (
    "Tu aides un avocat pénaliste français à repérer, dans un dossier de procédure, les "
    "passages qui ne concordent pas d'une pièce à l'autre. Tu disposes UNIQUEMENT "
    "d'éléments déjà extraits du dossier, chacun précédé de son numéro entre crochets "
    "(F = fait, P = acte de procédure, D = déclaration) et de sa page. "
"« [pièce du …] » est la date de la pièce qui rapporte un fait, pas celle de "
    "l'événement : ne relève jamais un écart entre cette date de pièce et la date d'un "
    "événement qu'elle raconte (une synthèse du 26/09 qui raconte un appel du 01/09). "
    "Relève au plus " + str(MAX_PROPOSITIONS_MODELE) + " discordances CONCRÈTES entre "
    "éléments de pages différentes portant sur le même fait : heure, date, lieu, "
    "véhicule, couleur, description d'une personne, présence de quelqu'un, montant, "
    "déroulé — y compris entre l'arme, le vêtement ou le véhicule décrits par la victime ou "
    "un témoin et l'objet saisi que l'enquête leur rattache (longueur de lame, couleur). "
    "N'en relève pas pour une simple dénégation opposée à une accusation, ni "
    "pour une différence de point de vue sans détail vérifiable, ni quand l'un des "
    "éléments ne dit rien du point (« non précisé » n'est pas une contradiction), ni "
    "pour un simple écart d'approximation (« 23h05 » et « vers 23 heures »), ni entre "
    "deux choses distinctes (une enveloppe et une somme saisie), ni quand les éléments "
    "concordent en réalité (« une jeune femme » selon la vidéo et « Lorène » selon un "
    "témoin disent la même chose) : les deux versions doivent s'exclure. "
    "Titre court et factuel (ex. « Couleur du véhicule : blanc selon le témoin, gris "
    "selon le PV de surveillance »), description en une ou deux phrases neutres. "
    "N'écris aucun nom, lieu, date ou chiffre qui ne figure pas dans les éléments cités, "
    "ni aucun numéro d'élément (F3, D7) dans le titre ou la description : ils vont dans "
    "« sources » seulement. "
    "Aucune qualification juridique, aucun jugement sur la sincérité, la culpabilité ou "
    "la valeur probante. domaine : \"procedure\" si la discordance porte sur un acte de "
    "procédure, \"fond\" sinon. "
    'Réponds uniquement en JSON : {"contradictions": [{"titre": "...", "description": '
    '"...", "domaine": "fond", "sources": ["F3", "D7"]}]}. Liste vide si aucune.'
)


def _douze_heures_d_ecart(titre: str) -> bool:
    """« vers 23h selon sa mère, vers 11h selon sa déclaration » : la mère
    avait dit « vers onze heures », le soir — la même heure (observé sur un
    dossier de test, accepté par la relecture)."""
    heures = {(int(h), int(m or 0)) for h, m in re.findall(r"\b(\d{1,2})\s*h\s*(\d{2})?\b", titre)}
    return any((h + 12, m) in heures for h, m in heures)


def proposition_retenue(proposition: dict, elements: dict[str, dict], noms: list[str] = ()) -> Contradiction | None:
    if not isinstance(proposition, dict):
        return None
    titre = sans_references(re.sub(r"\s+", " ", str(proposition.get("titre") or "")).strip(), elements)
    description = sans_references(re.sub(r"\s+", " ", str(proposition.get("description") or "")).strip(), elements)
    sources = [s for s in dict.fromkeys(str(x) for x in proposition.get("sources") or []) if s in elements]
    if not titre or not description or len(sources) < 2:
        return None
    if len({elements[s]["page"] for s in sources}) < 2:
        return None
    # Séparés par un point : collés, « …selon le PV de découverte La victime
    # déclare… » faisait de « La » un nom propre inconnu, et écartait quatre
    # discordances exactes sur un dossier d'essai (plaque, sac, heure…).
    texte = f"{titre}. {description}"
    if contient_qualification(texte) or RE_JUGEMENT.search(texte):
        return None
    if RE_ABSENCE_N_EST_PAS_CONTRADICTION.search(texte):
        return None
    if _douze_heures_d_ecart(titre):
        return None
    if elements_absents(texte, avec_noms_completes("\n".join(f"p. {elements[s]['page']} {elements[s]['ligne']}" for s in sources), noms)):
        return None
    domaine = proposition.get("domaine") if proposition.get("domaine") in ("procedure", "fond") else "fond"
    return Contradiction(
        domaine=domaine,
        titre=titre,
        description=description,
        sources=[{"libelle": elements[s].get("libelle", ""), "page": elements[s]["page"], "citation": elements[s]["citation"]} for s in sources],
        origine="modele",
        contexte=[elements[s]["ligne"] for s in sources],
    )


def generer_contradictions(db: sqlite3.Connection, config: Config, console: Console) -> int:
    """Remplace en base les contradictions proposées par le modèle. Renvoie
    le nombre retenu. Les contradictions par règles ne sont pas stockées :
    elles se recalculent à l'affichage."""
    db.execute("DELETE FROM contradictions")
    db.commit()
    if config.offline:
        return 0
    elements = _elements(db, console)
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

    noms = noms_identifies(db)
    retenues = [c for c in (proposition_retenue(p, elements, noms) for p in (propositions if isinstance(propositions, list) else [])) if c]
    nb_avant_relecture = len(retenues)
    retenues = relire(retenues[:MAX_PROPOSITIONS_MODELE], config, console)
    for ordre, c in enumerate(retenues, 1):
        db.execute(
            "INSERT INTO contradictions (ordre, domaine, titre, description, sources_json) VALUES (?, ?, ?, ?, ?)",
            (ordre, c.domaine, c.titre, c.description, json.dumps(c.sources, ensure_ascii=False)),
        )
    db.commit()
    console.print(
        f"  [build] contradictions : {len(retenues)}/{len(propositions) if isinstance(propositions, list) else 0} "
        f"proposition(s) retenue(s), dont {nb_avant_relecture - len(retenues)} écartée(s) à la relecture."
    )
    return len(retenues)


PROMPT_RELECTURE = (
    "Tu relis une discordance proposée entre des passages d'un dossier de procédure pénale "
    "française, avant qu'elle soit montrée à un avocat. Tu ne disposes que des passages "
    "cités, chacun avec son sujet et sa citation exacte entre « ». Réponds uniquement en JSON : {\"fidele\": true|false, "
    "\"contradiction_reelle\": true|false, \"motif\": \"...\"}. "
    "fidele : le titre et la description n'attribuent à aucun passage une information qu'il ne "
    "contient pas (personne, date, chiffre, lieu rattachés au bon passage). "
"« [pièce du …] » est la date de la pièce qui rapporte un fait, pas celle de "
    "l'événement : ne relève jamais un écart entre cette date de pièce et la date d'un "
    "événement qu'elle raconte (une synthèse du 26/09 qui raconte un appel du 01/09). "
    "contradiction_reelle : les passages donnent sur un même point des versions qui s'excluent "
    "— pas un silence, pas un écart d'approximation, pas deux choses distinctes, pas des "
    "versions qui concordent en réalité. L'objet décrit par la victime ou un témoin (arme, "
    "vêtement, véhicule) et l'objet saisi que l'enquête lui rattache portent sur un même point."
)


def relire(contradictions: list[Contradiction], config: Config, console: Console) -> list[Contradiction]:
    """Second appel au modèle, une discordance à la fois, avec ses seules
    citations : les garde-fous déterministes vérifient que chaque élément
    figure dans les sources, pas qu'il est rattaché au BON passage.
    Observé dans l'appli : « Auteur des retraits le 15/01 : Mehdi CARON
    selon la vidéoprotection » — la vidéo attribue ces retraits à « une
    jeune femme », Mehdi CARON n'y figure que pour d'autres jours. Une
    discordance non confirmée (ou dont la relecture échoue) n'est pas
    montrée : une fausse contradiction égare l'avocat plus qu'une absente."""
    if not contradictions:
        return []
    provider = obtenir_provider(config)

    def verifier(c: Contradiction) -> bool:
        # La ligne de l'élément porte le sujet de la question : « Kevin. »
        # seul ne dit pas qu'il répond à « qui a donné les coups de pied ».
        passages = "\n".join(
            f"- {ligne}" for ligne in c.contexte
        ) if c.contexte else "\n".join(f"- p. {s['page']} : « {s['citation']} »" for s in c.sources)
        reponse = provider.appeler(
            systeme=PROMPT_RELECTURE,
            prompt=f"Titre : {c.titre}\nDescription : {c.description}\nPassages :\n{passages}",
            modele=config.modele_analyse,
        )
        avis = extraire_json(reponse.texte)
        return isinstance(avis, dict) and avis.get("fidele") is True and avis.get("contradiction_reelle") is True

    gardees = []
    for c, avis in zip(contradictions, executer_en_parallele(verifier, contradictions)):
        if avis is True:
            gardees.append(c)
        elif isinstance(avis, Exception):
            console.print(f"  [build] relecture impossible, discordance non montrée : {c.titre} ({avis}).")
    return gardees


def _meme_passage(a: dict, b: dict) -> bool:
    """Même page, et l'une des citations contient l'autre : la règle cite
    « Réponse : Le numéro affiché était le 07 81 22 45 63 », le modèle
    « Le numéro affiché était le 07 81 22 45 63 » — c'est le même passage."""
    if a["page"] != b["page"]:
        return False
    ca, cb = (re.sub(r"\s+", " ", c["citation"]).strip().lower() for c in (a, b))
    return ca in cb or cb in ca


def _meme_discordance(sources: list[dict], autres: list[dict]) -> bool:
    """Deux passages au moins en commun : la même discordance, ne
    s'affichant qu'une fois (observé dans l'appli : le téléphone transposé,
    établi par la règle, répété par le modèle avec des citations découpées
    autrement)."""
    return sum(any(_meme_passage(s, a) for a in autres) for s in sources) >= 2


def _meme_ecart(sources: list[dict], texte: str, regle: list[dict], versions: list[str]) -> bool:
    """Mêmes pages, un passage en commun, et les mêmes versions : la règle
    a établi « 06h20 ou 06h10 », le modèle redit « 6h20 selon le PV, 6h10
    selon la notification » en citant, pour la seconde pièce, une autre
    phrase de la même page (observé sur un dossier d'essai : la discordance
    affichée deux fois)."""
    if not versions or {s["page"] for s in sources} != {r["page"] for r in regle}:
        return False
    if not any(_meme_passage(s, r) for s in sources for r in regle):
        return False
    return all(_version_citee(v, texte) for v in versions)


def toutes_les_contradictions(db: sqlite3.Connection) -> list[Contradiction]:
    """Règles fixes d'abord, puis propositions du modèle (table absente sur
    un dossier traité avant cette fonction : règles seules)."""
    resultats = contradictions_par_regles(db)
    # Le modèle peut retrouver une discordance déjà établie par les règles
    # (deux heures d'interpellation) : la même ne s'affiche pas deux fois.
    deja = [c.sources for c in resultats]
    versions_par_regle = []
    for c in resultats:
        _, _, versions = c.titre.rpartition(" : ")
        versions_par_regle.append((c.sources, versions.split(" ou ") if " ou " in versions else []))
    try:
        for ligne in db.execute("SELECT * FROM contradictions ORDER BY ordre"):
            sources = json.loads(ligne["sources_json"])
            if any(_meme_discordance(sources, d) for d in deja):
                continue
            texte = f"{ligne['titre']} {ligne['description']}"
            if any(_meme_ecart(sources, texte, regle, versions) for regle, versions in versions_par_regle):
                continue
            deja.append(sources)
            resultats.append(Contradiction(
                domaine=ligne["domaine"], titre=ligne["titre"], description=ligne["description"],
                sources=sources, origine="modele",
            ))
    except sqlite3.OperationalError:
        pass
    return resultats
