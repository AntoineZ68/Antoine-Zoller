"""Étape 2 — Classification des pièces.

Déterministe d'abord : la frontière entre deux pièces et le type de la
plupart des pièces se déduisent directement de l'en-tête de la page (les
PV français utilisent des intitulés standardisés — "PROCÈS-VERBAL DE...",
"CERTIFICAT MÉDICAL", etc.). Le modèle de langage n'intervient qu'en
secours, pour les pièces dont l'en-tête ne correspond à aucun motif connu,
et seulement si le mode --offline n'est pas actif.

Aucune pièce n'est devinée : sous le seuil de confiance, elle part en
"Non identifié" avec statut_revision='a_relire'.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .config import Config
from .db import TYPES_HORS_PROCEDURE
from .llm import ErreurModeOffline, extraire_json, obtenir_provider, tranches_de_texte
from .regex_patterns import cote_de_base, detecter_date_heure_acte, texte_sans_entete
from .titres import titrer_pieces

CATEGORIES = [
    "PV d'audition",
    "PV d'audition libre",
    "PV de plainte",
    "PV d'interpellation",
    "PV de surveillance",
    "PV de notification de placement en garde à vue",
    "PV de notification des droits",
    "PV de prolongation de garde à vue",
    "PV de fin de garde à vue",
    "Certificat médical",
    "PV d'entretien avocat",
    "PV de perquisition",
    "PV de saisie",
    "PV de constatations",
    "PV de saisine",
    "PV de synthèse",
    "PV de confrontation",
    "PV de renseignement",
    "PV de placement sous scellés",
    "PV d'exploitation",
    "PV d'enquête de voisinage",
    "PV de découverte",
    "PV de restitution",
    "PV d'identification",
    "PV de recherches",
    "Ordonnance du juge des libertés et de la détention",
    "Réquisition",
    "Réquisitoire introductif",
    "PV d'interrogatoire de première comparution",
    "Rapport d'expertise",
    "Analyse téléphonique",
    "Retranscription",
    "Soit-transmis",
    "Enquête de personnalité",
    "Casier judiciaire",
    "Relevé bancaire",
    "Facture",
    "Signalement (article 40 CPP ou L.823-12 du Code de commerce)",
    "Correspondance saisie (e-mail, courrier, SMS)",
    "Pièce de procédure – autre",
    "Bordereau / inventaire des pièces",
    "Document de l'avocat (hors procédure)",
    "Non identifié",
]

TYPE_BORDEREAU = "Bordereau / inventaire des pièces"
# Couverture du dossier, notes de parloir, annotations de l'avocat : jamais
# lues comme des pièces de la procédure (observé : des notes de parloir
# manuscrites restituées comme « déclarations » du mis en cause dans la
# chronologie). Exclues des faits, des actes, des questions.
TYPE_DOCUMENT_AVOCAT = "Document de l'avocat (hors procédure)"
assert (TYPE_BORDEREAU, TYPE_DOCUMENT_AVOCAT) == TYPES_HORS_PROCEDURE
RE_DOCUMENT_AVOCAT = re.compile(
    r"\bparloir\b|cabinet\s+de\s+ma[îi]tre|\btoque\b|[àa]\s+faire\s*:|notes?\s+d['’]entretien|\bhonoraires\b",
    re.IGNORECASE,
)
# « Feuillet 2/2 », « Page 1/2 » : le format des PV de police (LRPPN) et des
# décisions. Un feuillet 1 ouvre une pièce, un feuillet suivant la continue —
# plus sûr qu'un intitulé, que l'en-tête répété sur chaque feuillet imite.
RE_FEUILLET = re.compile(r"\b(?:Feuillet|Page)\s*(?:n[°o]\s*)?(\d{1,3})\s*/\s*(\d{1,3})\b", re.IGNORECASE)
LIGNES_FEUILLET = 15

# Règles déterministes : (motifs devant TOUS apparaître dans l'en-tête, type).
# Évaluées dans l'ordre ; la première règle satisfaite l'emporte. Ni exhaustif
# ni figé : chaque dossier réel testé peut révéler une formulation nouvelle à
# ajouter — une pièce qui ne correspond à aucune règle part en "Non identifié"
# pour relecture humaine, elle n'est jamais devinée.
REGLES_MOTS_CLES: list[tuple[list[str], str]] = [
    (["BORDEREAU"], TYPE_BORDEREAU),
    (["INVENTAIRE DES PIECES"], TYPE_BORDEREAU),
    (["NOTIFICATION", "PLACEMENT", "GARDE À VUE"], "PV de notification de placement en garde à vue"),
    (["NOTIFICATION", "MESURE", "GARDE À VUE"], "PV de notification de placement en garde à vue"),
    (["NOTIFICATION", "DROITS"], "PV de notification des droits"),
    (["PROLONGATION", "GARDE À VUE"], "PV de prolongation de garde à vue"),
    (["FIN DE GARDE À VUE"], "PV de fin de garde à vue"),
    # Repli sur un intitulé qui ne dit jamais littéralement "NOTIFICATION" —
    # un vrai PV rencontré titre simplement "PROCÈS-VERBAL DE PLACEMENT EN
    # GARDE À VUE", la notification des droits n'étant qu'une section du
    # même document. Vérifié après les règles plus spécifiques ci-dessus
    # (prolongation, fin) pour ne jamais les court-circuiter.
    (["PLACEMENT", "GARDE À VUE"], "PV de notification de placement en garde à vue"),
    # Avant les autres mots-clés : « PROCÈS-VERBAL DE SAISINE ET DE
    # CONSTATATIONS » ouvre la procédure ; c'est l'acte de référence de la
    # chronologie (voir contradictions._actes_avant_saisine).
    (["VERBAL DE SAISINE"], "PV de saisine"),
    (["INTERPELLATION"], "PV d'interpellation"),
    (["SURVEILLANCE"], "PV de surveillance"),
    (["CERTIFICAT MÉDICAL"], "Certificat médical"),
    (["ENTRETIEN", "AVOCAT"], "PV d'entretien avocat"),
    (["PERQUISITION"], "PV de perquisition"),
    (["SAISIE"], "PV de saisie"),
    (["CONSTATATIONS"], "PV de constatations"),
    (["SYNTHÈSE"], "PV de synthèse"),
    (["SOIT-TRANSMIS"], "Soit-transmis"),
    (["ENQUÊTE DE PERSONNALITÉ"], "Enquête de personnalité"),
    (["CASIER JUDICIAIRE"], "Casier judiciaire"),
    # Avant RÉQUISITION : « ORDONNANCE D'AUTORISATION D'INTERCEPTION —
    # RÉQUISITION » est une décision du juge, pas une réquisition.
    (["AUTORISATION", "INTERCEPTION"], "Ordonnance du juge des libertés et de la détention"),
    (["DETENTION PROVISOIRE"], "Ordonnance du juge des libertés et de la détention"),
    (["RÉQUISITOIRE"], "Réquisitoire introductif"),
    (["RÉQUISITION"], "Réquisition"),
    (["INTERROGATOIRE", "COMPARUTION"], "PV d'interrogatoire de première comparution"),
    (["RAPPORT D'ANALYSE"], "Rapport d'expertise"),
    (["EXPERTISE"], "Rapport d'expertise"),
    (["ANALYSE TÉLÉPHONIQUE"], "Analyse téléphonique"),
    (["TRANSCRIPTION"], "Retranscription"),
    # Deux personnes répondent tour à tour : ni « audition » ni pièce
    # inconnue (observé : classée « Non identifié », déclarations perdues).
    (["CONFRONTATION"], "PV de confrontation"),
    # « PROCÈS-VERBAL DE DÉPÔT DE PLAINTE » : une audition de la victime,
    # avec ses questions et réponses (observé : classée « Non identifié »,
    # déclarations de la plaignante perdues). « DE PLAINTE » et non
    # « PLAINTE » : un « SERVICE DES PLAINTES » en en-tête d'une audition de
    # témoin ne doit pas l'emporter.
    (["DE PLAINTE"], "PV de plainte"),
    (["PLAIGNANT"], "PV de plainte"),
    (["AUDITION LIBRE"], "PV d'audition libre"),
    (["AUDITION"], "PV d'audition"),
    # Pièces documentaires saisies comme preuve plutôt que rédigées par un
    # service enquêteur : leur en-tête suit un format bancaire/commercial
    # standardisé, pas la nomenclature des PV — reconnaissables sur un
    # mot-clé aussi fiable qu'un intitulé de PV classique.
    (["EXTRAIT DE COMPTE"], "Relevé bancaire"),
    (["RELEVÉ DE COMPTE"], "Relevé bancaire"),
    (["RELEVÉ BANCAIRE"], "Relevé bancaire"),
    (["FACTURE"], "Facture"),
    (["INVOICE"], "Facture"),
    # Pièces courantes sans mot-clé plus spécifique (observé : dix pièces
    # « Non identifié » sur deux dossiers d'essai de 40 pages). En dernier,
    # et ancrées sur « VERBAL DE » : « BRIGADE DE RECHERCHES » ou « SERVICE
    # D'IDENTIFICATION » dans l'en-tête d'une audition ne doivent jamais
    # l'emporter sur son intitulé.
    (["SOUS SCELLÉS"], "PV de placement sous scellés"),
    (["ENQUÊTE DE VOISINAGE"], "PV d'enquête de voisinage"),
    (["VERBAL DE RENSEIGNEMENT"], "PV de renseignement"),
    (["VERBAL D'EXPLOITATION"], "PV d'exploitation"),
    (["VERBAL DE DÉCOUVERTE"], "PV de découverte"),
    (["VERBAL DE RESTITUTION"], "PV de restitution"),
    (["VERBAL D'IDENTIFICATION"], "PV d'identification"),
    (["VERBAL DE RECHERCHE"], "PV de recherches"),
]

RE_TITRE = re.compile(r"^[A-ZÀ-Ÿ0-9°'’«»()/\-–—\s.,]{8,}$")
RE_SERVICE = re.compile(
    r"\b(brigade\s+de\s+gendarmerie\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+|"
    r"commissariat\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+|"
    r"Procureur\s+de\s+la\s+République\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+)\b",
    re.IGNORECASE,
)
RE_PERSONNE = re.compile(r"\b([A-ZÀ-Ÿ][a-zà-ÿ]+)\s+([A-ZÀ-Ÿ]{2,}(?:-[A-ZÀ-Ÿ]{2,})?)\b")
# Majuscules accentuées et noms composés compris : « JÉRÔME VASSEUR
# (TÉMOIN) » n'était pas reconnu, et le témoin manquait au dossier
# (observé sur un dossier d'essai de 40 pages) ; « JEAN-MARC », « DUPONT-
# MOREAU » non plus.
_MAJ = "A-ZÀ-ÖØ-Þ"
_LETTRE = "A-Za-zÀ-ÖØ-öø-ÿ"
# Particules : « AGNÈS LE ROUX (TÉMOIN) » donnait « Le ROUX », prénom perdu
# et particule prise pour un prénom (observé, dossier d'essai de 40 pages).
_PARTICULES = r"(?:LE|LA|DE|DU|DES|DA|DI|DOS|VAN|VON|DER|EL|BEN|AÏT|AIT|MAC|MC)"
RE_ROLE_TAG = re.compile(
    rf"(?<![{_LETTRE}])((?!{_PARTICULES}\s)[{_MAJ}][{_LETTRE}]+(?:-[{_MAJ}][{_LETTRE}]+)?)\s+"
    rf"((?:{_PARTICULES}\s+)*[{_MAJ}]{{2,}}(?:[-'][{_MAJ}]{{2,}})*)\s*\((MIS EN CAUSE|VICTIME|TÉMOIN)\)"
)
ROLE_PAR_TAG = {
    "MIS EN CAUSE": "mis_en_cause",
    "VICTIME": "victime",
    "TÉMOIN": "témoin",
}
ROLES_VALIDES = ("mis_en_cause", "victime", "témoin", "expert", "enqueteur")

TYPES_AUDITION = ("PV d'audition", "PV d'audition libre", "PV de plainte")
TYPE_CONFRONTATION = "PV de confrontation"

# Types où le premier nom "Prénom NOM" mentionné dans la pièce désigne sans
# ambiguïté la personne concernée (le texte la nomme tôt et explicitement,
# ex. "notifions à Julien MORVANNEC..."). Explicitement PAS les auditions,
# où un tiers peut être cité en passant dans une question — voir le bug
# corrigé précédemment.
TYPES_PERSONNE_PAR_PREMIERE_MENTION = (
    "PV de notification de placement en garde à vue",
    "PV de notification des droits",
    "PV de prolongation de garde à vue",
    "PV de fin de garde à vue",
    "Certificat médical",
    "PV d'entretien avocat",
    "PV de perquisition",
    "PV d'interpellation",
)


RE_SUFFIXE_METADONNEE_COLONNE = re.compile(
    r"\s+(Feuillet\b.*|Date\s*:.*|Heure\b.*|N°\s*\S*\s*:.*)$", re.IGNORECASE
)


def _sans_suffixe_metadonnee(ligne: str) -> str:
    """Certains PV disposent le titre et les repères (date, heure, feuillet)
    sur deux colonnes ; pdfplumber, qui lit par position verticale, recolle
    alors les deux colonnes sur une même ligne physique ("DIRECTION ... POLICE
    JUDICIAIRE Feuillet N° 1/2"). Sans ce nettoyage, le fragment de colonne
    de droite (casse mixte) empêche de reconnaître la ligne de gauche comme
    un intitulé — et par ricochet, empêche de détecter le début d'une
    nouvelle pièce."""
    return RE_SUFFIXE_METADONNEE_COLONNE.sub("", ligne)


# Taches de scan lues par l'OCR en bord de ligne : « _ PROCÈS-VERBAL
# D'INTERPELLATION » n'était plus un intitulé, et la pièce était fondue dans
# la précédente (observé sur des pages numérisées dégradées).
PARASITES_BORD_DE_LIGNE = "_|~•*·`¦"


def _est_titre(ligne: str) -> bool:
    ligne = _sans_suffixe_metadonnee(ligne.strip().strip(PARASITES_BORD_DE_LIGNE).strip()).strip()
    return bool(ligne) and len(ligne) >= 8 and bool(RE_TITRE.match(ligne))


RE_METADONNEE_ENTETE = re.compile(
    r"^(N°\s|N°$|FEUILLET\b|DATE\b|HEURE\b|PROCÉDURE\b|[A-Za-zÀ-ÿ°'\s]{1,30}:\s*\S)",
    re.IGNORECASE,
)


def _entete_etendu(texte_page: str) -> str:
    """Regroupe les lignes consécutives en capitales en tête de page (ex.
    "COTE X / TITRE" suivi du nom du service rédacteur, lui aussi en
    capitales). Sert de seule base à la classification par mots-clés : le
    reste du corps du texte est exclu, pour qu'une pièce ne soit jamais
    classée à tort à cause d'un mot-clé mentionné en passant dans une
    phrase (ex. un interrogatoire qui évoque "le réquisitoire introductif"
    en référence à une autre pièce du dossier).

    Certains PV placent des lignes de référence ("N° Procédure : ...",
    "Feuillet N° 1/2", "COMMISSARIAT DE ... COTE : D.3") AVANT le titre
    plutôt qu'après. Elles ne sont pas des titres — elles ne doivent pas
    non plus arrêter la lecture avant d'avoir atteint le vrai titre.

    Tant qu'aucun titre n'a encore été rencontré, on saute donc ces lignes
    de tête, quelle que soit leur forme (nombre borné). Auparavant seul un
    motif de métadonnée précis était sauté, et toute autre ligne arrêtait
    net la lecture : un en-tête sur deux colonnes, que l'extraction PDF
    recolle en une seule ligne mêlant service et référence ("COMMISSARIAT
    DE POLICE DE LYON 3/6 COTE : D.3"), n'était ni un titre (il contient
    ":") ni une métadonnée reconnue (trop longue, avec des chiffres). Le
    titre placé juste en dessous n'était alors JAMAIS lu, et toutes les
    pièces du dossier retombaient en "Non identifié" — mesuré sur un
    dossier d'essai de 47 pages : 10 pièces sur 10 non classées.

    Une fois le titre trouvé, en revanche, la première ligne qui n'en est
    pas un arrête bien la lecture : c'est le début du corps du texte, et
    le classer par mots-clés ferait dériver la classification."""
    lignes_entete = []
    lignes_tete_sautees = 0
    for ligne in texte_page.splitlines():
        ligne_nettoyee = ligne.strip()
        if not ligne_nettoyee:
            continue
        if _est_titre(ligne_nettoyee):
            lignes_entete.append(ligne_nettoyee)
            continue
        if lignes_tete_sautees < 6 and (not lignes_entete or RE_METADONNEE_ENTETE.match(ligne_nettoyee)):
            lignes_tete_sautees += 1
            continue
        break
    return " ".join(lignes_entete)


NB_LIGNES_EXAMINEES_FRONTIERE = 3
# Les bandes noires d'un scan (bord de page, agrafe, ombre de la vitre)
# sont lues par l'OCR comme de courtes lignes de charabia (« RE », « il »,
# « En 27 | ») en tête de page : elles repoussaient l'intitulé au-delà des
# lignes examinées, et la pièce était fondue dans la précédente (observé :
# l'avis à l'avocat et au procureur, et l'exploitation d'un téléphone,
# perdus dans leurs voisines sur un dossier de test de 30 pages).
NB_LIGNES_BRUIT_MAX = 6
RE_MOT_PLEIN = re.compile(r"[A-Za-zÀ-ÿ]{3,}")


def _bruit_de_scan(ligne: str) -> bool:
    """Ligne courte sans aucun mot de trois lettres : bruit d'OCR, ou cote
    seule (« D.09 »), qui ne décide jamais d'une frontière."""
    ligne = ligne.strip()
    return len(ligne) < 12 and not RE_MOT_PLEIN.search(ligne.replace("COTE", ""))


def _detecter_pieces_par_page(pages: list[sqlite3.Row]) -> list[dict]:
    """Frontière déterministe : une page démarre une nouvelle pièce si l'une
    de ses toutes premières lignes non vides est un intitulé en capitales,
    ou si elle provient d'un fichier PDF différent de la page précédente ;
    sinon elle prolonge la précédente.

    Certaines pages portent un tampon de transmission avant le vrai titre
    (ex. "FAX FROM: COMMISSARIAT VENISSIEUX -- TO: PJ LYON STUPS -- ... --
    PAGE 1/2") : cette ligne contient des ":" qui l'excluent d'office du
    test de titre (jamais dans un intitulé légitime), donc regarder
    uniquement la toute première ligne ratait le titre juste en dessous —
    fusionnant deux pièces distinctes en une seule.

    Un dossier réel arrive souvent en plusieurs PDF distincts (un relevé
    bancaire, un e-mail saisi, une facture...) dont beaucoup ne commencent
    pas par un intitulé en capitales — un e-mail commence par "De :",
    "Envoyé :". Sans ce second signal, deux fichiers sans rapport entre eux
    finissaient purement et simplement fusionnés en une seule pièce, avec
    le type, la date et les citations de l'un attribués à l'autre. Démarrer
    systématiquement une nouvelle pièce à chaque changement de fichier est
    plus sûr qu'un intitulé manqué : au pire une pièce réellement scindée
    entre deux fichiers ressort en deux pièces "Non identifié" signalées
    pour relecture — jamais une fusion silencieuse et invisible."""
    pieces: list[dict] = []
    for page in pages:
        nouveau_fichier = bool(pieces) and page["fichier_source"] != pieces[-1]["pages"][-1]["fichier_source"]
        premieres_lignes = [
            l for l in page["texte"].splitlines()[:NB_LIGNES_BRUIT_MAX + NB_LIGNES_EXAMINEES_FRONTIERE * 2]
            if l.strip() and not _bruit_de_scan(l)
        ][:NB_LIGNES_EXAMINEES_FRONTIERE]
        feuillet = _feuillet(page["texte"])
        if not pieces or nouveau_fichier or _document_avocat(page["texte"]):
            nouvelle_piece = True
        elif feuillet:
            nouvelle_piece = feuillet[0] == 1
        else:
            nouvelle_piece = any(_est_titre(l) for l in premieres_lignes)
        if pieces and not nouveau_fichier and _document_avocat(pieces[-1]["pages"][-1]["texte"]) and not _document_avocat(page["texte"]):
            nouvelle_piece = True
        if nouvelle_piece:
            pieces.append({"page_debut": page["numero_global"], "page_fin": page["numero_global"], "pages": [page]})
        else:
            pieces[-1]["page_fin"] = page["numero_global"]
            pieces[-1]["pages"].append(page)
    return pieces


def _feuillet(texte: str) -> tuple[int, int] | None:
    lignes = [l for l in texte.splitlines() if l.strip()]
    for ligne in lignes[:LIGNES_FEUILLET] + lignes[-4:]:
        m = RE_FEUILLET.search(ligne)
        if m and 0 < int(m.group(1)) <= int(m.group(2)):
            return int(m.group(1)), int(m.group(2))
    return None


def _document_avocat(texte: str) -> bool:
    """Page de l'avocat : marqueur explicite (parloir, cabinet de Maître,
    « À faire : »), sans en-tête de procès-verbal."""
    return bool(RE_DOCUMENT_AVOCAT.search(texte)) and not re.search(
        r"PROC[ÈE]S[- ]?VERBAL|\bNous,\s", texte[:600], re.IGNORECASE
    )


RE_OBJET = re.compile(r"\bObjet\s*:\s*([^\n]{4,120})", re.IGNORECASE)
RE_INTITULE_FORT = re.compile(
    r"^\s*(?:BORDEREAU|INVENTAIRE|PROC[ÈE]S[- ]VERBAL\s+D|ORDONNANCE|R[ÉE]QUISITOIRE|CERTIFICAT|AUTORISATION|RAPPORT)[^a-z]{4,}$"
)


def intitule_de_classement(texte_page: str) -> str:
    """Ce sur quoi la pièce est classée : l'en-tête en capitales, l'objet
    d'un PV de police (« Objet : Audition de personne gardée à vue — n° 1 »,
    lu « PROCÈS-VERBAL D'AUDITION DE PERSONNE GARDÉE À VUE ») et tout
    intitulé fort des premières lignes (« BORDEREAU DES PIÈCES »). Observé
    sur un vrai format de PV : l'intitulé ne disait que « PROCÈS-VERBAL », et
    trois auditions de garde à vue passaient pour des interrogatoires de
    première comparution."""
    parties = [_entete_etendu(texte_page)]
    lignes = [l.strip() for l in texte_page.splitlines() if l.strip()][:LIGNES_FEUILLET]
    m = RE_OBJET.search("\n".join(lignes))
    if m and re.search(r"PROC[ÈE]S[- ]?VERBAL", "\n".join(lignes), re.IGNORECASE):
        objet = re.split(r"\s{2,}|Feuillet|N°", m.group(1))[0].strip(" -–—")
        liaison = "D'" if _sans_accents(objet[:1]).upper() in "AEIOUYH" else "DE "
        parties.insert(0, f"PROCÈS-VERBAL {liaison}{objet.upper()}")
    parties += [l for l in lignes if RE_INTITULE_FORT.match(l)]
    return " ".join(p for p in parties if p)


def _sans_accents(texte: str) -> str:
    """Les majuscules françaises omettent souvent les accents en pratique
    ("GARDE A VUE", "REQUISITOIRE") — les règles de classification doivent
    reconnaître les deux écritures plutôt que de dépendre d'une convention
    typographique qu'aucun dossier réel ne respecte de façon uniforme."""
    forme = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in forme if not unicodedata.combining(c))


