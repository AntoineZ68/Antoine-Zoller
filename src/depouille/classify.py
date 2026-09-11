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
from .llm import ErreurModeOffline, extraire_json, obtenir_provider
from .regex_patterns import detecter_date_heure_acte

CATEGORIES = [
    "PV d'audition",
    "PV d'audition libre",
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
    "PV de synthèse",
    "Réquisition",
    "Réquisitoire introductif",
    "PV d'interrogatoire de première comparution",
    "Rapport d'expertise",
    "Analyse téléphonique",
    "Retranscription",
    "Soit-transmis",
    "Enquête de personnalité",
    "Casier judiciaire",
    "Pièce de procédure – autre",
    "Non identifié",
]

# Règles déterministes : (motifs devant TOUS apparaître dans l'en-tête, type).
# Évaluées dans l'ordre ; la première règle satisfaite l'emporte. Ni exhaustif
# ni figé : chaque dossier réel testé peut révéler une formulation nouvelle à
# ajouter — une pièce qui ne correspond à aucune règle part en "Non identifié"
# pour relecture humaine, elle n'est jamais devinée.
REGLES_MOTS_CLES: list[tuple[list[str], str]] = [
    (["NOTIFICATION", "PLACEMENT", "GARDE À VUE"], "PV de notification de placement en garde à vue"),
    (["NOTIFICATION", "MESURE", "GARDE À VUE"], "PV de notification de placement en garde à vue"),
    (["NOTIFICATION", "DROITS"], "PV de notification des droits"),
    (["PROLONGATION", "GARDE À VUE"], "PV de prolongation de garde à vue"),
    (["FIN DE GARDE À VUE"], "PV de fin de garde à vue"),
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
    (["RÉQUISITOIRE"], "Réquisitoire introductif"),
    (["RÉQUISITION"], "Réquisition"),
    (["INTERROGATOIRE", "COMPARUTION"], "PV d'interrogatoire de première comparution"),
    (["RAPPORT D'ANALYSE"], "Rapport d'expertise"),
    (["EXPERTISE"], "Rapport d'expertise"),
    (["ANALYSE TÉLÉPHONIQUE"], "Analyse téléphonique"),
    (["TRANSCRIPTION"], "Retranscription"),
    (["AUDITION LIBRE"], "PV d'audition libre"),
    (["AUDITION"], "PV d'audition"),
]

RE_TITRE = re.compile(r"^[A-ZÀ-Ÿ0-9°'’«»()/\-–—\s.,]{8,}$")
RE_SERVICE = re.compile(
    r"\b(brigade\s+de\s+gendarmerie\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+|"
    r"commissariat\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+|"
    r"Procureur\s+de\s+la\s+République\s+de\s+[A-ZÀ-Ÿ][\wà-ÿ'-]+)\b",
    re.IGNORECASE,
)
RE_PERSONNE = re.compile(r"\b([A-ZÀ-Ÿ][a-zà-ÿ]+)\s+([A-ZÀ-Ÿ]{2,}(?:-[A-ZÀ-Ÿ]{2,})?)\b")
RE_ROLE_TAG = re.compile(
    r"\b([A-ZÀ-Ÿ][A-Za-zà-ÿ]+)\s+([A-ZÀ-Ÿ]{2,})\s*\((MIS EN CAUSE|VICTIME|TÉMOIN)\)"
)
ROLE_PAR_TAG = {
    "MIS EN CAUSE": "mis_en_cause",
    "VICTIME": "victime",
    "TÉMOIN": "témoin",
}
ROLES_VALIDES = ("mis_en_cause", "victime", "témoin", "expert", "enqueteur")

TYPES_AUDITION = ("PV d'audition", "PV d'audition libre")

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


