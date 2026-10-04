"""Résumé détaillé : quelques sections, chaque phrase renvoyant à ses pièces.

La source de chaque phrase n'est pas cherchée après coup — c'est la
construction qui la garantit. Le modèle ne reçoit QUE des éléments déjà
extraits ET vérifiés au caractère près (faits, actes de procédure,
déclarations), chacun numéroté (F3, P12, D7). Il rédige ses phrases en
indiquant les numéros des éléments qu'il a utilisés ; la source d'une
phrase, ce sont les citations de ces éléments.

Trois contrôles déterministes, phrase par phrase — une phrase qui échoue
est retirée, le reste du résumé est conservé :
- au moins un numéro d'élément existant ;
- aucun nom, lieu ou nombre absent de ses propres sources (un « 12/02 » ou
  un « Biviers » qui n'y figure pas trahit un détail inventé ou déplacé) ;
- aucune qualification juridique, aucun jugement sur la sincérité ou la
  culpabilité.
"""

from __future__ import annotations

import json
import re
import sqlite3

from rich.console import Console

from .config import Config
from .garde_fous import avec_noms_completes, bloc_discordances, noms_identifies, problemes_redaction, versions_tues
from .llm import ErreurModeOffline, extraire_json, obtenir_provider

SECTIONS = (
    "Les faits",
    "L'enquête",
    "Les interpellations et les mesures",
    "Les déclarations",
    "Les expertises et l'état du dossier",
)
MAX_FAITS, MAX_ACTES, MAX_DECLARATIONS = 120, 80, 80

PROMPT = (
    "Tu rédiges le résumé détaillé d'un dossier de procédure pénale française pour un "
    "avocat. Tu disposes UNIQUEMENT d'éléments déjà extraits du dossier, chacun précédé "
    "de son numéro entre crochets (F = fait, P = acte de procédure, D = déclaration). "
    "Organise le résumé dans ces sections, dans cet ordre, en omettant celles pour "
    "lesquelles aucun élément ne convient : " + " ; ".join(SECTIONS) + ". "
    "Chaque section compte de 1 à 4 phrases factuelles, claires et complètes. Chaque "
    "phrase s'appuie sur un ou plusieurs éléments et indique leurs numéros. "
    "N'écris rien qui ne soit pas dans les éléments cités par la phrase elle-même : "
    "pas de date, de lieu, de nom ou de chiffre venus d'un autre élément ou de ta "
    "connaissance. Désigne les personnes par leur nom. "
    "Quand des éléments divergent sur un même point (heure, date, couleur, description), "
    "la phrase donne chaque version et cite chacun des éléments, sans trancher : "
    "« interpellé à 07h50 selon un PV, à 08h05 selon un autre ». "
    "Aucune qualification juridique, aucune appréciation sur la culpabilité, la "
    "solidité des charges ou la régularité des actes. "
    'Réponds uniquement en JSON : {"sections": [{"titre": "...", "phrases": '
    '[{"texte": "...", "sources": ["F3", "D7"]}]}]}.'
)


def _elements(db: sqlite3.Connection) -> dict[str, dict]:
    """Numérote les éléments vérifiés : {"F3": {page, citation, ligne}}."""
    elements: dict[str, dict] = {}
    # La date d'un fait est celle de sa pièce, comme dans la chronologie :
    # elle fait partie de l'élément, pour qu'une phrase qui la reprend ne
    # soit pas écartée à tort.
    for f in db.execute(
        """SELECT ef.id, ef.page, ef.citation, ef.description, pe.nom,
                  pi.date_apparente AS date, pi.heure_apparente AS heure
           FROM evenements_faits ef
           LEFT JOIN personnes pe ON pe.id = ef.personne_id_source
           LEFT JOIN pieces pi ON pi.id = ef.piece_id
           WHERE ef.statut_verif = 'verifie' ORDER BY ef.page LIMIT ?""", (MAX_FAITS,)
    ):
        quand = " ".join(x for x in (f["date"], f["heure"]) if x)
        qui = f" ({f['nom']})" if f["nom"] else ""
        elements[f"F{f['id']}"] = {
            "page": f["page"], "citation": f["citation"], "libelle": f["nom"] or "",
            "ligne": f"{quand + ' — ' if quand else ''}{f['description']}{qui} — « {f['citation']} »",
        }
    for p in db.execute(
        """SELECT e.id, e.page, e.citation, e.nature, e.date, e.heure, pe.nom
           FROM evenements_procedure e LEFT JOIN personnes pe ON pe.id = e.personne_id
           WHERE e.statut_verif = 'verifie' ORDER BY e.page LIMIT ?""", (MAX_ACTES,)
    ):
        quand = " ".join(x for x in (p["date"], p["heure"]) if x)
        qui = f", {p['nom']}" if p["nom"] else ""
        elements[f"P{p['id']}"] = {
            "page": p["page"], "citation": p["citation"], "libelle": p["nom"] or "",
            "ligne": f"{p['nature']} {quand}{qui} — « {p['citation']} »",
        }
    for d in db.execute(
        """SELECT d.id, d.page, d.citation, d.point_factuel, pe.nom, pi.date_apparente AS date
           FROM declarations d
           LEFT JOIN personnes pe ON pe.id = d.personne_id
           LEFT JOIN pieces pi ON pi.id = d.piece_id
           WHERE d.statut_verif = 'verifie' ORDER BY d.page LIMIT ?""", (MAX_DECLARATIONS,)
    ):
        quand = f"Le {d['date']}, " if d["date"] else ""
        elements[f"D{d['id']}"] = {
            "page": d["page"], "citation": d["citation"], "libelle": d["nom"] or "",
            "ligne": f"{quand}{d['nom'] or 'déclarant non identifié'} déclare : {d['point_factuel']} — « {d['citation']} »",
        }
    return elements