def _classifier_type_deterministe(texte_entete: str) -> tuple[str, float]:
    majuscules = _sans_accents(texte_entete.upper()).replace("’", "'")
    for motifs, type_ in REGLES_MOTS_CLES:
        if all(_sans_accents(motif) in majuscules for motif in motifs):
            return type_, 1.0
    return "Non identifié", 0.0


def _classifier_type_llm(config: Config, texte: str, console: Console, compteur: dict[str, int]) -> tuple[str, float]:
    try:
        provider = obtenir_provider(config)
        reponse = provider.appeler(
            systeme=(
                "Tu classes une pièce de procédure pénale française dans une des "
                "catégories suivantes, à l'exclusion de toute autre : "
                + ", ".join(CATEGORIES)
                + ". Réponds uniquement en JSON : "
                '{"type": "...", "confiance": 0.0 à 1.0}. '
                "Si aucune catégorie ne correspond clairement, réponds "
                '{"type": "Non identifié", "confiance": 0.0}. '
                "Ne déduis rien qui ne soit pas explicitement lisible dans le texte."
            ),
            prompt=texte[:4000],
            modele=config.modele_classification,
        )
        compteur["tokens_in"] += reponse.tokens_in
        compteur["tokens_out"] += reponse.tokens_out
        data = extraire_json(reponse.texte)
        type_ = data.get("type", "Non identifié")
        confiance = float(data.get("confiance", 0.0))
        if type_ not in CATEGORIES:
            return "Non identifié", 0.0
        return type_, confiance
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001 — on dégrade en "Non identifié", jamais une exception qui casse le run
        console.print(f"  [classify] échec de l'appel LLM ({exc}), pièce marquée Non identifié.")
        return "Non identifié", 0.0