def _est_titre(ligne: str) -> bool:
    ligne = _sans_suffixe_metadonnee(ligne.strip()).strip()
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

    Certains PV placent une ligne de référence ("N° Procédure : ...",
    "Feuillet N° 1/2", "Date : ...") AVANT le titre plutôt qu'après — la
    casse mixte de ces lignes les exclut de la capture, mais elles ne
    doivent pas non plus arrêter la lecture avant d'atteindre le vrai
    titre. On les saute (nombre borné, jamais le corps du texte)."""
    lignes_entete = []
    lignes_metadonnees_sautees = 0
    for ligne in texte_page.splitlines():
        ligne_nettoyee = ligne.strip()
        if not ligne_nettoyee:
            continue
        if _est_titre(ligne_nettoyee):
            lignes_entete.append(ligne_nettoyee)
            continue
        if lignes_metadonnees_sautees < 6 and RE_METADONNEE_ENTETE.match(ligne_nettoyee):
            lignes_metadonnees_sautees += 1
            continue
        break
    return " ".join(lignes_entete)


NB_LIGNES_EXAMINEES_FRONTIERE = 3


def _detecter_pieces_par_page(pages: list[sqlite3.Row]) -> list[dict]:
    """Frontière déterministe : une page démarre une nouvelle pièce si l'une
    de ses toutes premières lignes non vides est un intitulé en capitales ;
    sinon elle prolonge la précédente.

    Certaines pages portent un tampon de transmission avant le vrai titre
    (ex. "FAX FROM: COMMISSARIAT VENISSIEUX -- TO: PJ LYON STUPS -- ... --
    PAGE 1/2") : cette ligne contient des ":" qui l'excluent d'office du
    test de titre (jamais dans un intitulé légitime), donc regarder
    uniquement la toute première ligne ratait le titre juste en dessous —
    fusionnant deux pièces distinctes en une seule."""
    pieces: list[dict] = []
    for page in pages:
        premieres_lignes = [l for l in page["texte"].splitlines() if l.strip()][:NB_LIGNES_EXAMINEES_FRONTIERE]
        nouvelle_piece = not pieces or any(_est_titre(l) for l in premieres_lignes)
        if nouvelle_piece:
            pieces.append({"page_debut": page["numero_global"], "page_fin": page["numero_global"], "pages": [page]})
        else:
            pieces[-1]["page_fin"] = page["numero_global"]
            pieces[-1]["pages"].append(page)
    return pieces


def _sans_accents(texte: str) -> str:
    """Les majuscules françaises omettent souvent les accents en pratique
    ("GARDE A VUE", "REQUISITOIRE") — les règles de classification doivent
    reconnaître les deux écritures plutôt que de dépendre d'une convention
    typographique qu'aucun dossier réel ne respecte de façon uniforme."""
    forme = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in forme if not unicodedata.combining(c))


def _classifier_type_deterministe(texte_entete: str) -> tuple[str, float]:
    majuscules = _sans_accents(texte_entete.upper())
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
        resultats.append((f"{prenom.capitalize()} {nom.upper()}", ROLE_PAR_TAG[tag]))
    return resultats


def identifier_declarant(db: sqlite3.Connection, page_entete: str) -> int | None:
    """Identifie la personne d'une pièce à partir du rôle explicitement tagué
    dans son en-tête ("(MIS EN CAUSE)", "(VICTIME)", "(TÉMOIN)") — jamais en
    cherchant le premier nom mentionné n'importe où dans le texte, qui
    attraperait aussi bien un tiers cité en passant (ex. dans une question)."""
    roles = _detecter_personnes_avec_role(page_entete)
    if not roles:
        return None
    nom, role = roles[0]
    row = db.execute("SELECT id FROM personnes WHERE nom = ? AND role = ?", (nom, role)).fetchone()
    return row["id"] if row else None


def _upsert_personne(db: sqlite3.Connection, nom: str, role: str) -> int:
    row = db.execute("SELECT id FROM personnes WHERE nom = ? AND role = ?", (nom, role)).fetchone()
    if row:
        return row["id"]
    cur = db.execute("INSERT INTO personnes (nom, role) VALUES (?, ?)", (nom, role))
    return cur.lastrowid


TITRES_A_EXCLURE = {
    "capitaine", "commandant", "lieutenant", "colonel", "major", "brigadier",
    "adjudant", "gardien", "maréchal", "docteur", "maître", "monsieur", "madame",
}


