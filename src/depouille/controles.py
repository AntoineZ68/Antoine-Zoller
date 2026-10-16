"""Contrôles de procédure : une liste fermée, appliquée par des règles fixes.

Chaque contrôle lit des actes déjà vérifiés (citation retrouvée mot pour
mot, précisions appuyées par le texte : voir actes.py) et signale un fait,
jamais une qualification : « à vérifier », l'article en cause cité pour que
l'avocat retrouve la règle. Rien n'est signalé sur une simple absence de
mention quand la loi n'impose pas d'écrit, ni sur un délai que la loi ne
chiffre pas.

La liste, complète :
- garde à vue : durée au-delà de 24 h sans prolongation, au-delà de 48 h ;
  fin, notification des droits, entretien avec l'avocat non retrouvés ;
  examen médical demandé jamais réalisé ; première audition moins de deux
  heures après l'avis à l'avocat (conformite.py) ;
- interprète : audition menée sans interprète ; interprète demandé, aucun
  interprète mentionné à la notification des droits ;
- avocat : audition hors la présence de l'avocat demandé, heure de l'avis
  non retrouvée ;
- proches : avis à un proche demandé, fait plus de trois heures après, ou
  jamais retrouvé ;
- fouille intégrale décidée par un agent (et non un officier) de police
  judiciaire ;
- prolongation : décidée par un enquêteur, sans présentation au magistrat,
  ou après l'échéance des vingt-quatre heures ;
- propos recueillis hors procès-verbal d'audition ;
- avis au procureur non retrouvé ;
- personne mineure au moment de la garde à vue ;
- perquisition : avant 6 h ou après 21 h (conformite.py) ; en enquête
  préliminaire sans assentiment ni autorisation du juge ;
- audition après l'échéance des 24 heures, avant toute prolongation ;
  audition suivante hors la présence de l'avocat avisé peu avant ;
- acte d'enquête de flagrance après l'ouverture de l'information ;
  géolocalisation en temps réel sans autorisation de magistrat ;
- acte de procédure antérieur à la saisine (contradictions.py).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

from .regex_patterns import FRAGMENT_DATE, normaliser_date

NOTE_SANS_DATE = (
    " Les pièces ne portent pas de date : heures placées sur une même journée — à vérifier sur les originaux."
)
DELAI_AVIS_PROCHE = timedelta(hours=3)
DUREE_AVANT_PROLONGATION = timedelta(hours=24)


def _signalement(titre: str, description: str, acte: dict | None):
    from .conformite import Signalement

    return Signalement(
        titre=titre, description=description,
        page_reference=acte["page"] if acte else None,
        citation_reference=acte["citation"] if acte else None,
    )


def _actes(gav: dict, nature: str) -> list[dict]:
    return [e for e in gav["evenements"] if e["nature"] == nature]


def _a(acte: dict) -> str:
    """« à 14h30 (page 18) » ou « page 18 »."""
    return f"à {acte['heure']} (page {acte['page']})" if acte.get("heure") else f"page {acte['page']}"


RE_LOCUTEUR = re.compile(
    r"\b(?:M\.|Mme|Mlle|Madame|Monsieur|Mademoiselle)\s+((?:[A-ZÀ-Ý][\w'’-]+\s+){0,2}[A-ZÀ-Ý][\w'’-]+)"
    r"[^.«:]{0,40}?\b(?:d[ée]clare|indique|dit|pr[ée]cise|affirme|ajoute|s['’][ée]crie)"
)


def _propos_d_un_tiers(citation: str, nom_personne: str) -> bool:
    """« Mme DERRADJI Nadia déclare spontanément : « Il est rentré tard » » :
    les propos de la mère, rattachés par le modèle à la garde à vue du fils.
    Un locuteur nommé dont un mot n'est pas dans le nom de la personne est un
    tiers ; « M. TARKAN déclare » reste la personne."""
    from .classify import _sans_accents

    m = RE_LOCUTEUR.search(citation)
    if not m or not nom_personne:
        return False
    mots_personne = set(_sans_accents(nom_personne).upper().split())
    return any(mot not in mots_personne for mot in _sans_accents(m.group(1)).upper().split())


def controles_garde_a_vue(gav: dict, suffixe: str) -> list:
    note = NOTE_SANS_DATE if gav.get("heures_sans_date") else ""
    signalements = []
    auditions = _actes(gav, "debut_audition")
    demande_interprete = next(iter(_actes(gav, "demande_interprete")), None)

    # Interprète.
    for audition in auditions:
        if audition["details"].get("interprete") == "absent":
            demande = f" ; un interprète avait été demandé (page {demande_interprete['page']})" if demande_interprete else ""
            signalements.append(_signalement(
                f"Audition menée sans interprète{suffixe}",
                f"L'audition débute {_a(audition)} sans interprète, selon la pièce{demande}. Les articles 63-1 et "
                f"803-5 du code de procédure pénale prévoient l'assistance d'un interprète pour une personne qui ne "
                f"comprend pas le français — à vérifier.{note}",
                audition,
            ))
            break
    if demande_interprete:
        droits = next(iter(_actes(gav, "notification_droits")), None)
        if droits and droits["details"].get("interprete") != "present":
            signalements.append(_signalement(
                f"Interprète demandé — aucun interprète mentionné à la notification des droits{suffixe}",
                f"La personne demande un interprète (page {demande_interprete['page']}) ; la notification des droits "
                f"({_a(droits)}) ne mentionne aucun interprète. Article 63-1 du code de procédure pénale : droits "
                f"notifiés dans une langue que la personne comprend — à vérifier.{note}",
                demande_interprete,
            ))

    # Avocat absent à l'audition, sans heure d'avis permettant le calcul des deux heures.
    avocat_demande = _actes(gav, "demande_entretien_avocat") or _actes(gav, "avis_avocat")
    if avocat_demande and gav.get("delai_avis_avocat_audition_minutes") is None:
        for audition in auditions:
            if audition["details"].get("avocat") == "absent":
                signalements.append(_signalement(
                    f"Audition hors la présence de l'avocat demandé{suffixe}",
                    f"Un avocat est demandé (page {avocat_demande[0]['page']}) ; l'audition débute {_a(audition)} "
                    "hors sa présence, et l'heure de l'avis à l'avocat n'a pas été retrouvée : le délai de deux heures "
                    f"(article 63-4-2 du code de procédure pénale) ne peut être calculé — à vérifier.{note}",
                    audition,
                ))
                break

    # Avis à un proche (article 63-2 : trois heures).
    for demande in _actes(gav, "demande_avis_famille"):
        avis = next((a for a in _actes(gav, "avis_famille")
                     if not (a["instant"] and demande["instant"]) or a["instant"] >= demande["instant"]), None)
        if avis is None:
            signalements.append(_signalement(
                f"Avis à un proche demandé, non retrouvé au dossier{suffixe}",
                f"La personne demande qu'un proche soit avisé ({_a(demande)}) ; aucun avis n'a été retrouvé. "
                f"Article 63-2 du code de procédure pénale — à vérifier.{note}",
                demande,
            ))
        elif avis["instant"] and demande["instant"] and avis["instant"] - demande["instant"] > DELAI_AVIS_PROCHE:
            signalements.append(_signalement(
                f"Proche avisé plus de trois heures après la demande{suffixe}",
                f"Demande {_a(demande)}, avis {_a(avis)}. L'article 63-2 du code de procédure pénale prévoit un avis "
                f"dans les trois heures, sauf circonstances insurmontables mentionnées — à vérifier.{note}",
                avis,
            ))
        break

    # Fouille intégrale (article 63-7 : décidée par un officier de police judiciaire).
    for fouille in _actes(gav, "fouille_integrale"):
        if fouille["details"].get("agent") == "APJ":
            signalements.append(_signalement(
                f"Fouille intégrale décidée par un agent de police judiciaire{suffixe}",
                f"La fouille intégrale ({_a(fouille)}) est menée par un agent de police judiciaire, selon la pièce. "
                "L'article 63-7 du code de procédure pénale la réserve à la décision d'un officier de police "
                "judiciaire — à vérifier.",
                fouille,
            ))

    # Prolongation (article 63 II).
    for prolongation in _actes(gav, "prolongation_garde_a_vue"):
        agent = prolongation["details"].get("agent")
        if agent in ("OPJ", "APJ"):
            signalements.append(_signalement(
                f"Prolongation de garde à vue décidée par un enquêteur{suffixe}",
                f"La prolongation ({_a(prolongation)}) est décidée par un "
                f"{'officier' if agent == 'OPJ' else 'agent'} de police judiciaire, selon la pièce. L'article 63 II "
                "du code de procédure pénale la soumet à l'autorisation écrite et motivée du procureur de la "
                f"République — à vérifier.{note}",
                prolongation,
            ))
        if prolongation["details"].get("presentation") == "non":
            signalements.append(_signalement(
                f"Prolongation sans présentation au magistrat{suffixe}",
                f"La pièce de prolongation ({_a(prolongation)}) indique que la personne n'a pas été présentée. "
                "L'article 63 II du code de procédure pénale prévoit une présentation préalable, sauf décision "
                f"écrite et motivée — à vérifier.{note}",
                prolongation,
            ))
    # Seule la première prolongation se compare aux vingt-quatre heures : les
    # suivantes (régimes dérogatoires, à 48 h puis 72 h) ont leur propre échéance.
    prolongations = sorted((p for p in _actes(gav, "prolongation_garde_a_vue") if p["instant"]),
                           key=lambda p: p["instant"])
    debut = gav.get("interpellation") or gav["placement"]
    if prolongations and debut["instant"] and prolongations[0]["instant"] - debut["instant"] > DUREE_AVANT_PROLONGATION:
        premiere = prolongations[0]
        depuis = "l'interpellation, début de la privation de liberté," if gav.get("interpellation") else "le placement"
        signalements.append(_signalement(
            f"Prolongation après l'échéance des 24 heures{suffixe}",
            f"La mesure court depuis {depuis} {_a(debut)} ; la prolongation intervient {_a(premiere)}, plus de "
            "vingt-quatre heures après (article 63 II du code de procédure pénale) — à vérifier, notamment si les "
            f"pièces ne datent pas l'interpellation de la même façon.{note}",
            premiere,
        ))

    # Audition tenue après l'échéance des vingt-quatre heures, alors qu'aucune
    # prolongation n'est encore intervenue (observé sur un dossier de test :
    # audition à 06h40, échéance à 06h10 depuis l'interpellation,
    # prolongation à 07h05 — et c'est dans cette audition que viennent les
    # aveux).
    if debut["instant"]:
        echeance = debut["instant"] + DUREE_AVANT_PROLONGATION
        for audition in auditions:
            if not audition["instant"] or audition["instant"] <= echeance:
                continue
            if any(p["instant"] <= audition["instant"] for p in prolongations):
                continue
            prolongation = f", la prolongation intervenant {_a(prolongations[0])}" if prolongations else ""
            depuis = "l'interpellation" if gav.get("interpellation") else "le placement"
            signalements.append(_signalement(
                f"Audition après l'échéance des 24 heures, avant toute prolongation{suffixe}",
                f"La mesure court depuis {depuis} {_a(debut)} : les vingt-quatre heures expirent le "
                f"{echeance.strftime('%d/%m à %Hh%M')}. L'audition débute {_a(audition)}{prolongation} "
                f"(article 63 II du code de procédure pénale) — à vérifier.{note}",
                audition,
            ))
            break

    # Auditions suivantes hors la présence de l'avocat, avisé peu avant (la
    # première audition relève du délai de deux heures : conformite.py).
    for audition in auditions[1:] if len(auditions) > 1 else []:
        if audition["details"].get("avocat") != "absent" or not audition["instant"]:
            continue
        avis = [a for a in _actes(gav, "avis_avocat") if a["instant"] and a["instant"] <= audition["instant"]]
        if not avis:
            continue
        dernier = max(avis, key=lambda a: a["instant"])
        ecart = audition["instant"] - dernier["instant"]
        if ecart < timedelta(hours=2):
            minutes = int(ecart.total_seconds() // 60)
            signalements.append(_signalement(
                f"Audition hors la présence de l'avocat, avisé {minutes} min avant{suffixe}",
                f"L'avocat est avisé {_a(dernier)} ; l'audition débute {_a(audition)} hors sa présence "
                f"(article 63-4-2 du code de procédure pénale ; renonciation éventuelle à vérifier sur la pièce).{note}",
                audition,
            ))

    # Propos hors audition — ceux de la personne, pas d'un tiers.
    for propos in _actes(gav, "propos_hors_audition"):
        if _propos_d_un_tiers(propos["citation"] or "", gav.get("nom") or ""):
            continue
        sans_avocat = " hors la présence de l'avocat," if propos["details"].get("avocat") == "absent" else ""
        signalements.append(_signalement(
            f"Propos recueillis hors procès-verbal d'audition{suffixe}",
            f"La pièce ({_a(propos)}) rapporte des propos de la personne recueillis hors audition,{sans_avocat} "
            "en dehors des garanties de l'audition (droit au silence, assistance de l'avocat : articles 63-1 et "
            "63-4-2 du code de procédure pénale) — à vérifier.",
            propos,
        ))
        break

    # Avis au procureur (article 63).
    if not _actes(gav, "avis_parquet"):
        signalements.append(_signalement(
            f"Avis au procureur non identifié{suffixe}",
            "Un placement en garde à vue a été identifié, mais aucun avis au procureur de la République n'a été "
            "retrouvé dans le dossier (article 63 du code de procédure pénale : avis dès le début de la mesure). "
            "Pièce absente, ou mention non reconnue — à vérifier.",
            gav["placement"],
        ))
    return signalements


RE_NAISSANCE = re.compile(rf"\bn[ée]e?\s+le\s+{FRAGMENT_DATE}", re.IGNORECASE)


def controle_mineur(db: sqlite3.Connection, gav: dict, suffixe: str) -> list:
    """Date de naissance écrite dans une pièce de la personne, comparée à la
    date de la garde à vue : la personne avait-elle moins de dix-huit ans ?"""
    from .gardes_a_vue import _instant

    if gav["personne_id"] is None or not gav["placement"]["date"]:
        return []
    for piece in db.execute(
        "SELECT page_debut, page_fin FROM pieces WHERE personne_principale_id = ? ORDER BY page_debut",
        (gav["personne_id"],),
    ):
        for page in db.execute(
            "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ?", (piece[0], piece[1])
        ):
            m = RE_NAISSANCE.search(page["texte"])
            naissance = normaliser_date(m.group(1)) if m else None
            if not naissance:
                continue
            ne = _instant(naissance, "00h00")
            placement = gav["placement"]["instant"]
            if ne and placement:
                dix_huit = ne.replace(year=ne.year + 18) if not (ne.month == 2 and ne.day == 29) else ne.replace(year=ne.year + 18, day=28)
                if placement < dix_huit:
                    return [_signalement(
                        f"Personne mineure lors de la garde à vue{suffixe}",
                        f"Née le {naissance} (page {page['numero_global']}), la personne a moins de dix-huit ans au "
                        f"placement ({gav['placement']['date']}). Le code de la justice pénale des mineurs prévoit un "
                        "régime propre (avocat obligatoire, avis aux représentants légaux, enregistrement des "
                        "auditions) — à vérifier.",
                        gav["placement"],
                    )]
            return []
    return []


RE_FLAGRANCE = re.compile(r"flagran", re.IGNORECASE)
RE_COMMISSION_ROGATOIRE = re.compile(r"commission\s+rogatoire", re.IGNORECASE)
RE_GEOLOCALISATION = re.compile(r"(?:g[ée]o)?localisation\s+en\s+temps\s+r[ée]el|g[ée]olocalisation", re.IGNORECASE)
RE_AUTORISATION_MAGISTRAT = re.compile(
    r"autoris[ée]e?s?\b[^.]{0,80}(?:procureur|juge|JLD|libert[ée]s)|(?:procureur|juge|JLD)[^.]{0,80}autoris", re.IGNORECASE
)


# Une photographie montrée seule au témoin pour savoir « si c'était lui »,
# avant la planche photographique (observé sur un dossier de test : photo
# envoyée par WhatsApp au témoin, message effacé à la demande du policier,
# puis « reconnaissance » sur planche).
RE_PHOTO_ISOLEE = re.compile(
    r"\b(?:envoy|montr|pr[ée]sent|fait\s+voir|soumis)\w*\s+(?:une|la|sa|cette)\s+(?:seule\s+)?photo(?:graphie)?s?\b"
    r"(?![^.]{0,40}\bplanche)(?=[^«»]{0,200}?(?:si\s+c['’][ée]tait\s+lui|reconna|identifi|c['’]est\s+lui|ressembl))",
    re.IGNORECASE,
)
RE_PLANCHE = re.compile(r"planche\s+(?:photographique|comportant|de\s+(?:\d+|six|cinq|huit|dix))|tapissage", re.IGNORECASE)
RE_EFFACE = re.compile(r"effac|supprim|d[ée]truit", re.IGNORECASE)


def _photo_montree_avant_la_planche(db: sqlite3.Connection) -> list:
    pages = db.execute("SELECT numero_global, texte FROM pages ORDER BY numero_global").fetchall()
    planche = next((p for p in pages if RE_PLANCHE.search(p["texte"])), None)
    if planche is None:
        return []
    for page in pages:
        m = RE_PHOTO_ISOLEE.search(page["texte"])
        if not m:
            continue
        suite = page["texte"][m.start() : m.start() + 600]
        efface = " ; le message aurait été effacé à la demande de l'enquêteur" if RE_EFFACE.search(suite) else ""
        return [_signalement(
            "Photographie montrée seule au témoin, en plus de la planche",
            f"La pièce (page {page['numero_global']}) rapporte qu'une photographie a été montrée ou envoyée seule à "
            f"un témoin pour savoir s'il reconnaissait l'auteur{efface}. Une planche photographique figure au dossier "
            f"(page {planche['numero_global']}) : la reconnaissance a pu en être influencée — l'ordre des deux, la "
            "fiabilité de l'identification et la loyauté du procédé sont à vérifier.",
            {"page": page["numero_global"], "citation": _phrase(page["texte"], m.start())},
        )]
    return []


def _cle(date: str | None):
    try:
        j, m, a = (int(x) for x in (date or "").split("/"))
        return (a, m, j)
    except ValueError:
        return None


def _texte_piece(db: sqlite3.Connection, piece) -> list:
    return db.execute(
        "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
        (piece["page_debut"], piece["page_fin"]),
    ).fetchall()


def controles_pieces(db: sqlite3.Connection) -> list:
    """Contrôles lus directement dans le texte des pièces, par des règles :
    - un acte d'enquête de flagrance daté après l'ouverture de
      l'information (réquisitoire introductif), sans commission rogatoire —
      observé sur un dossier de test : réquisition du 17/03, information
      ouverte le 16/03 ;
    - une géolocalisation en temps réel sans autorisation d'un magistrat
      mentionnée (articles 230-32 et suivants)."""
    signalements = []
    pieces = db.execute("SELECT * FROM pieces ORDER BY page_debut").fetchall()
    ouverture = min(
        (p for p in pieces if p["type"] == "Réquisitoire introductif" and _cle(p["date_apparente"])),
        key=lambda p: _cle(p["date_apparente"]), default=None,
    )
    for piece in pieces:
        if piece["type"] in ("Réquisitoire introductif", "PV de synthèse"):
            continue
        pages = _texte_piece(db, piece)
        texte = "\n".join(p["texte"] for p in pages)
        if (ouverture and _cle(piece["date_apparente"]) and _cle(piece["date_apparente"]) > _cle(ouverture["date_apparente"])
                and RE_FLAGRANCE.search(texte) and not RE_COMMISSION_ROGATOIRE.search(texte)):
            m = RE_FLAGRANCE.search(texte)
            signalements.append(_signalement(
                f"Acte d'enquête de flagrance après l'ouverture de l'information ({piece['date_apparente']})",
                f"La pièce du {piece['date_apparente']} se réfère à l'enquête de flagrance, alors que l'information est "
                f"ouverte depuis le réquisitoire du {ouverture['date_apparente']} (page {ouverture['page_debut']}), "
                "et ne mentionne aucune commission rogatoire. Après l'ouverture de l'information, les actes "
                "d'enquête relèvent du juge d'instruction (articles 81 et 151 du code de procédure pénale) — à vérifier.",
                {"page": _page_de(pages, m.group(0)), "citation": _phrase(texte, m.start())},
            ))
        m = RE_GEOLOCALISATION.search(texte)
        if m and piece["type"] not in ("Ordonnance du juge des libertés et de la détention",) and not RE_AUTORISATION_MAGISTRAT.search(texte):
            signalements.append(_signalement(
                "Géolocalisation en temps réel sans autorisation de magistrat mentionnée",
                f"La pièce (page {piece['page_debut']}) demande une localisation en temps réel ; aucune autorisation du "
                "procureur de la République ou du juge n'y est mentionnée (articles 230-32 et suivants du code de "
                "procédure pénale)" + (" ; la demande est faite « sans limitation de durée »"
                                        if re.search(r"sans\s+limitation\s+de\s+dur[ée]e", texte, re.IGNORECASE) else "")
                + " — à vérifier.",
                {"page": _page_de(pages, m.group(0)), "citation": _phrase(texte, m.start())},
            ))
    return signalements


def _page_de(pages, extrait: str) -> int:
    return next((p["numero_global"] for p in pages if extrait in p["texte"]), pages[0]["numero_global"])


def _phrase(texte: str, position: int) -> str:
    debut = max(texte.rfind(".", 0, position), texte.rfind("\n\n", 0, position)) + 1
    fin = texte.find(".", position)
    return " ".join(texte[debut : fin + 1 if fin >= 0 else len(texte)].split())


def controles_dossier(db: sqlite3.Connection) -> list:
    """Perquisition en enquête préliminaire sans assentiment ni juge
    (article 76) — le cadre d'enquête est lu dans la pièce par une règle."""
    import json

    signalements = controles_pieces(db) + _photo_montree_avant_la_planche(db)
    for e in db.execute(
        "SELECT * FROM evenements_procedure WHERE nature = 'debut_perquisition' AND statut_verif = 'verifie'"
    ):
        details = json.loads(e["details_json"] or "{}") if "details_json" in e.keys() else {}
        if (details.get("cadre") == "preliminaire" and details.get("assentiment") == "non"
                and details.get("autorisation_juge") != "oui"):
            signalements.append(_signalement(
                "Perquisition en enquête préliminaire sans assentiment",
                f"La perquisition ({_a(dict(e))}) a lieu en enquête préliminaire sans l'assentiment de la personne, "
                "et aucune autorisation du juge des libertés et de la détention n'est mentionnée. Article 76 du code "
                "de procédure pénale — à vérifier.",
                dict(e),
            ))
    return signalements