def _detecter_service(texte: str) -> str | None:
    m = RE_SERVICE.search(texte)
    return m.group(1) if m else None


def _detecter_personnes_citees(texte: str) -> list[str]:
    return sorted({f"{p} {n}" for p, n in RE_PERSONNE.findall(texte)})


def _detecter_personnes_avec_role(texte: str) -> list[tuple[str, str]]:
    resultats = []
    for prenom, nom, tag in RE_ROLE_TAG.findall(texte):
        prenom = "-".join(partie.capitalize() for partie in prenom.split("-"))
        resultats.append((f"{prenom} {nom.upper()}", ROLE_PAR_TAG[tag]))
    return resultats


def identifier_declarant(db: sqlite3.Connection, page_entete: str) -> int | None:
    """Identifie la personne d'une pièce à partir du rôle explicitement tagué
    dans son en-tête ("(MIS EN CAUSE)", "(VICTIME)", "(TÉMOIN)") — jamais en
    cherchant le premier nom mentionné n'importe où dans le texte, qui
    attraperait aussi bien un tiers cité en passant (ex. dans une question)."""
    roles = _detecter_personnes_avec_role(page_entete)
    if not roles:
        return None
    # Par le nom seul : la personne a pu être enregistrée avec un autre rôle
    # (entendue comme témoin, puis comme mise en cause) — voir
    # _enregistrer_personnes_taguees.
    nom, _ = roles[0]
    row = db.execute("SELECT id FROM personnes WHERE nom = ?", (nom,)).fetchone()
    return row["id"] if row else None


