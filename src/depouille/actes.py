"""Actes de procédure lus par le modèle, vérifiés par le code.

Les actes d'une garde à vue (placement, droits, avocat, médecin,
interprète, avis au procureur, audition, perquisition, fouille,
prolongation…) s'écrivent de cent façons : « A 07h30, nous notifions… »,
« Droits notifiés à 09h05 », « SOS Médecins, contacté à 09h10 »,
« Avis est donné à Madame la Procureure à six heures quarante-cinq ».
Les reconnaître par des formules écrites à la main obligeait à en ajouter
une à chaque dossier. Le modèle absorbe cette variété ; il ne décide de
rien. Chaque fiche qu'il rend est contrôlée ici, de façon déterministe :

- la citation figure mot pour mot sur la page annoncée (vérification
  habituelle, en aval) ;
- la nature de l'acte est appuyée par un mot de la citation (un « avis
  au procureur » cite « procureur », « parquet » ou « magistrat ») ;
- l'heure et la date figurent dans la citation (ou la date est celle de
  la pièce), sinon elles sont retirées — jamais devinées ;
- chaque précision (qui accomplit l'acte, avocat ou interprète présent ou
  absent, présentation au magistrat, assentiment) est appuyée par les mots
  de la citation, sinon elle est retirée.

Une fiche qui ne passe pas ces contrôles n'est pas enregistrée. Les
contrôles de procédure (controles.py) ne lisent que des fiches ainsi
vérifiées."""

from __future__ import annotations

import json
import re
import sqlite3

from rich.console import Console

from .config import Config
from .garde_fous import horaires
from .llm import ErreurModeOffline, executer_en_parallele, extraire_json, lots_de_pages, obtenir_provider
from .regex_patterns import RE_DATE, normaliser_date
from .verification import _normaliser

# Nature -> (mots qui doivent figurer dans la citation, libellé). Pas de
# « saisine » : sur un dossier de test, vingt pages de remplissage
# (« Conformément aux instructions du magistrat… ») passaient pour des
# saisines ; la saisine est lue par le type de la pièce (PV de saisine).
NATURES: dict[str, tuple[str, str]] = {
    "interpellation": (
        r"interpell|appr[ée]hend|ma[îi]tris|menott|libert[ée]|arr[êe]t|p[ée]n[ée]tr|conduit|contr[ôo]le",
        "Interpellation",
    ),
    "placement_garde_a_vue": (r"garde\s+[àa]\s+vue|\bGAV\b", "Placement en garde à vue"),
    "notification_droits": (r"droits", "Notification des droits"),
    "demande_entretien_avocat": (r"avocat|ma[îi]tre|conseil|\bMe\b", "Demande d'avocat"),
    "avis_avocat": (r"avocat|ma[îi]tre|conseil|barreau|permanence|\bMe\b", "Avis à l'avocat"),
    "realisation_entretien_avocat": (r"entretien|avocat|ma[îi]tre|\bMe\b", "Entretien avocat réalisé"),
    "demande_examen_medical": (r"m[ée]decin|m[ée]dical", "Demande d'examen médical"),
    "realisation_examen_medical": (r"m[ée]decin|m[ée]dical|examin", "Examen médical réalisé"),
    "demande_interprete": (r"interpr[èe]te|tradu", "Demande d'interprète"),
    "demande_avis_famille": (r"famille|proche|parent|m[èe]re|p[èe]re|[ée]pou|conjoint|employeur|pr[ée]venir", "Demande d'avis à un proche"),
    "avis_famille": (r"famille|proche|parent|m[èe]re|p[èe]re|[ée]pou|conjoint|employeur|avis", "Avis à un proche"),
    "avis_parquet": (r"procureur|parquet|magistrat|substitut", "Avis au procureur"),
    "debut_audition": (r"audition|entend|extray|question|interrog|d[ée]clar", "Début d'audition"),
    "fin_audition": (r"audition|clos|lecture|fin", "Fin d'audition"),
    "debut_perquisition": (r"perquisition|visite|fouill|p[ée]n[ée]tr|introduisons", "Début de perquisition"),
    "fin_perquisition": (r"perquisition|visite|fouill|termin|fin", "Fin de perquisition"),
    "fouille_integrale": (r"fouille|mise\s+[àa]\s+nu|int[ée]grale", "Fouille intégrale"),
    "prolongation_garde_a_vue": (r"prolong", "Prolongation de garde à vue"),
    "fin_garde_a_vue": (
        r"fin\b|lev[ée]e|libert[ée]|d[ée]f[ée]r|terme|lib[ée]r|pr[ée]sent[ée]|conduit|remis",
        "Fin de garde à vue",
    ),
    "propos_hors_audition": (r"hors|oralement|spontan|couloir|transfert|v[ée]hicule", "Propos recueillis hors audition"),
}