RE_GROUPE_DE_REFERENCES = re.compile(
    r"\s*[(\[]\s*[FPD]\d+(?:\s*(?:,|;|/|et)\s*[FPD]\d+)*\s*[)\]]"
)


def sans_references(texte: str, elements: dict[str, dict]) -> str:
    """Retire les numéros d'éléments entre parenthèses ou crochets
    (« (F6) », « [D3, D7] ») que le modèle recopie parfois : ils ne disent
    rien à l'avocat — les sources sont affichées à côté — et leurs chiffres
    passaient pour des nombres inventés (observé : des contradictions exactes
    écartées). Seuls les groupes faits de numéros existants sont retirés :
    une cote « (D0003) » reste. Un numéro employé comme un mot (« selon
    D3 ») n'est pas retiré : la phrase serait bancale, elle sera écartée."""
    def remplacer(m: re.Match) -> str:
        return "" if all(r in elements for r in re.findall(r"[FPD]\d+", m.group(0))) else m.group(0)

    return re.sub(r"\s{2,}", " ", RE_GROUPE_DE_REFERENCES.sub(remplacer, texte)).strip()


def phrase_retenue(
    texte: str, sources: list[str], elements: dict[str, dict],
    discordances: list[tuple[str, list[str]]] = (), noms: list[str] = (),
) -> list[str] | None:
    """Renvoie les numéros de sources valides si la phrase passe les trois
    contrôles, None sinon."""
    valides = [s for s in dict.fromkeys(sources) if s in elements]
    if not texte or not valides:
        return None
    # Les pages des sources font partie de la référence : « (p. 4) » est exact.
    reference = avec_noms_completes("\n".join(f"p. {elements[s]['page']} {elements[s]['ligne']}" for s in valides), noms)
    if problemes_redaction(texte, reference) or versions_tues(texte, discordances):
        return None
    return valides


def sources_distinctes(cles: list[str], elements: dict[str, dict]) -> list[dict]:
    """Un fait et l'acte de procédure tirés de la même phrase d'une pièce
    portent la même citation : l'avocat la lisait deux ou trois fois sous
    la même phrase du résumé."""
    vues: dict[tuple, dict] = {}
    for cle in cles:
        source = {"page": elements[cle]["page"], "citation": elements[cle]["citation"]}
        vues.setdefault((source["page"], source["citation"]), source)
    return list(vues.values())


def generer_resume_detaille(db: sqlite3.Connection, config: Config, console: Console) -> int:
    """Remplace le résumé détaillé en base. Renvoie le nombre de phrases
    retenues (0 : aucun résumé détaillé, l'onglet n'affiche que le court)."""
    db.execute("DELETE FROM resume_detaille")
    db.commit()
    if config.offline:
        return 0
    elements = _elements(db)
    if not elements:
        return 0

    from .contradictions import discordances_connues  # import ici : contradictions importe ce module

    discordances = discordances_connues(db)
    noms = noms_identifies(db)
    texte = "\n".join(f"[{cle}] (p. {e['page']}) {e['ligne']}" for cle, e in elements.items())
    texte += bloc_discordances(discordances)
    try:
        reponse = obtenir_provider(config).appeler(systeme=PROMPT, prompt=texte, modele=config.modele_analyse)
        sections = extraire_json(reponse.texte).get("sections", [])
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001 — sans résumé détaillé, le reste du dossier est intact
        console.print(f"  [build] résumé détaillé non généré ({exc}).")
        return 0

    proposees, retenues = 0, 0
    ordre_section = 0
    for section in sections if isinstance(sections, list) else []:
        titre = (section.get("titre") or "").strip() if isinstance(section, dict) else ""
        if titre not in SECTIONS:
            continue
        lignes = []
        for phrase in section.get("phrases") or []:
            if not isinstance(phrase, dict):
                continue
            proposees += 1
            texte_phrase = sans_references(re.sub(r"\s+", " ", (phrase.get("texte") or "")).strip(), elements)
            sources = phrase.get("sources") or []
            valides = phrase_retenue(texte_phrase, [str(s) for s in sources], elements, discordances, noms)
            if valides:
                lignes.append((texte_phrase, sources_distinctes(valides, elements)))
        if not lignes:
            continue
        ordre_section += 1
        for ordre_phrase, (texte_phrase, sources_phrase) in enumerate(lignes, 1):
            db.execute(
                "INSERT INTO resume_detaille (section_ordre, section_titre, phrase_ordre, texte, sources_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (ordre_section, titre, ordre_phrase, texte_phrase, json.dumps(sources_phrase, ensure_ascii=False)),
            )
            retenues += 1
    db.commit()
    console.print(f"  [build] résumé détaillé : {retenues}/{proposees} phrase(s) retenue(s).")
    return retenues