# Quand une même personne est désignée avec des rôles différents selon les
# auditions, celui qui compte pour l'avocat l'emporte.
PRIORITE_ROLE_TAGUE = {"mis_en_cause": 3, "victime": 2, "témoin": 1}


def _enregistrer_personnes_taguees(db: sqlite3.Connection, textes: list[str]) -> None:
    """Enregistre, AVANT toute autre identification, chaque personne que le
    dossier désigne explicitement en en-tête d'audition (« (MIS EN CAUSE) »,
    « (VICTIME) », « (TÉMOIN) »).

    Sans cela, la première pièce qui nommait une personne fixait son rôle
    pour tout le dossier — observé sur un dossier d'essai de 46 pages : le
    modèle, appelé en dernier recours sur un PV de surveillance, classait
    « témoin » un homme que ses deux auditions désignent ensuite « MIS EN
    CAUSE » ; il manquait aux mis en cause et ses auditions n'étaient plus
    rattachées par leur en-tête."""
    roles: dict[str, str] = {}
    for texte in textes:
        trouvees = _detecter_personnes_avec_role(texte)
        if not trouvees:
            # Sans rôle entre parenthèses : « Personne entendue : NOM
            # Prénom » sous l'intitulé « AUDITION DE TÉMOIN ».
            nom, role = personne_du_champ(texte), role_par_intitule(_entete_etendu(texte))
            if nom and role:
                trouvees = [(nom, role)]
        for nom, role in trouvees:
            if PRIORITE_ROLE_TAGUE[role] > PRIORITE_ROLE_TAGUE.get(roles.get(nom, ""), 0):
                roles[nom] = role
    for nom, role in roles.items():
        _upsert_personne(db, nom, role)


RE_MOT = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _mots(texte: str) -> frozenset[str]:
    """Ensemble des mots d'un texte, insensible à la ponctuation, à la casse
    et aux traits d'union — sert à comparer deux écritures d'un même nom
    ("DUPONT Julie" / "Julie DUPONT" / "Julie, Clémence DUPONT") sans exiger
    une chaîne identique. Même raisonnement que _personne_par_nom dans
    chrono.py, qui résout déjà ce problème de l'autre côté du pipeline : le
    français administratif écrit NOM Prénom, un modèle reformule
    spontanément en Prénom NOM, et c'est la même personne.

    Les mots d'une seule lettre sont ignorés : une initiale ("É. COMBES")
    n'apporte rien à la comparaison et ferait échouer un rapprochement par
    ailleurs correct."""
    return frozenset(m.group(0).lower() for m in RE_MOT.finditer(texte) if len(m.group(0)) > 1)


def _personne_par_mots(db: sqlite3.Connection, nom: str) -> int | None:
    """« Kevin LEROY » retrouve « LEROY Kevin » : même ensemble de mots."""
    mots = _mots(nom)
    for row in db.execute("SELECT id, nom FROM personnes ORDER BY id"):
        if _mots(row["nom"]) == mots:
            return row["id"]
    return None


def _motif_nom(nom: str) -> re.Pattern:
    """Un nom connu, dans un ordre ou l'autre (« Kevin LEROY », « LEROY
    Kevin »)."""
    mots = nom.split()
    prenoms = [m for m in mots if not m.isupper()]
    noms = [m for m in mots if m.isupper()]
    ordres = {" ".join(mots), " ".join(noms + prenoms)}
    variantes = "|".join(r"\s+".join(re.escape(m) for m in ordre.split()) for ordre in ordres)
    return re.compile(rf"(?<![\w-])(?:{variantes})(?![\w-])", re.IGNORECASE)


def _premier_mis_en_cause_nomme(db: sqlite3.Connection, texte: str) -> tuple[int, int] | None:
    """(position, id) du premier mis en cause déjà identifié nommé dans le
    texte, ou None."""
    meilleur = None
    for row in db.execute("SELECT id, nom FROM personnes WHERE role = 'mis_en_cause'"):
        m = _motif_nom(row["nom"]).search(texte)
        if m and (meilleur is None or m.start() < meilleur[0]):
            meilleur = (m.start(), row["id"])
    return meilleur