MAGISTRAT = r"procureur|parquet|substitut|\bjuge\b|\bJLD\b"

# Précision -> valeur -> mots qui doivent figurer dans la citation.
_AVOCAT = r"avocat|conseil|ma[îi]tre|\bMe\b"
_INTERPRETE = r"interpr[èe]te|tradu"
_ABSENCE = r"\bsans\b|absen|\bhors\b|pas\s+(?:encore\s+|toujours\s+)?(?:pr[ée]sent|disponible|venu|arriv)|n['’]est\s+(?:toujours\s+)?pas|indisponible|en\s+fran[çc]ais"
_PRESENCE = r"pr[ée]sence\s+de|assist[ée]|en\s+pr[ée]sence|pr[ée]sent\b|par\s+l['’]interm[ée]diaire"
PRECISIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "agent": {
        "OPJ": (r"\bOPJ\b|officier\s+de\s+police\s+judiciaire",),
        "APJ": (r"\bAPJA?\b|agent\s+de\s+police\s+judiciaire|gendarme\s+adjoint|policier\s+adjoint",),
        "procureur": (r"procureur|parquet|substitut",),
        "juge": (r"\bjuge\b|\bJLD\b|libert[ée]s\s+et\s+de\s+la\s+d[ée]tention",),
    },
    "avocat": {"absent": (_AVOCAT, _ABSENCE), "present": (_AVOCAT, _PRESENCE)},
    "interprete": {"absent": (_INTERPRETE, _ABSENCE), "present": (_INTERPRETE, _PRESENCE)},
    "presentation": {
        "non": (r"(?:pas|sans|non)\b[^.]{0,40}pr[ée]sent",),
        "oui": (r"pr[ée]sent[ée]e?\s+(?:au|devant|[àa])|visio|pr[ée]sentation",),
    },
    "assentiment": {
        "non": (r"sans\s+(?:l['’]\s*)?assentiment|sans\s+(?:son\s+|l['’])?(?:accord|consentement)|refus|forc",),
        "oui": (r"assentiment|accord\s+expr[èe]s|consent",),
    },
    "autorisation_juge": {
        "non": (r"sans\s+(?:autorisation|ordonnance)[^.]{0,60}(?:juge|JLD|libert)",),
        "oui": (r"(?:autoris|ordonnance)[^.]{0,60}(?:juge|JLD|libert)",),
    },
}

# Pièces où l'on ne cherche pas d'actes de garde à vue : documents saisis,
# expertises, et la synthèse (elle reprend les actes d'autres pièces, avec
# des heures parfois arrondies : elle ferait naître de fausses discordances).
TYPES_SANS_ACTES = (
    "Bordereau / inventaire des pièces", "Document de l'avocat (hors procédure)",
    "Relevé bancaire", "Facture", "Casier judiciaire", "Enquête de personnalité",
    "Correspondance saisie (e-mail, courrier, SMS)", "Analyse téléphonique", "Retranscription",
    "Rapport d'expertise", "PV de synthèse", "Signalement (article 40 CPP ou L.823-12 du Code de commerce)",
)