def _premiere_mention_hors_titres(texte: str) -> tuple[str, str] | None:
    """Le premier nom "Prénom NOM" mentionné n'est pas forcément le mis en
    cause : les PV nomment très souvent l'officier rédacteur ("nous,
    capitaine Élodie BASTIER...") avant de nommer la personne concernée. On
    exclut donc les mentions immédiatement précédées d'un titre ou d'un
    grade — la personne concernée par la pièce est cherchée après ça."""
    for m in RE_PERSONNE.finditer(texte):
        avant = texte[: m.start()].rstrip().split()
        dernier_mot = avant[-1].lower().rstrip(",.") if avant else ""
        if dernier_mot in TITRES_A_EXCLURE:
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
    explicitement."""
    trouve = _premiere_mention_hors_titres(texte)
    if not trouve:
        return None
    prenom, nom_famille = trouve
    nom = f"{prenom} {nom_famille.upper()}"
    row = db.execute("SELECT id FROM personnes WHERE nom = ?", (nom,)).fetchone()
    if row:
        return row["id"]
    return _upsert_personne(db, nom, role_par_defaut)


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
    vérifiée, pas une invention), soit être retrouvé littéralement dans le
    texte de cette pièce précise. Un nom qui ne remplit aucune des deux
    conditions est rejeté, quelle que soit la confiance apparente du
    modèle."""
    try:
        provider = obtenir_provider(config)
        liste_connues = "\n".join(f"- {nom} ({role})" for nom, role in personnes_connues) or "(aucune)"
        reponse = provider.appeler(
            systeme=(
                "Tu identifies la personne interrogée ou concernée par cette pièce de "
                "procédure pénale française, et son rôle. Rôles possibles, à l'exclusion "
                "de tout autre : " + ", ".join(ROLES_VALIDES) + ".\n"
                "Personnes déjà identifiées ailleurs dans ce dossier :\n"
                f"{liste_connues}\n"
                "Si cette pièce concerne l'une d'entre elles — même si le texte ne la "
                'nomme pas explicitement (ex. "le gardé à vue", "l\'intéressé") — réponds '
                "avec son nom EXACTEMENT comme il apparaît dans cette liste. Sinon, si le "
                "texte nomme explicitement quelqu'un d'autre, recopie ce nom EXACTEMENT "
                'comme il apparaît dans le texte. Réponds uniquement en JSON : '
                '{"nom": "...", "role": "..."}. Si tu ne peux déterminer la personne avec '
                'certitude ni par la liste ni par le texte, réponds {"nom": null, "role": null}.'
            ),
            prompt=texte_piece[:4000],
            modele=config.modele_classification,
        )
        compteur["tokens_in"] += reponse.tokens_in
        compteur["tokens_out"] += reponse.tokens_out
        data = extraire_json(reponse.texte)
        nom, role = data.get("nom"), data.get("role")
        if not nom or role not in ROLES_VALIDES:
            return None

        noms_connus = {n.lower(): n for n, _ in personnes_connues}
        if nom.lower() in noms_connus:
            return noms_connus[nom.lower()], role
        if nom.lower() in texte_piece.lower():
            return nom, role

        console.print(
            f"  [classify] identification par le modèle rejetée : {nom!r} ni retrouvé "
            "littéralement dans la pièce, ni parmi les personnes déjà identifiées."
        )
        return None
    except ErreurModeOffline:
        raise
    except Exception as exc:  # noqa: BLE001 — dégrade en None, jamais une exception qui casse le run
        console.print(f"  [classify] échec de l'identification de personne par le modèle ({exc}).")
        return None


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
    personne_id = identifier_declarant(db, texte_complet)
    if personne_id is not None:
        return personne_id, "tag_entete"

    if type_piece in TYPES_PERSONNE_PAR_PREMIERE_MENTION:
        personne_id = _premiere_mention_ou_creation(db, texte_complet, "mis_en_cause")
        if personne_id is not None:
            return personne_id, "premiere_mention"

    if type_piece in TYPES_AUDITION and not config.offline:
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

    for groupe in groupes:
        texte_complet = "\n".join(p["texte"] for p in groupe["pages"])
        entete = _entete_etendu(groupe["pages"][0]["texte"])

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
        cote = groupe["pages"][0]["cote_detectee"]
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