def _position_mention(texte: str, prenom_nom: tuple[str, str]) -> int:
    m = _motif_nom(f"{prenom_nom[0]} {prenom_nom[1].upper()}").search(texte)
    return m.start() if m else len(texte)


def _upsert_personne(db: sqlite3.Connection, nom: str, role: str) -> int:
    """Recherche par nom seul, jamais par (nom, rôle) : la même personne
    réelle, reconnue par deux pièces différentes avec un rôle différent
    (une pièce mal ciblée lui attribue par erreur le rôle par défaut d'une
    autre), ne doit jamais se retrouver dupliquée dans la table — un
    avocat qui voit "Julie DUPONT" citée deux fois avec deux rôles
    différents croit à deux personnes distinctes. Le rôle du premier
    enregistrement l'emporte ; un tel désaccord signale un bug d'extraction
    en amont à corriger à la source, pas quelque chose à trancher ici."""
    row = db.execute("SELECT id FROM personnes WHERE nom = ?", (nom,)).fetchone()
    if row:
        return row["id"]
    cur = db.execute("INSERT INTO personnes (nom, role) VALUES (?, ?)", (nom, role))
    return cur.lastrowid


TITRES_A_EXCLURE = {
    "capitaine", "commandant", "lieutenant", "colonel", "major", "brigadier",
    "adjudant", "gardien", "maréchal", "docteur", "maître", "monsieur", "madame",
    # Magistrats, greffe et avocats : « M. Antoine ROQUIER, Procureur de la
    # République » sur un PV de prolongation faisait du procureur un mis en
    # cause (observé sur le banc d'essai IA).
    "procureur", "procureure", "vice-procureur", "substitut", "juge", "magistrat",
    "greffier", "greffière", "avocat", "avocate",
}

# « agent » et « officier » ne désignent un enquêteur que suivis de leur
# service : « Marc TESSIER, agent de police judiciaire » est un enquêteur,
# « Julien MORVANNEC, agent immobilier » peut être le mis en cause.
RE_FONCTION_ENQUETEUR_APRES = re.compile(
    r"(?:agent|officier)s?\s+de\s+(?:la\s+)?(?:police|gendarmerie)", re.IGNORECASE
)
FONCTIONS_ENQUETEUR_AVANT = {"agent", "officier"}

# Abréviations d'honorifiques ("Me SCHMITT") : elles matchent le même motif
# Prénom-NOM que "Jean-Marc TARDIEU" (une majuscule suivie de minuscules,
# puis un mot tout en capitales) — sans cette exclusion, le premier avocat
# mentionné en passant ("Demande à s'entretenir avec un avocat (Me
# SCHMITT)") est pris pour la personne concernée par la pièce, alors qu'il
# ne l'est jamais.
HONORIFIQUES_ABREGES = {"me", "mme", "mlle", "m"}

# Certains PV donnent l'identité de la personne concernée dans un champ
# encadré ("Personne : TARDIEU Jean-Marc") plutôt qu'en prose — et
# l'écrivent alors NOM Prénom, jamais Prénom NOM (RE_PERSONNE ne la
# reconnaît donc pas du tout : "TARDIEU" est tout en capitales, pas
# "Majuscule puis minuscules"). Motif dédié, volontairement ancré à ce
# libellé de champ précis plutôt que généralisé à tout le texte : une
# règle "NOM Prénom" sans ancrage confondrait presque tout titre en
# capitales suivi d'un mot de la phrase suivante avec un nom de personne.
#
# Le libellé varie d'un service à l'autre : « Personne entendue : », « Personne
# gardée à vue : »… (observé : aucune personne identifiée sur un dossier
# d'essai écrit comme les vrais PV). L'ordre Prénom NOM y est aussi admis.
_NOM_MAJUSCULES = r"[A-ZÀ-Ÿ]{2,}(?:-[A-ZÀ-Ÿ]{2,})?"
_PRENOM = r"[A-ZÀ-Ÿ][a-zà-ÿ]+(?:-[A-ZÀ-Ÿ][a-zà-ÿ]+)?"
RE_PERSONNE_CHAMP = re.compile(
    r"\bPersonne(?:\s+(?:entendue|auditionn[ée]e|gard[ée]e\s+[àa]\s+vue|interpell[ée]e|concern[ée]e"
    r"|mise\s+en\s+cause|soup[çc]onn[ée]e))?\s*:\s*"
    rf"(?:(?P<nom>{_NOM_MAJUSCULES})\s+(?P<prenom>{_PRENOM})|(?P<prenom2>{_PRENOM})\s+(?P<nom2>{_NOM_MAJUSCULES})\b)"
)

# Autres formulations, tout aussi standard, du même besoin NOM Prénom : "la
# personne dénommée : NOM Prénom" (constatation de présence, notification de
# placement...) et "déclare se nommer (verbalement) : NOM Prénom"
# (interpellation — l'individu s'identifie lui-même). Une seule expression
# régulière plutôt que plusieurs motifs testés indépendamment : search()
# doit renvoyer la toute première déclaration d'identité de la page, quelle
# que soit sa formulation — jamais "dénommé(e)" par défaut même quand une
# autre déclaration légitime la précède. Régression réelle : sur un PV
# d'interpellation, le suspect se nomme lui-même en premier ("se nommer
# verbalement : BENALI Sofiane"), et la victime n'est que "dénommée" bien
# plus loin, en passant, comme propriétaire des objets volés retrouvés sur
# lui — sans cette unification, "dénommée" gagnait toujours, quelle que
# soit sa position, et la victime héritait par erreur du rôle par défaut
# "mis_en_cause" de cette pièce. Ancré sur ces tours de phrase précis
# plutôt que généralisé à tout "NOM Prénom" du texte, pour la même raison
# que RE_PERSONNE_CHAMP.
#
# « au nommé LEROY Kevin », « du nommé », « la nommée » : même formule,
# sans le préfixe (observé sur un dossier d'essai écrit comme les vrais PV).
RE_PERSONNE_DENOMMEE = re.compile(
    r"\b(?:d[ée]nomm[ée]e?|se\s+nomm(?:e|er|ant)|(?:au|du|le|la)\s+nomm[ée]e?)\b[^:\n]{0,20}:?\s+"
    r"([A-ZÀ-Ÿ]{2,}(?:-[A-ZÀ-Ÿ]{2,})?)\s+([A-ZÀ-Ÿ][a-zà-ÿ]+(?:-[A-ZÀ-Ÿ][a-zà-ÿ]+)?)"
)


def _prenom_nom_du_champ(m: re.Match) -> tuple[str, str]:
    if m.group("nom"):
        return m.group("prenom"), m.group("nom")
    return m.group("prenom2"), m.group("nom2")


# « Le 14/03/2031 à 15h00, nous entendons Paul LEROUX, voisin, … » : la
# formule d'audition nomme la personne entendue (observé : audition de
# témoin sans déclarant, sur le dossier de contrôle).
RE_PERSONNE_ENTENDUE = re.compile(
    r"\b(?:entendons|auditionnons)\s+(?:(?:M\.|Mme|Mlle|Monsieur|Madame|Mademoiselle)\s+)?"
    rf"(?:(?P<nom>{_NOM_MAJUSCULES})\s+(?P<prenom>{_PRENOM})|(?P<prenom2>{_PRENOM})\s+(?P<nom2>{_NOM_MAJUSCULES})\b)"
)


def personne_du_champ(texte: str) -> str | None:
    """« Personne entendue : LEROY Kevin » ou « nous entendons Kevin LEROY »
    -> « Kevin LEROY » : la première des deux formules dans le texte."""
    trouves = [m for m in (RE_PERSONNE_CHAMP.search(texte), RE_PERSONNE_ENTENDUE.search(texte)) if m]
    if not trouves:
        return None
    prenom, nom = _prenom_nom_du_champ(min(trouves, key=lambda m: m.start()))
    return f"{prenom} {nom.upper()}"