# Actes qui n'ont lieu qu'une fois par garde à vue : un second relevé du
# même acte dans la même pièce n'est pas un nouvel acte.
ACTES_UNIQUES_PAR_PIECE = {
    "interpellation", "placement_garde_a_vue", "notification_droits", "fin_garde_a_vue",
    "prolongation_garde_a_vue", "debut_perquisition", "fin_perquisition",
}

PROMPT = (
    "Tu relèves les actes de procédure consignés par les enquêteurs dans une pièce de procédure "
    "pénale française (pages précédées de « [page N] »). Natures possibles, à l'exclusion de "
    "toute autre : " + ", ".join(NATURES) + ". "
    "Pour chaque acte, rends une fiche : "
    '{"nature": "...", "page": N, "citation": "phrase EXACTE recopiée mot pour mot, qui contient '
    "l'heure si l'acte est horodaté\", \"heure\": \"HHhMM\" ou null, \"date\": \"JJ/MM/AAAA\" ou null, "
    '"personne": "nom de la personne concernée tel qu\'écrit" ou null, '
    '"agent": "OPJ" | "APJ" | "procureur" | "juge" | null (qui accomplit ou décide l\'acte), '
    '"avocat": "present" | "absent" | null, "interprete": "present" | "absent" | null, '
    '"presentation": "oui" | "non" | null (prolongation : personne présentée au magistrat), '
    '"assentiment": "oui" | "non" | null (perquisition), '
    '"autorisation_juge": "oui" | "non" | null (perquisition)}. '
    "N'invente rien, ne déduis rien : une précision que la citation ne dit pas vaut null ; une "
    "heure absente de la citation vaut null. Ne relève pas un acte seulement évoqué dans une "
    "question posée à la personne entendue, ni ce que la personne raconte de son côté. "
    "« Propos recueillis hors audition » : propos de la personne obtenus en dehors d'un procès-verbal "
    "d'audition (couloir, véhicule, oralement). Le texte peut venir d'une page numérisée : recopie "
    "la citation telle qu'écrite, sans la corriger. "
    'Réponds uniquement en JSON : {"actes": [...]}.'
)


def _appuye(motifs: tuple[str, ...], citation: str) -> bool:
    return all(re.search(m, citation, re.IGNORECASE) for m in motifs)


# Un acte « réalisé » rédigé à la négative n'a pas eu lieu : « SOS Médecins
# […] ne s'est toujours pas présenté […] la mesure se poursuit sans examen
# médical » était rendu par le modèle comme un examen réalisé — ce qui
# aurait effacé le signalement de l'examen demandé et jamais fait.
NATURES_REALISATION = {"realisation_examen_medical", "realisation_entretien_avocat", "avis_famille", "fin_garde_a_vue"}
RE_NEGATION = re.compile(
    r"\bsans\b|\bpas\b|\bjamais\b|\baucun|n['’]a\s+(?:pas\s+)?(?:pu|été)\s+(?:pas\s+)?|indisponible|impossible",
    re.IGNORECASE,
)
# Fenêtre de texte qui suit la citation, dans la pièce, où une précision
# peut être prouvée : « À 14h30, extrayons le mis en cause. L'interprète […]
# n'étant pas disponible […], nous décidons de procéder à l'audition en
# français » — le modèle cite la première phrase, la preuve est dans la
# suivante.
FENETRE_PREUVE = 320
# « Médecin : « Non. » », « Interprète : « Non, je parle français. » » : la
# personne refuse — ce n'est pas une demande (observé : un examen médical et
# un interprète « demandés », d'où deux faux signalements).
NATURES_DEMANDE = {"demande_examen_medical", "demande_entretien_avocat", "demande_interprete", "demande_avis_famille"}
RE_REFUS = re.compile(
    r"[:«]\s*«?\s*non\b|\bne\s+(?:souhaite|veut|demande|d[ée]sire|sollicite)\s+pas|\brefuse|\brenonce|"
    r"n['’]a\s+sollicit[ée]\s+aucun|\baucun\s+(?:examen|avocat|interpr)",
    re.IGNORECASE,
)