# Rôle que l'intitulé du PV donne à la personne entendue, quand l'en-tête
# ne l'écrit pas entre parenthèses — le cas des vrais PV : « AUDITION DE
# PERSONNE GARDÉE À VUE », « DÉPÔT DE PLAINTE », « AUDITION DE TÉMOIN ».
# L'audition libre (art. 61-1 CPP) est celle d'un suspect. Deux rôles
# différents dans le même intitulé : aucun n'est retenu.
ROLES_PAR_INTITULE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bGARDEE?\s+A\s+VUE\b|\bAUDITION\s+LIBRE\b|\bMIS\s+EN\s+CAUSE\b|\bSUSPECT|\bINTERROGATOIRE\b"), "mis_en_cause"),
    (re.compile(r"\bDE\s+PLAINTE\b|\bPLAIGNANTE?\b|\bVICTIME\b|\bPARTIE\s+CIVILE\b"), "victime"),
    (re.compile(r"\bTEMOIN\b"), "témoin"),
]


def role_par_intitule(entete: str) -> str | None:
    majuscules = _sans_accents(entete.upper())
    roles = {role for motif, role in ROLES_PAR_INTITULE if motif.search(majuscules)}
    return roles.pop() if len(roles) == 1 else None


def _premiere_mention_hors_titres(texte: str) -> tuple[str, str] | None:
    """Le premier nom "Prénom NOM" mentionné n'est pas forcément le mis en
    cause : les PV nomment très souvent l'officier rédacteur, selon deux
    formulations — le titre AVANT le nom ("nous, capitaine Élodie
    BASTIER...") ou APRÈS, séparé par une virgule ("Nous, Gilles GAUTHIER,
    Capitaine de Police...", la formule d'auto-présentation la plus
    universelle des PV français). On exclut donc les mentions précédées OU
    suivies d'un titre ou d'un grade, ou dont le "prénom" capturé n'est en
    réalité qu'un honorifique abrégé — la personne concernée par la pièce
    est cherchée après ça."""
    m_champ = RE_PERSONNE_CHAMP.search(texte)
    if m_champ:
        return _prenom_nom_du_champ(m_champ)

    m_denommee = RE_PERSONNE_DENOMMEE.search(texte)
    if m_denommee:
        return m_denommee.group(2), m_denommee.group(1)

    for m in RE_PERSONNE.finditer(texte):
        if m.group(1).lower() in HONORIFIQUES_ABREGES:
            continue
        avant = texte[: m.start()].rstrip().split()
        dernier_mot = avant[-1].lower().rstrip(",.") if avant else ""
        if dernier_mot in TITRES_A_EXCLURE or dernier_mot in FONCTIONS_ENQUETEUR_AVANT:
            continue
        apres = texte[m.end() :].lstrip(" ,")
        premier_mot_apres = apres.split(" ", 1)[0].split(",", 1)[0].lower().rstrip(",.") if apres else ""
        if premier_mot_apres in TITRES_A_EXCLURE or RE_FONCTION_ENQUETEUR_APRES.match(apres):
            continue
        return m.group(1), m.group(2)
    return None


def _premiere_mention_ou_creation(db: sqlite3.Connection, texte: str, role_par_defaut: str) -> int | None:
    """Pour les pièces où le premier nom mentionné (hors officier
    rédacteur) désigne sans ambiguïté la personne concernée (voir
    TYPES_PERSONNE_PAR_PREMIERE_MENTION) : ces pièces existent
    structurellement à propos du mis en cause (placement, notification,
    prolongation, perquisition...), donc si la personne n'est pas encore
    enregistrée, on la crée avec le rôle par défaut plutôt que d'échouer
    silencieusement faute d'un tag qu'aucun de ces PV n'écrit jamais
    explicitement.

    Le texte est d'abord débarrassé de ses en-têtes/titres (texte_sans_entete) :
    un document à deux colonnes juxtapose parfois, sur la même ligne
    physique, la fin d'un bloc d'adresse ("...Cabinet du Procureur de la
    République") et le titre du PV qui suit ("PROCÈS-VERBAL DE...") — une
    fois recollés par pdfplumber, "République" (majuscule-minuscules) suivi
    d'un mot tout en capitales matche par erreur le motif Prénom-NOM, sans
    rapport avec la personne concernée. Observé en réel sur un vrai
    dossier."""
    texte = texte_sans_entete(texte, _est_titre)
    trouve = _premiere_mention_hors_titres(texte)
    # « Notifions à l'intéressé, LEROY Kevin, … », « la garde à vue de LEROY
    # Kevin » : un mis en cause déjà identifié, écrit NOM Prénom sans
    # formule d'identité. Retenu s'il est nommé avant toute autre personne ;
    # une identité déclarée (« Personne : », « dénommé ») prime toujours.
    ancre = RE_PERSONNE_CHAMP.search(texte) or RE_PERSONNE_DENOMMEE.search(texte)
    connu = None if ancre else _premier_mis_en_cause_nomme(db, texte)
    if connu is not None and (trouve is None or connu[0] < _position_mention(texte, trouve)):
        return connu[1]
    if not trouve:
        return None
    prenom, nom_famille = trouve
    nom = f"{prenom} {nom_famille.upper()}"
    row = db.execute("SELECT id FROM personnes WHERE nom = ?", (nom,)).fetchone()
    if row:
        return row["id"]
    return _upsert_personne(db, nom, role_par_defaut)


# Au-delà, on cesse de chercher : une identité qui n'apparaît ni dans les
# ~24 000 premiers caractères d'une pièce ni parmi les personnes déjà
# identifiées ne sera pas trouvée en continuant, et chaque tranche coûte un
# appel.
MAX_TRANCHES_IDENTIFICATION = 3


def identifier_personne_via_llm(
    config: Config,
    texte_piece: str,
    personnes_connues: list[tuple[str, str]],
    console: Console,
    compteur: dict[str, int],
) -> tuple[str, str] | None:
    """Repli sur le modèle pour identifier la personne concernée par une
    pièce quand aucun tag de rôle explicite n'est présent dans l'en-tête —
    jamais en --offline.

    Beaucoup de PV ne renomment pas la personne (ils disent "le gardé à
    vue", "l'intéressé") parce que son identité a déjà été établie plus tôt
    dans le dossier : on donne donc au modèle la liste des personnes déjà
    identifiées ailleurs, pour qu'il puisse la reconnaître par le contexte.
    Mais la vérifiabilité reste non négociable : le nom renvoyé doit soit
    correspondre à une personne déjà établie (donc déjà une identité
    vérifiée, pas une invention), soit être composé de mots réellement
    présents dans le texte de cette pièce précise. Un nom qui ne remplit
    aucune des deux conditions est rejeté, quelle que soit la confiance
    apparente du modèle.

    La comparaison se fait par ensemble de mots, pas par chaîne exacte : un
    PV écrit couramment l'identité en champs éclatés ("Nom : DUPONT
    Prénom : Julie, Clémence"), si bien que la chaîne "Julie DUPONT"
    n'existe littéralement nulle part dans la pièce — alors même que le
    modèle a parfaitement identifié la bonne personne. Exiger la chaîne
    exacte faisait rejeter en silence des identifications correctes, et le
    garde-fou anti-invention rejetait donc aussi les bonnes réponses. Un
    nom inventé reste rejeté : chacun de ses mots doit figurer dans la
    pièce.

    Une pièce longue est parcourue par tranches successives plutôt que
    tronquée à la première : l'identité peut être déclarée ailleurs qu'en
    tête (reprise d'audition, pièce jointe en fin de PV). On s'arrête dès
    qu'une tranche donne un nom vérifié, si bien que le cas courant ne coûte
    qu'un seul appel."""
    noms_connus = {n.lower(): n for n, _ in personnes_connues}
    mots_connus = {frozenset(_mots(n)): n for n, _ in personnes_connues}
    mots_piece = _mots(texte_piece)
    dernier_rejet: str | None = None

    try:
        provider = obtenir_provider(config)
        liste_connues = "\n".join(f"- {nom} ({role})" for nom, role in personnes_connues) or "(aucune)"
        systeme = (
            "Tu identifies la personne interrogée ou concernée par cette pièce de "
            "procédure pénale française, et son rôle. Rôles possibles, à l'exclusion "
            "de tout autre : " + ", ".join(ROLES_VALIDES) + ".\n"
            "Personnes déjà identifiées ailleurs dans ce dossier :\n"
            f"{liste_connues}\n"
            "Si cette pièce concerne l'une d'entre elles — même si le texte ne la "
            'nomme pas explicitement (ex. "le gardé à vue", "l\'intéressé") — réponds '
            "avec son nom EXACTEMENT comme il apparaît dans cette liste. Sinon, si le "
            "texte nomme explicitement quelqu'un d'autre, recopie ce nom EXACTEMENT "
            "comme il apparaît dans le texte. Le texte peut provenir d'une page "
            "numérisée mal reconnue : ne devine pas un nom à partir de caractères "
            "douteux. Réponds uniquement en JSON : "
            '{"nom": "...", "role": "..."}. Si tu ne peux déterminer la personne avec '
            'certitude ni par la liste ni par le texte, réponds {"nom": null, "role": null}.'
        )

        for tranche in tranches_de_texte(texte_piece)[:MAX_TRANCHES_IDENTIFICATION]:
            reponse = provider.appeler(
                systeme=systeme,
                prompt=tranche,
                modele=config.modele_classification,
            )
            compteur["tokens_in"] += reponse.tokens_in
            compteur["tokens_out"] += reponse.tokens_out
            data = extraire_json(reponse.texte)
            nom, role = data.get("nom"), data.get("role")
            if not nom or role not in ROLES_VALIDES:
                continue
            nom = sans_titre(nom)

            if nom.lower() in noms_connus:
                return noms_connus[nom.lower()], role

            mots_nom = _mots(nom)
            if mots_nom in mots_connus:
                return mots_connus[mots_nom], role

            # La vérification porte sur la pièce ENTIÈRE, pas sur la seule
            # tranche soumise : le nom peut être écrit en champs éclatés à un
            # endroit et repris au fil du texte à un autre.
            if nom.lower() in texte_piece.lower():
                return nom, role
            # Au moins deux mots exigés : un nom d'un seul mot ("Dupont") se
            # retrouve trop facilement par hasard dans une page pour valoir
            # vérification.
            if len(mots_nom) >= 2 and mots_nom <= mots_piece:
                return nom, role

            dernier_rejet = nom

        if dernier_rejet is not None:
            console.print(
                f"  [classify] identification par le modèle rejetée : {dernier_rejet!r} ni retrouvé "
                "littéralement dans la pièce, ni parmi les personnes déjà identifiées."
            )
        return None
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001 — dégrade en None, jamais une exception qui casse le run
        console.print(f"  [classify] échec de l'identification de personne par le modèle ({exc}).")
        return None


# --- Une personne, plusieurs écritures --------------------------------------
# L'OCR lit « DERRADJI » tantôt « DERRADIJI », tantôt « DERRADII », et le
# dossier écrit tantôt « NOM Prénom », tantôt « Prénom NOM » : observé sur un
# dossier de test, une seule personne devenait trois, et sa garde à vue,
# coupée en deux, ne donnait plus aucun délai. Même prénom, nom à une lettre
# près (deux pour un nom long) : la même personne.


def _distance(a: str, b: str) -> int:
    precedente = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        courante = [i]
        for j, cb in enumerate(b, 1):
            courante.append(min(precedente[j] + 1, courante[j - 1] + 1, precedente[j - 1] + (ca != cb)))
        precedente = courante
    return precedente[-1]


def _prenoms_et_nom(nom: str) -> tuple[str, str]:
    mots = nom.split()
    noms = [m for m in mots if m.isupper() and len(m) > 1]
    prenoms = [m for m in mots if m not in noms]
    return _sans_accents(" ".join(prenoms)).lower().replace(" ", ""), _sans_accents(" ".join(noms)).upper()


def meme_personne(a: str, b: str) -> bool:
    prenom_a, nom_a = _prenoms_et_nom(a)
    prenom_b, nom_b = _prenoms_et_nom(b)
    if not (prenom_a and prenom_b and nom_a and nom_b) or nom_a[0] != nom_b[0]:
        return False
    # Une lettre d'écart ; deux pour un nom long de même longueur (deux
    # lettres mal lues). « MARTIN » et « MARTINEZ » restent deux personnes.
    tolerance = 2 if min(len(nom_a), len(nom_b)) >= 9 and abs(len(nom_a) - len(nom_b)) <= 1 else 1
    return _distance(prenom_a, prenom_b) <= 1 and _distance(nom_a, nom_b) <= tolerance


def fusionner_personnes_proches(db: sqlite3.Connection) -> int:
    """Réunit les personnes qui sont la même écrite autrement. L'écriture
    retenue est celle qui revient le plus souvent dans les pages, à l'ordre
    Prénom NOM ; le rôle retenu est le plus lourd (mis en cause, victime,
    témoin). Renvoie le nombre de doublons fusionnés."""
    personnes = [dict(r) for r in db.execute("SELECT * FROM personnes ORDER BY id")]
    texte = "\n".join(r[0] for r in db.execute("SELECT texte FROM pages")).upper()
    groupes: list[list[dict]] = []
    for p in personnes:
        groupe = next((g for g in groupes if any(meme_personne(p["nom"], q["nom"]) for q in g)), None)
        if groupe is None:
            groupes.append([p])
        else:
            groupe.append(p)
    fusionnes = 0
    for groupe in groupes:
        if len(groupe) < 2:
            continue

        def frequence(personne: dict) -> int:
            _, nom = _prenoms_et_nom(personne["nom"])
            return len(re.findall(rf"(?<![A-Z]){re.escape(nom)}(?![A-Z])", _sans_accents(texte)))

        modele = max(groupe, key=frequence)
        mots = modele["nom"].split()
        noms = [m for m in mots if m.isupper() and len(m) > 1]
        nom_retenu = " ".join([m for m in mots if m not in noms] + noms)
        garde = min(groupe, key=lambda p: p["id"])
        role = max((p["role"] for p in groupe), key=lambda r: PRIORITE_ROLE_TAGUE.get(r, 0))
        client = max(int(p.get("est_client") or 0) for p in groupe)
        for autre in groupe:
            if autre["id"] == garde["id"]:
                continue
            for table, colonne in (("pieces", "personne_principale_id"), ("evenements_procedure", "personne_id"),
                                   ("declarations", "personne_id"), ("evenements_faits", "personne_id_source"),
                                   ("divergences", "personne_id")):
                db.execute(f"UPDATE {table} SET {colonne} = ? WHERE {colonne} = ?", (garde["id"], autre["id"]))
            db.execute("DELETE FROM personnes WHERE id = ?", (autre["id"],))
            fusionnes += 1
        db.execute("UPDATE personnes SET nom = ?, role = ? WHERE id = ?", (nom_retenu, role, garde["id"]))
        if client:
            db.execute("UPDATE personnes SET est_client = 1 WHERE id = ?", (garde["id"],))
    db.commit()
    return fusionnes


TITRES_ET_CIVILITES = TITRES_A_EXCLURE | HONORIFIQUES_ABREGES | {"dr", "pr", "professeur", "mademoiselle"}


def sans_titre(nom: str) -> str:
    """« Docteur Agathe MERCIER » -> « Agathe MERCIER » : le modèle recopie
    le titre avec le nom (observé), et la même personne, nommée sans titre
    ailleurs, apparaissait en double."""
    mots = nom.split()
    while len(mots) > 2 and mots[0].lower().rstrip(".") in TITRES_ET_CIVILITES:
        mots = mots[1:]
    return " ".join(mots)