def _fenetre(citation: str, texte: str) -> str:
    texte_n, citation_n = _normaliser(texte), _normaliser(citation)
    debut = texte_n.find(citation_n)
    if debut < 0:
        return citation
    return texte_n[debut : debut + len(citation_n) + FENETRE_PREUVE]


def fiche_verifiee(fiche: dict, date_piece: str | None, texte_piece: str = "") -> dict | None:
    """La fiche telle qu'elle sera enregistrée, ou None si elle n'est pas
    appuyée par sa citation. Les champs non appuyés sont retirés. Une
    précision (avocat absent, assentiment…) peut aussi être prouvée par le
    texte qui suit immédiatement la citation dans la pièce."""
    if not isinstance(fiche, dict):
        return None
    nature = fiche.get("nature")
    citation = str(fiche.get("citation") or "").strip()
    if nature not in NATURES or not citation or not isinstance(fiche.get("page"), int):
        return None
    if not re.search(NATURES[nature][0], citation, re.IGNORECASE):
        return None
    if nature in NATURES_REALISATION and RE_NEGATION.search(citation):
        return None
    if nature in NATURES_DEMANDE and RE_REFUS.search(citation):
        return None

    heure = fiche.get("heure")
    if heure:
        m = re.fullmatch(r"(\d{1,2})\s*h\s*(\d{2})", str(heure).strip(), re.IGNORECASE)
        heure = f"{int(m.group(1)):02d}h{m.group(2)}" if m else None
        if heure and (int(heure[:2]), int(heure[3:])) not in horaires(citation):
            heure = None

    date = normaliser_date(str(fiche.get("date"))) if fiche.get("date") else None
    if date:
        dates_citation = {normaliser_date(d) for d in RE_DATE.findall(citation)}
        if date not in dates_citation and date != date_piece:
            date = None
    if date is None and date_piece:
        date = date_piece

    preuve = _fenetre(citation, texte_piece) if texte_piece else citation
    details = {}
    for champ, valeurs in PRECISIONS.items():
        valeur = fiche.get(champ)
        # L'agent se lit dans la citation seule : la phrase suivante peut
        # nommer quelqu'un d'autre.
        support = citation if champ == "agent" else preuve
        if valeur in valeurs and _appuye(valeurs[valeur], support):
            # « La procureure […] autorise, sur demande de l'OPJ » : un
            # enquêteur et un magistrat dans la même phrase, on ne sait pas
            # qui décide — rien n'est retenu.
            if champ == "agent" and valeur in ("OPJ", "APJ") and re.search(MAGISTRAT, citation, re.IGNORECASE):
                continue
            details[champ] = valeur
    return {
        "nature": nature, "page": fiche["page"], "citation": citation, "heure": heure, "date": date,
        "personne": str(fiche.get("personne") or "").strip() or None, "details": details,
    }


def _cadre(texte: str) -> str | None:
    """Cadre d'enquête écrit dans la pièce — par une règle, pas par le modèle."""
    flagrance = re.search(r"flagran", texte, re.IGNORECASE)
    preliminaire = re.search(r"enqu[êe]te\s+pr[ée]liminaire|\bpr[ée]liminaire\b", texte, re.IGNORECASE)
    commission = re.search(r"commission\s+rogatoire", texte, re.IGNORECASE)
    trouves = [nom for nom, m in (("flagrance", flagrance), ("preliminaire", preliminaire),
                                  ("commission_rogatoire", commission)) if m]
    return trouves[0] if len(trouves) == 1 else None