def identifier_personne_principale(
    db: sqlite3.Connection,
    config: Config,
    type_piece: str,
    texte_complet: str,
    console: Console,
    compteur: dict[str, int],
) -> tuple[int | None, str]:
    """Point d'entrée unique pour déterminer à qui appartient une pièce,
    calculé une seule fois pendant la classification et réutilisé partout
    ensuite (chrono, déclarations) — pour ne jamais recalculer, au risque
    d'obtenir des réponses différentes selon l'étape, et pour ne jamais
    payer deux fois le même appel au modèle. Ordre de priorité : le tag de
    rôle explicite en en-tête (fiable), puis le premier nom mentionné pour
    les pièces où c'est sans ambiguïté, puis le modèle en dernier recours."""
    if type_piece in TYPES_HORS_PROCEDURE:
        return None, "hors_procedure"

    personne_id = identifier_declarant(db, texte_complet)
    if personne_id is not None:
        return personne_id, "tag_entete"

    # « Personne entendue : NOM Prénom » : l'identité est écrite, le rôle
    # vient de l'intitulé (ou de la pré-passe, qui a déjà enregistré la
    # personne). Sans rôle établi, on ne crée personne : le modèle tranche.
    nom = personne_du_champ(texte_complet)
    if nom:
        personne_id = _personne_par_mots(db, nom)
        if personne_id is None:
            role = role_par_intitule(_entete_etendu(texte_complet))
            if role:
                personne_id = _upsert_personne(db, nom, role)
        if personne_id is not None:
            return personne_id, "champ_identite"

    # Certificat médical : le premier nom est celui du médecin (« Je
    # soussignée, Docteur Anne-Laure CHABERT ») — observé, la médecin légiste
    # devenait mise en cause. On ne retient qu'une personne déjà connue.
    if type_piece == "Certificat médical":
        connus = []
        for row in db.execute("SELECT id, nom FROM personnes"):
            m = _motif_nom(row["nom"]).search(texte_complet)
            if m:
                connus.append((m.start(), row["id"]))
        if connus:
            return min(connus)[1], "premiere_mention"
    elif type_piece in TYPES_PERSONNE_PAR_PREMIERE_MENTION:
        personne_id = _premiere_mention_ou_creation(db, texte_complet, "mis_en_cause")
        if personne_id is not None:
            return personne_id, "premiere_mention"

    # Repli sur le modèle pour TOUT type de pièce, pas seulement les
    # auditions : une pièce dont la règle déterministe n'a rien tiré ne
    # renvoyait jusqu'ici aucune personne du tout, alors que le modèle sait
    # très souvent la lire. Les formulations d'identité varient bien plus
    # d'un service à l'autre qu'aucun jeu de motifs ne peut couvrir —
    # ajouter une regex par tournure rencontrée est sans fin ; le modèle
    # absorbe cette variété, et la vérification ci-dessus (chaque mot du nom
    # doit figurer dans la pièce) garde l'invention à distance.
    if not config.offline:
        personnes_connues = [(r["nom"], r["role"]) for r in db.execute("SELECT DISTINCT nom, role FROM personnes")]
        resultat = identifier_personne_via_llm(config, texte_complet, personnes_connues, console, compteur)
        if resultat is not None:
            nom, role = resultat
            personne_id = _upsert_personne(db, nom, role)
            return personne_id, "llm"

    return None, "non_identifie"


def lancer_classification(db: sqlite3.Connection, config: Config, force: bool, console: Console) -> None:
    debut = datetime.now(timezone.utc)

    if force:
        db.execute("DELETE FROM pieces")
        db.execute("DELETE FROM personnes")
        db.commit()
    elif db.execute("SELECT COUNT(*) FROM pieces").fetchone()[0] > 0:
        console.print("  [classify] des pièces existent déjà, ignoré (utilise --force pour retraiter).")
        return

    pages = db.execute("SELECT * FROM pages ORDER BY numero_global").fetchall()
    if not pages:
        console.print("  [classify] aucune page en base — lance d'abord `depouille ingest`.")
        return

    groupes = _detecter_pieces_par_page(pages)

    nb_deterministe = 0
    nb_llm = 0
    nb_non_identifie = 0
    compteur = {"tokens_in": 0, "tokens_out": 0}

    _enregistrer_personnes_taguees(db, ["\n".join(p["texte"] for p in groupe["pages"]) for groupe in groupes])

    for groupe in groupes:
        texte_complet = "\n".join(p["texte"] for p in groupe["pages"])
        entete = intitule_de_classement(groupe["pages"][0]["texte"])

        if _document_avocat(texte_complet):
            type_, confiance = TYPE_DOCUMENT_AVOCAT, 1.0
        else:
            type_, confiance = _classifier_type_deterministe(entete)
        if type_ == "Non identifié" and not config.offline:
            type_, confiance = _classifier_type_llm(config, texte_complet, console, compteur)
            nb_llm += 1
        elif type_ == "Non identifié":
            nb_non_identifie += 1
        else:
            nb_deterministe += 1

        statut_revision = "ok" if confiance >= config.seuil_confiance else "a_relire"
        if type_ == "Non identifié":
            statut_revision = "a_relire"

        date_apparente, heure_apparente = detecter_date_heure_acte(texte_complet)
        service = _detecter_service(texte_complet)
        # Première cote trouvée sur l'une des pages de la pièce, ramenée à sa
        # base (« D45/3 » -> « D45 ») : le tampon manque souvent sur la page
        # de garde, ou y est illisible, alors qu'il figure sur les suivantes.
        cote = cote_de_base(next((pg["cote_detectee"] for pg in groupe["pages"] if pg["cote_detectee"]), None))
        personnes_citees = _detecter_personnes_citees(texte_complet)

        for nom, role in _detecter_personnes_avec_role(texte_complet):
            _upsert_personne(db, nom, role)

        cur = db.execute(
            """INSERT INTO pieces
               (type, page_debut, page_fin, date_apparente, heure_apparente, service_redacteur,
                personnes_citees_json, cote, confiance, statut_revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                type_,
                groupe["page_debut"],
                groupe["page_fin"],
                date_apparente,
                heure_apparente,
                service,
                json.dumps(personnes_citees, ensure_ascii=False),
                cote,
                confiance,
                statut_revision,
            ),
        )

        personne_id, methode = identifier_personne_principale(db, config, type_, texte_complet, console, compteur)
        db.execute(
            "UPDATE pieces SET personne_principale_id = ?, methode_personne_principale = ? WHERE id = ?",
            (personne_id, methode, cur.lastrowid),
        )

    db.commit()
    fusionnes = fusionner_personnes_proches(db)
    if fusionnes:
        console.print(f"  [classify] {fusionnes} écriture(s) d'un même nom réunie(s) (lecture du scan, ordre NOM Prénom).")

    # Après l'identification des personnes : un intitulé d'audition porte
    # le nom de la personne entendue.
    titrer_pieces(db, config, console, compteur)

    fin = datetime.now(timezone.utc)
    cout = config.cout(config.modele_classification, compteur["tokens_in"], compteur["tokens_out"])
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin, tokens_in, tokens_out, cout_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("classify", "termine", debut.isoformat(), fin.isoformat(), compteur["tokens_in"], compteur["tokens_out"], cout),
    )
    db.commit()

    table = Table(title="Classification")
    table.add_column("Type")
    table.add_column("Pages")
    table.add_column("Confiance", justify="right")
    table.add_column("Statut")
    for row in db.execute("SELECT * FROM pieces ORDER BY page_debut"):
        table.add_row(
            row["type"],
            f"{row['page_debut']}-{row['page_fin']}",
            f"{row['confiance']:.2f}",
            row["statut_revision"],
        )
    console.print(table)
    console.print(
        f"  Classées par règle déterministe : {nb_deterministe} — "
        f"par appel modèle : {nb_llm} — Non identifié : {nb_non_identifie}"
    )