def extraire_actes(
    db: sqlite3.Connection, config: Config, pieces: list[sqlite3.Row], console: Console, compteur: dict[str, int]
) -> int:
    """Relève les actes de chaque pièce, les vérifie et les enregistre dans
    evenements_procedure (statut « a_faire » : la citation est vérifiée en
    aval, comme les autres). Un acte déjà reconnu par les règles de
    chrono.py n'est pas dupliqué : ses précisions lui sont ajoutées."""
    from .chrono import _personne_par_nom

    provider = obtenir_provider(config)
    lots: list[tuple[sqlite3.Row, list]] = []
    for piece in pieces:
        if piece["type"] in TYPES_SANS_ACTES:
            continue
        pages = db.execute(
            "SELECT numero_global, texte FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
            (piece["page_debut"], piece["page_fin"]),
        ).fetchall()
        lots.extend((piece, lot) for lot in lots_de_pages(pages))

    def appeler(element):
        _, lot = element
        texte = "\n".join(f"[page {p['numero_global']}]\n{p['texte']}" for p in lot)
        return provider.appeler(systeme=PROMPT, prompt=texte, modele=config.modele_analyse)

    nb_retenus = nb_ecartes = 0
    for (piece, lot), reponse in zip(lots, executer_en_parallele(appeler, lots)):
        try:
            if isinstance(reponse, Exception):
                raise reponse
            compteur["tokens_in"] += reponse.tokens_in
            compteur["tokens_out"] += reponse.tokens_out
            donnees = extraire_json(reponse.texte)
        except ErreurModeOffline:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [actes] échec pour la pièce {piece['id']} : {exc}, pièce ignorée.")
            continue
        fiches = donnees.get("actes", []) if isinstance(donnees, dict) else donnees if isinstance(donnees, list) else []
        pages_lot = {p["numero_global"]: p["texte"] for p in lot}
        texte_lot = "\n".join(pages_lot.values())
        cadre = _cadre(texte_lot)
        for brute in fiches:
            fiche = fiche_verifiee(brute, piece["date_apparente"], texte_lot)
            if fiche is None or fiche["page"] not in pages_lot:
                nb_ecartes += 1
                continue
            if cadre:
                fiche["details"]["cadre"] = cadre
            personne_id = _personne_par_nom(db, fiche["personne"] or "") or piece["personne_principale_id"]
            if _fusionner_avec_existant(db, piece["id"], fiche):
                nb_retenus += 1
                continue
            db.execute(
                """INSERT INTO evenements_procedure
                   (piece_id, date, heure, nature, personne_id, page, citation, methode_verif, details_json, statut_verif)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'modele', ?, 'a_faire')""",
                (piece["id"], fiche["date"], fiche["heure"], fiche["nature"], personne_id, fiche["page"],
                 fiche["citation"], json.dumps(fiche["details"], ensure_ascii=False)),
            )
            nb_retenus += 1
    db.commit()
    console.print(f"  [actes] actes relevés par le modèle et vérifiés : {nb_retenus}, écartés : {nb_ecartes}")
    return nb_retenus


def _fusionner_avec_existant(db: sqlite3.Connection, piece_id: int, fiche: dict) -> bool:
    """Vrai si l'acte existe déjà pour cette pièce (reconnu par les règles,
    ou relevé deux fois) : ses précisions vérifiées lui sont ajoutées."""
    existants = db.execute(
        "SELECT id, heure, details_json FROM evenements_procedure WHERE piece_id = ? AND nature = ?",
        (piece_id, fiche["nature"]),
    ).fetchall()
    for e in existants:
        meme = e["heure"] == fiche["heure"] or fiche["nature"] in ACTES_UNIQUES_PAR_PIECE
        if meme:
            details = {**fiche["details"], **json.loads(e["details_json"] or "{}")}
            db.execute("UPDATE evenements_procedure SET details_json = ? WHERE id = ?",
                       (json.dumps(details, ensure_ascii=False), e["id"]))
            return True
    return False
