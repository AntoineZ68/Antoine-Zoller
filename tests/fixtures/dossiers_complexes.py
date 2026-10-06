"""Dossiers pénaux complexes — entièrement fictifs — pour le banc d'essai IA.

Le dossier de contrôle (generate_controle.py) tient en 17 pages et une
seule personne en garde à vue. Un vrai dossier, c'est plusieurs mis en
cause, des auditions de plusieurs pages qui varient, des magistrats, des
avocats et des médecins nommés partout, des pages numérisées, des
réquisitions et des écoutes. Ces deux dossiers de plus de 40 pages
reproduisent cette complexité, avec des pièges dont on connaît d'avance la
réponse (VeriteDossier) :

1. « Opération Ressac » — trafic de stupéfiants, trois mis en cause, garde
   à vue de 72 heures en régime dérogatoire (deux prolongations, dont une
   par le juge des libertés), écoutes, saisie dont le poids diffère de
   l'expertise, numéro de téléphone transposé d'une pièce à l'autre.
2. « Rue Pasteur » — vol avec violences, deux coauteurs qui se renvoient
   les coups, témoins contradictoires (couleur du vêtement, heure), ITT
   différente entre le certificat et la synthèse, plaque transposée, un
   témoin au prénom accentué dans l'en-tête de son audition.

Tout est inventé : noms, lieux, dates (2032-2033), numéros de procédure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from . import generate_fixture as base

LARGEUR_PAGE, HAUTEUR_PAGE = A4
LIGNES_PAR_PAGE = 34  # lignes de 95 caractères, intertitres compris


@dataclass
class QuestionAttendue:
    texte: str
    # Heures que la réponse (ou, réduite aux passages, ses citations) doit
    # toutes donner : une seule cacherait une discordance du dossier.
    heures: tuple[tuple[int, int], ...] = ()
    # Mots qui doivent tous figurer dans la réponse ou ses citations.
    mots: tuple[str, ...] = ()


@dataclass
class VeriteDossier:
    chemin_pdf: Path
    nb_pages: int
    pages_scan: list[int]
    # Chaque discordance attendue des règles fixes : morceaux qui doivent
    # tous figurer dans le titre d'une même contradiction.
    contradictions_par_regles: tuple[tuple[str, ...], ...] = ()
    # Discordances que seul le modèle peut relever : au moins deux de ces
    # mots dans le titre ou la description d'une contradiction du modèle.
    contradictions_modele: tuple[tuple[str, ...], ...] = ()
    durees_gav: dict[str, int] = field(default_factory=dict)  # nom -> minutes
    mis_en_cause: tuple[str, ...] = ()
    jamais_mis_en_cause: tuple[str, ...] = ()
    questions: tuple[QuestionAttendue, ...] = ()


# --- Rendu ---------------------------------------------------------------------


def _hauteur(ligne: str) -> float:
    texte = ligne[2:] if ligne.startswith("##") else ligne
    return math.ceil(max(len(texte), 1) / 95) + 0.4


def paginer(lignes: list[str]) -> list[list[str]]:
    """Répartit les paragraphes d'une pièce sur autant de pages qu'il faut."""
    pages, courante, hauteur = [], [], 0.0
    for ligne in lignes:
        h = _hauteur(ligne)
        if courante and hauteur + h > LIGNES_PAR_PAGE:
            pages.append(courante)
            courante, hauteur = [], 0.0
        courante.append(ligne)
        hauteur += h
    if courante:
        pages.append(courante)
    return pages


def _page_texte(lignes: list[str], pied: str, cote: str) -> bytes:
    tampon = BytesIO()
    c = pdfcanvas.Canvas(tampon, pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(LARGEUR_PAGE - 30 * mm, HAUTEUR_PAGE - 12 * mm, cote)
    y = HAUTEUR_PAGE - 25 * mm
    for ligne in lignes:
        gras = ligne.startswith("##")
        texte = ligne[2:].strip() if gras else ligne
        c.setFont("Helvetica-Bold" if gras else "Helvetica", 11 if gras else 10)
        for sous_ligne in base._decouper_ligne(texte, 95):
            c.drawString(20 * mm, y, sous_ligne)
            y -= 6 * mm
        y -= 2 * mm
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 12 * mm, pied)
    c.showPage()
    c.save()
    return tampon.getvalue()


def _page_scan(lignes: list[str], pied: str, cote: str) -> bytes:
    largeur_px, hauteur_px = 1654, 2339
    img = Image.new("RGB", (largeur_px, hauteur_px), "white")
    dessin = ImageDraw.Draw(img)
    dessin.text((largeur_px - 260, 60), cote, fill="black", font=base._police(24))
    y = 150
    for ligne in lignes:
        gras = ligne.startswith("##")
        texte = ligne[2:].strip() if gras else ligne
        police = base._police(30 if gras else 26)
        for sous_ligne in base._decouper_ligne(texte, 80):
            dessin.text((110, y), sous_ligne, fill="black", font=police)
            y += 40
        y += 12
    dessin.text((110, hauteur_px - 100), pied, fill="black", font=base._police(20))
    return base._page_image_vers_pdf(img)


def ecrire_dossier(pieces: list[dict], chemin: Path, parquet: str) -> tuple[int, list[int]]:
    """pieces : [{"scan": bool, "lignes": [...]}] — paginées ici. Renvoie
    (nombre de pages, pages numérisées)."""
    ecrivain = PdfWriter()
    numero, scans = 0, []
    for piece in pieces:
        for lignes in paginer(piece["lignes"]):
            numero += 1
            cote = f"D{numero}"
            pied = f"N° PARQUET {parquet} — Cote D{numero:04d} — Page {numero}"
            if piece.get("scan"):
                scans.append(numero)
                octets = _page_scan(lignes, pied, cote)
            else:
                octets = _page_texte(lignes, pied, cote)
            ecrivain.add_page(PdfReader(BytesIO(octets)).pages[0])
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("wb") as f:
        ecrivain.write(f)
    return numero, scans


def piece(titre: str, *paragraphes: str, scan: bool = False) -> dict:
    return {"scan": scan, "lignes": [f"## {titre}", *paragraphes]}


def audition(
    nom: str, role: str, date: str, debut: str, fin: str, enqueteur: str,
    questions_reponses: list[tuple[str, str]], libre: bool = False, scan: bool = False,
) -> dict:
    titre = f"PROCÈS-VERBAL D'AUDITION{' LIBRE' if libre else ''} DE {nom.upper()} ({role})"
    lignes = [
        f"Le {date}, audition débutée à {debut}, close à {fin}.",
        f"Par devant nous, {enqueteur}, comparaît la personne ci-dessus désignée, qui déclare :",
    ]
    for q, r in questions_reponses:
        lignes += [f"Question : {q}", f"Réponse : {r}"]
    lignes.append("Lecture faite par elle-même, la personne entendue persiste et signe avec nous.")
    return piece(titre, *lignes, scan=scan)


def minutes_entre(debut: str, fin: str) -> int:
    f = "%d/%m/%Y %Hh%M"
    return int((datetime.strptime(fin, f) - datetime.strptime(debut, f)).total_seconds() // 60)


# --- Dossier 1 : Opération Ressac (stupéfiants) ---------------------------------

PARQUET_RESSAC = "2032/01177"
OPJ_RESSAC = "commandant Laurent VIGIER, officier de police judiciaire à la brigade de recherches de Port-Bréval"
APJ_RESSAC = "lieutenant Sarah KHELIF, officier de police judiciaire"


def _pieces_ressac() -> list[dict]:
    return [
        piece(
            "PROCÈS-VERBAL DE SYNTHÈSE",
            "Brigade de recherches de Port-Bréval. Le 26/09/2032, le commandant Laurent VIGIER, "
            "officier de police judiciaire, dresse la synthèse de l'enquête préliminaire ouverte "
            "le 02/09/2032 sur instruction de Mme Claire DESMARETS, procureure de la République.",
            "Un renseignement anonyme faisait état d'un trafic de résine de cannabis animé depuis "
            "un box de l'allée des Mouettes à Port-Bréval. Les surveillances et les interceptions "
            "téléphoniques autorisées ont permis d'identifier Karim TALBI, Yanis BOUCHARD et "
            "Nadia OGIER.",
            "Le 23/09/2032, Karim TALBI et Yanis BOUCHARD ont été interpellés et placés en garde à "
            "vue. Nadia OGIER a été entendue sous le régime de l'audition libre le 24/09/2032.",
            "La perquisition du domicile de Karim TALBI a permis la saisie de résine de cannabis "
            "d'un poids net de 1 200 grammes, de 4 350 euros en numéraire et de deux téléphones.",
            "Karim TALBI conteste toute participation à un trafic. Yanis BOUCHARD a d'abord nié "
            "connaître Karim TALBI avant de reconnaître le connaître depuis le lycée.",
            "À l'issue de sa garde à vue, Karim TALBI a été déféré devant le parquet le 26/09/2032. "
            "Une information judiciaire est requise.",
        ),
        piece(
            "SOIT-TRANSMIS",
            "Le 02/09/2032, Mme Claire DESMARETS, procureure de la République de Port-Bréval, "
            "transmet à la brigade de recherches de Port-Bréval le renseignement anonyme reçu le "
            "01/09/2032 et prescrit l'ouverture d'une enquête préliminaire du chef de trafic de "
            "stupéfiants.",
            "Elle demande à être avisée de toute mesure de garde à vue et de toute difficulté.",
        ),
        piece(
            "PROCÈS-VERBAL DE RENSEIGNEMENT",
            "Le 01/09/2032 à 18h20, un appel anonyme est reçu au standard de la brigade. "
            "L'interlocuteur indique que des allées et venues ont lieu chaque soir autour du box "
            "numéro 7 de l'allée des Mouettes, et qu'un homme surnommé « Kim » y entreposerait de "
            "la résine de cannabis.",
            "L'interlocuteur précise que les transactions ont lieu le plus souvent entre 21 heures "
            "et minuit, et qu'un fourgon vient régulièrement charger des sacs de sport.",
        ),
        piece(
            "PROCÈS-VERBAL DE SURVEILLANCE",
            "Le 12/09/2032 de 20h30 à 23h50, nous, lieutenant Sarah KHELIF, assistée du brigadier "
            "Thomas RENOU, procédons à une surveillance discrète de l'allée des Mouettes à "
            "Port-Bréval, depuis un véhicule banalisé stationné face au box numéro 7.",
            "À 21h15, un homme correspondant au signalement de Karim TALBI ouvre le box numéro 7 et "
            "y demeure une dizaine de minutes.",
            "À 22h05, un scooter de couleur noire s'arrête devant le box. Son conducteur remet un "
            "sac plastique à l'homme et repart aussitôt en direction du centre-ville.",
            "À 23h40, l'homme referme le box et rejoint à pied le 12 allée des Mouettes.",
        ),
        piece(
            "PROCÈS-VERBAL DE SURVEILLANCE",
            "Le 20/09/2032 de 22h00 à 23h55, nous, brigadier Thomas RENOU, procédons à une nouvelle "
            "surveillance de l'allée des Mouettes à Port-Bréval.",
            "À 23h05, un fourgon de couleur grise se gare devant le box numéro 7. Deux individus en "
            "descendent et chargent quatre sacs de sport dans le fourgon.",
            "Le fourgon repart à 23h20 en direction de l'autoroute. Son immatriculation n'a pas pu "
            "être relevée en raison de l'obscurité.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE SURVEILLANCE",
            "Le 16/09/2032 de 21h00 à 23h30, nous, lieutenant Sarah KHELIF, procédons à une "
            "surveillance de l'allée des Mouettes et des abords du box numéro 7.",
            "À 21h35, Yanis BOUCHARD, identifié grâce à la photographie de son permis de conduire, "
            "arrive à pied et attend devant le box en consultant son téléphone.",
            "À 21h50, l'homme surveillé le 12/09/2032 ouvre le box ; les deux hommes y entrent et en "
            "ressortent à 22h10, Yanis BOUCHARD portant un sac de sport noir.",
            "Aucune interpellation n'est réalisée afin de ne pas compromettre l'enquête.",
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À OPÉRATEUR DE TÉLÉPHONIE",
            "Le 05/09/2032, nous, commandant Laurent VIGIER, requérons la société Nérée Mobile de nous "
            "communiquer l'identité du titulaire de la ligne 06 71 42 88 19 ainsi que la liste des "
            "appels entrants et sortants du 01/08/2032 au 05/09/2032.",
            "Réponse de l'opérateur le 08/09/2032 : ligne prépayée activée le 14/06/2032 au nom "
            "de Kim TALBA, adresse déclarée 12 allée des Mouettes à Port-Bréval.",
            scan=True,
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À OPÉRATEUR DE TÉLÉPHONIE",
            "Le 09/09/2032, nous, commandant Laurent VIGIER, requérons la société Nérée Mobile de nous "
            "communiquer l'identité du titulaire de la ligne 07 58 23 64 10, en contact fréquent avec "
            "la ligne attribuée à Karim TALBI.",
            "Réponse de l'opérateur le 11/09/2032 : abonnement au nom de Yanis BOUCHARD, né le "
            "17/02/2001, demeurant 5 rue du Môle à Port-Bréval.",
        ),
        piece(
            "ORDONNANCE D'AUTORISATION D'INTERCEPTION — RÉQUISITION",
            "Le 10/09/2032, M. Hervé LANGLOIS, juge des libertés et de la détention au tribunal "
            "judiciaire de Port-Bréval, saisi par requête de la procureure de la République, "
            "autorise pour une durée d'un mois l'interception des communications émises et reçues "
            "par les lignes 07 58 23 64 10 et 06 71 42 88 19.",
        ),
        piece(
            "PROCÈS-VERBAL DE TRANSCRIPTION D'INTERCEPTIONS",
            "Le 18/09/2032, nous, lieutenant Sarah KHELIF, procédons à la transcription des "
            "communications utiles interceptées sur la ligne 07 58 23 64 10.",
            "Communication du 15/09/2032 à 20h47, appel sortant vers le 06 71 42 88 91 :",
            "Voix attribuée à Yanis BOUCHARD : « C'est bon pour ce soir ? Il me faut les quatre. »",
            "Voix attribuée à Karim TALBI : « Pas au téléphone. Viens au box après dix heures. »",
            "Communication du 16/09/2032 à 09h12, appel entrant du 06 71 42 88 91 :",
            "Voix attribuée à Karim TALBI : « Le gris passe vendredi soir, sois là pour charger. »",
            "Voix attribuée à Yanis BOUCHARD : « Vendredi c'est compliqué, je bosse jusqu'à "
            "vingt-deux heures. »",
            "Communication du 17/09/2032 à 22h31, appel sortant vers une ligne non identifiée :",
            "Voix attribuée à Yanis BOUCHARD : « Il a changé d'avis, ce sera samedi. Préviens Nadia "
            "pour l'argent. »",
        ),
        piece(
            "PROCÈS-VERBAL DE TRANSCRIPTION D'INTERCEPTIONS",
            "Le 21/09/2032, nous, lieutenant Sarah KHELIF, procédons à la transcription des "
            "communications utiles interceptées du 19/09/2032 au 21/09/2032.",
            "Communication du 19/09/2032 à 18h03, appel entrant sur la ligne 07 58 23 64 10 depuis "
            "le 06 12 90 37 55 :",
            "Voix attribuée à Nadia OGIER : « J'ai déposé l'enveloppe chez ta mère comme prévu. »",
            "Voix attribuée à Yanis BOUCHARD : « Combien ? » — « Comme d'habitude, trois mille. »",
            "Communication du 20/09/2032 à 21h50, appel sortant vers le 06 71 42 88 91 :",
            "Voix attribuée à Yanis BOUCHARD : « On charge à onze heures, viens avec ton cousin. »",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE TRANSCRIPTION D'INTERCEPTIONS",
            "Le 22/09/2032, nous, brigadier Thomas RENOU, procédons à la transcription des "
            "communications utiles interceptées le 22/09/2032.",
            "Communication du 22/09/2032 à 19h22, appel entrant sur la ligne 07 58 23 64 10 depuis "
            "le 06 71 42 88 91 :",
            "Voix attribuée à Karim TALBI : « Il y a des gens bizarres dans une voiture en bas depuis "
            "deux jours. On arrête tout jusqu'à nouvel ordre. »",
            "Voix attribuée à Yanis BOUCHARD : « Et ce qui reste dans le box ? » — « Je m'en occupe "
            "demain matin. »",
        ),
        piece(
            "PROCÈS-VERBAL D'ANALYSE TÉLÉPHONIQUE",
            "Le 22/09/2032, nous, lieutenant Sarah KHELIF, procédons à l'analyse des fadettes "
            "communiquées par la société Nérée Mobile.",
            "Entre le 01/08/2032 et le 05/09/2032, la ligne attribuée à Karim TALBI a échangé 214 "
            "communications avec la ligne de Yanis BOUCHARD et 37 avec la ligne 06 12 90 37 55, "
            "dont le titulaire n'est pas identifié.",
            "Les communications se concentrent entre 20h00 et 23h30, ce qui correspond aux "
            "horaires des allées et venues constatées autour du box numéro 7.",
            "Le bornage de la ligne de Karim TALBI la situe le 12/09/2032 à 21h14 sur le relais "
            "couvrant l'allée des Mouettes.",
        ),
        audition(
            "Odette MARCHAL", "TÉMOIN", "22/09/2032", "10h00", "10h40", APJ_RESSAC,
            [
                ("Pouvez-vous décliner votre situation ?",
                 "Je suis retraitée, j'habite au 10 allée des Mouettes depuis vingt ans, juste en face "
                 "des boxes."),
                ("Avez-vous remarqué des faits inhabituels ces dernières semaines ?",
                 "Oui, beaucoup de passage le soir autour du box numéro 7, des scooters, des voitures "
                 "qui restent moteur allumé."),
                ("Que s'est-il passé le soir du 20/09/2032 ?",
                 "Vers 23 heures, un fourgon blanc s'est arrêté devant le box. Trois hommes en sont "
                 "descendus et ont chargé des sacs de sport."),
                ("Pouvez-vous décrire ces hommes ?",
                 "Il faisait sombre. L'un était grand avec une casquette, c'est celui que je vois "
                 "souvent ouvrir le box. Les deux autres étaient plus jeunes."),
                ("Connaissez-vous le locataire du box numéro 7 ?",
                 "Je sais que c'est le fils TALBI, du numéro 12. On l'appelle Kim dans le quartier."),
                ("Avez-vous déjà été menacée ou sollicitée ?",
                 "Non, jamais. Mais je ne sors plus le soir."),
            ],
        ),
        audition(
            "Fatima TALBI", "TÉMOIN", "22/09/2032", "15h00", "15h45", APJ_RESSAC,
            [
                ("Vous êtes la mère de Karim TALBI. Le box numéro 7 est-il loué à votre nom ?",
                 "Oui, depuis la mort de mon mari en 2020. Il y avait sa voiture dedans, je l'ai vendue."),
                ("Qui utilise ce box aujourd'hui ?",
                 "Karim a la clé. Je n'y descends jamais, mes jambes ne me le permettent plus."),
                ("Avez-vous reçu une enveloppe le 19/09/2032 ?",
                 "Une jeune femme a déposé une enveloppe pour Karim. Je l'ai posée sur son lit sans "
                 "l'ouvrir."),
                ("Quelles sont les ressources de votre fils ?",
                 "Il travaille en intérim. Il m'aide pour les courses et le loyer."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 23/09/2032 à 06h10, nous, commandant Laurent VIGIER, officier de police judiciaire, "
            "notifions à Karim TALBI, né le 04/11/1994 à Port-Bréval, demeurant 12 allée des Mouettes "
            "à Port-Bréval, son placement en garde à vue pour des faits de trafic de stupéfiants "
            "commis entre le 01/08/2032 et le 23/09/2032.",
            "Interpellation effectuée le 23/09/2032 à 06h00, 12 allée des Mouettes à Port-Bréval.",
            "La procureure de la République est avisée de la mesure le 23/09/2032 à 06h25.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
            "Le 23/09/2032 à 06h15, nous notifions à Karim TALBI ses droits attachés à la mesure de "
            "garde à vue : droit de faire prévenir un proche et son employeur, droit d'être examiné "
            "par un médecin, droit d'être assisté par un avocat, droit de se taire.",
            "Karim TALBI demande l'assistance de Maître Paul ESTRADE, avocat au barreau de "
            "Port-Bréval, et un examen médical. Il demande que sa mère soit prévenue.",
        ),
        piece(
            "PROCÈS-VERBAL DE PERQUISITION ET DE SAISIE",
            "Le 23/09/2032 de 06h40 à 08h15, perquisition au domicile de Karim TALBI, 12 allée des "
            "Mouettes à Port-Bréval, en présence de l'intéressé, puis dans le box numéro 7 dont il "
            "remet la clé.",
            "Dans la chambre : une somme de 4 350 euros en billets de 50 et de 20 euros dissimulée "
            "dans une boîte à chaussures, deux téléphones portables de marque Corvex.",
            "Dans le box numéro 7 : douze plaquettes de résine de cannabis conditionnées sous film "
            "plastique, d'un poids net de 1 200 grammes après pesée sur la balance du service, une "
            "balance de précision et un rouleau de film étirable.",
            "Karim TALBI déclare que l'argent provient de ses économies et que le box est utilisé "
            "par plusieurs personnes du quartier.",
            "Les objets saisis sont placés sous scellés numérotés de 1 à 6.",
        ),
        piece(
            "PROCÈS-VERBAL DE PLACEMENT SOUS SCELLÉS",
            "Le 23/09/2032, nous, commandant Laurent VIGIER, plaçons sous scellés les objets saisis "
            "lors de la perquisition du domicile de Karim TALBI et du box numéro 7.",
            "Scellé numéro 1 : la somme de 4 350 euros. Scellé numéro 2 : deux téléphones portables "
            "de marque Corvex. Scellé numéro 3 : douze plaquettes de résine de cannabis. Scellé "
            "numéro 4 : une balance de précision. Scellé numéro 5 : un rouleau de film étirable. "
            "Scellé numéro 6 : une clé du box numéro 7.",
            "Les scellés numéros 1 à 6 sont déposés au service des pièces à conviction.",
        ),
        piece(
            "CERTIFICAT MÉDICAL",
            "Je soussigné, Docteur Marc ROUSSEL, médecin requis, certifie avoir examiné ce jour "
            "23/09/2032 à 08h50, sur réquisition du 23/09/2032 reçue à 06h45, Karim TALBI dans le "
            "cadre de sa garde à vue.",
            "L'intéressé ne signale aucun traitement en cours. Aucune lésion traumatique récente "
            "n'est constatée.",
            "Son état de santé est compatible avec la mesure de garde à vue.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL D'ENTRETIEN AVEC L'AVOCAT",
            "Karim TALBI, placé en garde à vue, a demandé à s'entretenir avec son avocat.",
            "Demande d'entretien formulée le 23/09/2032 à 06h12.",
            "Entretien avec Maître Paul ESTRADE, avocat choisi, réalisé le 23/09/2032 de 08h55 à 09h25.",
        ),
        audition(
            "Karim TALBI", "MIS EN CAUSE", "23/09/2032", "09h40", "11h55", OPJ_RESSAC,
            [
                ("Vous êtes assisté de Maître Paul ESTRADE. Souhaitez-vous faire des déclarations ?",
                 "Oui, je vais répondre."),
                ("Quelle est votre situation professionnelle ?",
                 "Je suis cariste en intérim chez Portalis Logistique depuis deux ans, je gagne à peu "
                 "près 1 600 euros par mois."),
                ("Êtes-vous locataire du box numéro 7 de l'allée des Mouettes ?",
                 "Il est au nom de ma mère, mais tout le monde a la clé dans le quartier. Je n'y vais "
                 "presque jamais."),
                ("Des plaquettes de résine de cannabis y ont été découvertes. Qu'avez-vous à dire ?",
                 "Elles ne sont pas à moi. N'importe qui peut entrer dans ce box."),
                ("Vous avez été vu ouvrir ce box le 12/09/2032 à 21h15. Que faisiez-vous ?",
                 "Je cherchais un pneu pour la voiture de ma mère. Je n'ai rien fait d'autre."),
                ("D'où proviennent les 4 350 euros retrouvés dans votre chambre ?",
                 "Ce sont mes économies. Je ne fais pas confiance aux banques."),
                ("Utilisez-vous la ligne 06 71 42 88 19 ?",
                 "Je ne connais pas ce numéro. J'ai un seul téléphone, avec un abonnement."),
                ("Connaissez-vous Yanis BOUCHARD ?",
                 "C'est un gars du quartier, je le croise. On n'est pas spécialement amis."),
                ("Que faisiez-vous le soir du 20/09/2032 vers 23 heures ?",
                 "J'étais chez moi, devant un match de football avec ma mère."),
                ("Consommez-vous des stupéfiants ?",
                 "Je fume un peu le week-end, c'est tout."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE PROLONGATION DE GARDE À VUE",
            "Le 24/09/2032 à 05h50, Mme Claire DESMARETS, procureure de la République de Port-Bréval, "
            "après présentation, autorise la prolongation de la garde à vue de Karim TALBI pour une "
            "durée de vingt-quatre heures.",
        ),
        audition(
            "Karim TALBI", "MIS EN CAUSE", "24/09/2032", "10h30", "11h10", OPJ_RESSAC,
            [
                ("Vous êtes assisté de Maître Paul ESTRADE. Des interceptions vous attribuent des "
                 "propos sur un chargement le vendredi soir. Qu'en dites-vous ?",
                 "Sur les conseils de mon avocat, je ne souhaite pas répondre."),
                ("Une voisine affirme vous voir régulièrement ouvrir le box numéro 7. Qu'en dites-vous ?",
                 "Je ne souhaite pas répondre."),
                ("Reconnaissez-vous la voix qui vous est attribuée dans les écoutes ?",
                 "Je ne souhaite pas répondre."),
            ],
        ),
        piece(
            "ORDONNANCE DE PROLONGATION DE GARDE À VUE",
            "Le 25/09/2032 à 05h45, M. Hervé LANGLOIS, juge des libertés et de la détention, saisi "
            "par la procureure de la République, autorise la prolongation de la garde à vue de Karim "
            "TALBI pour une durée de quarante-huit heures, en application de l'article 706-88 du "
            "code de procédure pénale.",
            "L'intéressé a été présenté au magistrat, qui a entendu ses observations.",
        ),
        audition(
            "Karim TALBI", "MIS EN CAUSE", "25/09/2032", "14h00", "15h20", OPJ_RESSAC,
            [
                ("Vous avez souhaité être réentendu. Pourquoi ?",
                 "Je veux dire la vérité sur une partie. La résine dans le box, une partie est à moi, "
                 "pour ma consommation. Le reste, je ne sais pas."),
                ("Quelle quantité est à vous ?",
                 "Deux ou trois plaquettes, pas plus. Je ne vends rien."),
                ("Et l'argent ?",
                 "C'est de l'argent que je gardais pour quelqu'un, je ne veux pas dire qui."),
                ("Connaissez-vous Nadia OGIER ?",
                 "C'est la copine de Yanis. Je la connais de vue."),
                ("Le fourgon du 20/09/2032, était-ce pour vous ?",
                 "Je n'étais pas là ce soir-là, je l'ai déjà dit."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE CONFRONTATION",
            "Le 25/09/2032 de 16h00 à 17h15, nous, commandant Laurent VIGIER, procédons à la "
            "confrontation de Karim TALBI, assisté de Maître Paul ESTRADE, et de Yanis BOUCHARD, "
            "assisté de Maître Inès VALLON.",
            "Question à Yanis BOUCHARD : Maintenez-vous que Karim TALBI organisait les chargements ?",
            "Réponse de Yanis BOUCHARD : Oui. C'est lui qui avait la clé et qui fixait les heures.",
            "Question à Karim TALBI : Qu'avez-vous à répondre ?",
            "Réponse de Karim TALBI : Il ment pour se protéger. Je n'organise rien du tout.",
            "Question à Yanis BOUCHARD : Qui est le cousin dont vous parlez ?",
            "Réponse de Yanis BOUCHARD : Je ne connais que son surnom, Rico. Il conduisait le fourgon.",
            "Question à Karim TALBI : Connaissez-vous un dénommé Rico ?",
            "Réponse de Karim TALBI : Non.",
            "Chacun maintient ses déclarations. Lecture faite, les intéressés signent avec nous.",
        ),
        piece(
            "PROCÈS-VERBAL D'EXPLOITATION DES TÉLÉPHONES SAISIS",
            "Le 25/09/2032, nous, lieutenant Sarah KHELIF, procédons à l'exploitation des téléphones "
            "placés sous scellé numéro 2.",
            "Le premier téléphone contient une carte SIM correspondant à la ligne 06 71 42 88 19. Le "
            "répertoire comporte un contact enregistré sous le nom « Yaya » associé au 07 58 23 64 10.",
            "La messagerie comporte, le 20/09/2032 à 22h48, un message envoyé au contact « Rico » : "
            "« Box ouvert, 4 sacs, fais vite. »",
            "Le second téléphone ne comporte aucune carte SIM ; ses données ont été effacées.",
            "Les captures d'écran sont annexées au présent procès-verbal.",
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 26/09/2032 à 06h00, il est mis fin à la garde à vue de Karim TALBI, qui est conduit "
            "au tribunal judiciaire de Port-Bréval pour y être présenté à la procureure de la "
            "République.",
        ),
        piece(
            "PROCÈS-VERBAL D'INTERPELLATION",
            "Le 23/09/2032 à 06h20, nous, brigadier Thomas RENOU, agent de police judiciaire, "
            "procédons à l'interpellation de Yanis BOUCHARD au 5 rue du Môle à Port-Bréval.",
            "L'intéressé ouvre la porte en tenue de nuit et ne fait aucune difficulté.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 23/09/2032 à 06h30, nous, lieutenant Sarah KHELIF, officier de police judiciaire, "
            "notifions à Yanis BOUCHARD, né le 17/02/2001 à Port-Bréval, demeurant 5 rue du Môle à "
            "Port-Bréval, son placement en garde à vue pour des faits de trafic de stupéfiants.",
            "Interpellation effectuée le 23/09/2032 à 06h05, 5 rue du Môle à Port-Bréval.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
            "Le 23/09/2032 à 06h35, nous notifions à Yanis BOUCHARD ses droits attachés à la mesure "
            "de garde à vue : droit de faire prévenir un proche, droit d'être examiné par un "
            "médecin, droit d'être assisté par un avocat, droit de se taire.",
            "Yanis BOUCHARD demande l'assistance d'un avocat commis d'office et ne souhaite pas être "
            "examiné par un médecin.",
        ),
        piece(
            "PROCÈS-VERBAL DE PERQUISITION ET DE SAISIE",
            "Le 23/09/2032 de 07h00 à 07h45, perquisition au domicile de Yanis BOUCHARD, 5 rue du "
            "Môle à Port-Bréval, en présence de l'intéressé.",
            "Sont saisis : trois téléphones portables, dont un téléphone de marque Corvex sans carte "
            "SIM, une balance de précision et un carnet comportant des noms et des sommes.",
            "Aucun produit stupéfiant n'est découvert.",
        ),
        piece(
            "PROCÈS-VERBAL D'ENTRETIEN AVEC L'AVOCAT",
            "Yanis BOUCHARD, placé en garde à vue, a demandé l'assistance d'un avocat commis d'office.",
            "Demande d'entretien formulée le 23/09/2032 à 06h36.",
            "Entretien avec Maître Inès VALLON, avocate commise d'office, réalisé le 23/09/2032 de "
            "09h30 à 09h55.",
        ),
        audition(
            "Yanis BOUCHARD", "MIS EN CAUSE", "23/09/2032", "10h15", "11h30", APJ_RESSAC,
            [
                ("Vous êtes assisté de Maître Inès VALLON. Quelle est votre situation ?",
                 "Je suis serveur dans une brasserie du port, je travaille surtout le soir."),
                ("Utilisez-vous la ligne 07 58 23 64 10 ?",
                 "Oui, c'est mon numéro."),
                ("Connaissez-vous Karim TALBI ?",
                 "Non, je ne connais pas de Karim TALBI. Je ne vois pas qui c'est."),
                ("Votre ligne a échangé plus de deux cents communications avec une ligne qu'il "
                 "utilise. Comment l'expliquez-vous ?",
                 "Je ne sais pas, peut-être un client de la brasserie. J'appelle beaucoup de monde."),
                ("À quoi sert la balance de précision saisie chez vous ?",
                 "C'est pour la cuisine, je fais de la pâtisserie."),
                ("Que contient le carnet saisi ?",
                 "Ce sont des dettes entre amis, des paris sur le football."),
                ("Où étiez-vous le 20/09/2032 vers 23 heures ?",
                 "Je travaillais à la brasserie jusqu'à minuit."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE PROLONGATION DE GARDE À VUE",
            "Le 24/09/2032 à 06h15, Mme Claire DESMARETS, procureure de la République de Port-Bréval, "
            "autorise la prolongation de la garde à vue de Yanis BOUCHARD pour une durée de "
            "vingt-quatre heures.",
        ),
        audition(
            "Yanis BOUCHARD", "MIS EN CAUSE", "24/09/2032", "15h00", "16h40", APJ_RESSAC,
            [
                ("Vous êtes assisté de Maître Inès VALLON. On vous fait écouter les communications du "
                 "15/09/2032 et du 16/09/2032. Reconnaissez-vous votre voix ?",
                 "Oui, c'est moi."),
                ("Et la voix de votre interlocuteur ?",
                 "C'est Karim. Je le connais depuis le lycée, on était dans la même classe."),
                ("Lors de votre première audition, vous avez déclaré ne pas le connaître. Pourquoi ?",
                 "J'avais peur. Karim est quelqu'un de dangereux quand on parle."),
                ("Que signifie « il me faut les quatre » ?",
                 "Quatre sacs. Je devais aider à charger, il me donnait cent euros à chaque fois."),
                ("Étiez-vous présent le 20/09/2032 au chargement du fourgon ?",
                 "Oui, j'étais là. On était trois avec Karim et son cousin."),
                ("Quel rôle joue Nadia OGIER ?",
                 "Elle garde l'argent quelquefois. Elle n'y est pour rien dans le reste."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 24/09/2032 à 18h30, il est mis fin à la garde à vue de Yanis BOUCHARD, qui est remis "
            "en liberté et convoqué devant le juge d'instruction.",
        ),
        audition(
            "Nadia OGIER", "MIS EN CAUSE", "24/09/2032", "10h00", "11h40", APJ_RESSAC,
            [
                ("Vous êtes entendue librement. Vous avez été informée de votre droit de quitter les "
                 "locaux à tout moment. Quelle est votre situation ?",
                 "Je suis aide-soignante à la clinique du Phare, je vis avec Yanis BOUCHARD."),
                ("Avez-vous déposé une enveloppe chez la mère de Karim TALBI le 19/09/2032 ?",
                 "Oui, Yanis me l'avait demandé. Je ne savais pas ce qu'il y avait dedans."),
                ("Dans une communication, vous parlez de trois mille. Qu'est-ce que cela signifie ?",
                 "Yanis m'a dit que c'était pour rembourser une dette. Je ne pose pas de questions."),
                ("Utilisez-vous la ligne 06 12 90 37 55 ?",
                 "Oui, c'est mon second téléphone, pour le travail."),
                ("Saviez-vous que Yanis participait à un trafic ?",
                 "Non. Je savais qu'il fumait, c'est tout."),
            ],
            libre=True,
        ),
        piece(
            "RAPPORT D'EXPERTISE TOXICOLOGIQUE",
            "Laboratoire de police scientifique de Varennes. Expert : Docteur Agathe MERCIER.",
            "Scellé numéro 3 reçu le 24/09/2032 : douze plaquettes de produit brunâtre sous film "
            "plastique.",
            "Poids net constaté après retrait des emballages : 980 grammes.",
            "Les analyses mettent en évidence du tétrahydrocannabinol, avec une teneur moyenne de "
            "18 %. Le produit est de la résine de cannabis.",
            "Conclusions transmises le 02/10/2032.",
            scan=True,
        ),
        piece(
            "ENQUÊTE DE PERSONNALITÉ — KARIM TALBI",
            "Né le 04/11/1994 à Port-Bréval, célibataire, sans enfant, vit chez sa mère au 12 allée "
            "des Mouettes.",
            "Scolarité jusqu'au baccalauréat professionnel en logistique, obtenu en 2013. Emplois "
            "successifs en intérim comme cariste depuis 2015.",
            "Sa mère, Mme Fatima TALBI, le décrit comme serviable et présent pour elle depuis le "
            "décès de son père. Elle indique ignorer l'usage du box.",
            "L'intéressé déclare une consommation de cannabis depuis l'âge de seize ans.",
        ),
        piece(
            "ENQUÊTE DE PERSONNALITÉ — YANIS BOUCHARD",
            "Né le 17/02/2001 à Port-Bréval, vit en concubinage avec Nadia OGIER au 5 rue du Môle.",
            "Titulaire d'un CAP de service en restauration, serveur dans une brasserie du port "
            "depuis 2021. Son employeur le décrit comme ponctuel et apprécié de la clientèle.",
            "Il déclare des dettes de jeu d'environ 2 000 euros.",
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "TALBI Karim, né le 04/11/1994 à Port-Bréval.",
            "Condamnation du 12/03/2019, tribunal correctionnel de Port-Bréval : détention de "
            "stupéfiants, 6 mois d'emprisonnement avec sursis.",
            "Condamnation du 08/10/2021, tribunal correctionnel de Port-Bréval : conduite sous "
            "l'emprise de stupéfiants, 400 euros d'amende.",
            scan=True,
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "BOUCHARD Yanis, né le 17/02/2001 à Port-Bréval.",
            "Néant.",
        ),
        piece(
            "RÉQUISITOIRE INTRODUCTIF",
            "Le 26/09/2032, Mme Claire DESMARETS, procureure de la République de Port-Bréval, vu les "
            "pièces de la procédure, requiert l'ouverture d'une information judiciaire contre Karim "
            "TALBI et Yanis BOUCHARD des chefs de détention, transport, offre ou cession de "
            "stupéfiants et d'association de malfaiteurs, et contre X.",
            "Elle requiert le placement de Karim TALBI en détention provisoire.",
        ),
    ]


def generer_ressac(dossier: Path) -> VeriteDossier:
    chemin = dossier / "dossier_ressac.pdf"
    nb, scans = ecrire_dossier(_pieces_ressac(), chemin, PARQUET_RESSAC)
    return VeriteDossier(
        chemin_pdf=chemin, nb_pages=nb, pages_scan=scans,
        contradictions_par_regles=(
            ("Interpellation de Yanis BOUCHARD", "06h05", "06h20"),
            ("Téléphone", "06 71 42 88 19", "06 71 42 88 91"),
        ),
        contradictions_modele=(
            ("1 200", "980", "poids", "grammes"),
            ("blanc", "gris", "fourgon", "couleur"),
        ),
        durees_gav={
            "Karim TALBI": minutes_entre("23/09/2032 06h10", "26/09/2032 06h00"),
            "Yanis BOUCHARD": minutes_entre("23/09/2032 06h30", "24/09/2032 18h30"),
        },
        mis_en_cause=("Karim TALBI", "Yanis BOUCHARD", "Nadia OGIER"),
        jamais_mis_en_cause=(
            "Claire DESMARETS", "Hervé LANGLOIS", "Laurent VIGIER", "Sarah KHELIF", "Thomas RENOU",
            "Paul ESTRADE", "Inès VALLON", "Marc ROUSSEL", "Agathe MERCIER", "Odette MARCHAL",
            "Fatima TALBI",
        ),
        questions=(
            QuestionAttendue("À quelle heure Yanis BOUCHARD a-t-il été interpellé ?", heures=((6, 5), (6, 20))),
            QuestionAttendue("Quel poids de résine de cannabis a été saisi ?", mots=("1 200", "980")),
            QuestionAttendue("Yanis BOUCHARD connaît-il Karim TALBI ?", mots=("lycée",)),
        ),
    )


# --- Dossier 2 : Rue Pasteur (vol avec violences) -------------------------------

PARQUET_PASTEUR = "2033/00342"
OPJ_PASTEUR = "capitaine Mathilde ROCHE, officier de police judiciaire au commissariat de Saint-Amarin"
APJ_PASTEUR = "brigadier-chef Olivier DANTEC, agent de police judiciaire"


def _pieces_pasteur() -> list[dict]:
    return [
        piece(
            "PROCÈS-VERBAL DE SYNTHÈSE",
            "Commissariat de Saint-Amarin. Le 23/05/2033, la capitaine Mathilde ROCHE, officier de "
            "police judiciaire, dresse la synthèse de l'enquête de flagrance ouverte le 19/05/2033.",
            "Le 19/05/2033 vers 21h30, Damien LACOMBE, livreur, a été agressé rue Pasteur à "
            "Saint-Amarin par deux individus qui lui ont dérobé son scooter et son téléphone. Le "
            "certificat médical fixe une incapacité totale de travail de 5 jours.",
            "L'exploitation de la vidéoprotection, les témoignages et la téléphonie ont permis "
            "d'identifier Dylan MARCOUX et Kevin SAUNIER, interpellés le 21/05/2033.",
            "Chacun reconnaît sa présence et attribue les coups à l'autre. Le scooter a été "
            "retrouvé le 20/05/2033 dans un parking du quartier des Tilleuls.",
            "La victime a désigné Dylan MARCOUX sur planche photographique. Le téléphone de la "
            "victime a été retrouvé à son domicile, ainsi qu'un blouson rouge correspondant à la "
            "description donnée par la victime et par un témoin ; un second témoin décrit une veste "
            "noire.",
            "Le profil génétique de Kevin SAUNIER a été identifié sur le casque retrouvé sous la "
            "selle du scooter. La téléphonie situe sa ligne rue Pasteur à 21h38.",
            "Lors de la confrontation du 22/05/2033, chacun a maintenu que l'autre avait porté les "
            "coups.",
            "Les deux mis en cause sont convoqués devant le tribunal correctionnel.",
        ),
        audition(
            "Damien LACOMBE", "VICTIME", "20/05/2033", "09h00", "10h30", OPJ_PASTEUR,
            [
                ("Vous déposez plainte. Que s'est-il passé ?",
                 "Le 19/05/2033 vers 21h30, je livrais une commande rue Pasteur. En sortant de "
                 "l'immeuble, deux hommes m'attendaient à côté de mon scooter."),
                ("Que vous ont-ils dit ?",
                 "Le plus grand m'a dit de donner les clés. J'ai refusé, il m'a donné un coup de poing "
                 "au visage et je suis tombé."),
                ("Pouvez-vous décrire les deux hommes ?",
                 "Le plus grand portait un blouson rouge et une capuche. L'autre était plus petit, "
                 "avec une casquette noire et une sacoche."),
                ("Avez-vous reçu d'autres coups ?",
                 "Oui, une fois au sol, j'ai reçu des coups de pied dans les côtes. Je ne sais pas "
                 "lequel des deux frappait."),
                ("Qu'ont-ils emporté ?",
                 "Mon scooter Yamaha immatriculé FT-318-QA, avec le sac isotherme, et mon téléphone "
                 "portable qui était dans ma poche."),
                ("Quel est le numéro de votre téléphone ?",
                 "C'est le 06 44 17 52 80."),
                ("Avez-vous été soigné ?",
                 "Je suis allé aux urgences de l'hôpital de Saint-Amarin dans la nuit, j'ai une côte "
                 "fêlée et une plaie à l'arcade."),
                ("Pourriez-vous reconnaître vos agresseurs ?",
                 "Le grand, peut-être. Je l'ai bien vu quand il m'a parlé."),
                ("Quelle est votre situation professionnelle ?",
                 "Je suis livreur indépendant pour une plateforme depuis un an. Le scooter est mon "
                 "seul outil de travail."),
                ("Combien de temps a duré l'agression ?",
                 "Une minute, peut-être deux. Tout est allé très vite."),
                ("Quelqu'un est-il intervenu ?",
                 "Un homme qui passait a appelé la police et il est resté avec moi."),
                ("Avez-vous remarqué les deux hommes avant d'entrer dans l'immeuble ?",
                 "Non, je n'ai fait attention à rien, j'étais pressé, j'avais une autre commande."),
                ("Quel est le préjudice matériel ?",
                 "Le sac isotherme vaut 60 euros, le téléphone 400 euros. Sans scooter, je ne peux pas "
                 "travailler."),
            ],
        ),
        piece(
            "CERTIFICAT MÉDICAL",
            "Unité médico-judiciaire de Saint-Amarin. Je soussigné, Docteur Lucie GARNIER, "
            "certifie avoir examiné ce jour 20/05/2033 à 11h40, sur réquisition du 20/05/2033 reçue "
            "à 10h45, Damien LACOMBE.",
            "Constatations : plaie de l'arcade sourcilière gauche suturée, hématome de la "
            "pommette gauche, fracture non déplacée de la neuvième côte droite.",
            "Ces lésions entraînent une incapacité totale de travail de 8 jours, sous réserve de "
            "complications.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE TRANSPORT ET DE CONSTATATIONS",
            "Le 19/05/2033 à 22h05, nous, brigadier-chef Olivier DANTEC, nous transportons au 16 rue "
            "Pasteur à Saint-Amarin à la suite d'un appel au 17.",
            "Nous constatons la présence d'un livreur assis sur le trottoir, saignant de l'arcade "
            "gauche, pris en charge par les sapeurs-pompiers. Un témoin, présent sur place, déclare "
            "avoir appelé les secours.",
            "Des traces de sang sont relevées sur le trottoir. Aucun objet n'est retrouvé sur place.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL D'ENQUÊTE DE VOISINAGE",
            "Le 20/05/2033, nous, brigadier-chef Olivier DANTEC, procédons à une enquête de "
            "voisinage aux 12, 14, 16 et 18 rue Pasteur.",
            "Au 12 : personne n'a rien vu ni entendu. Au 14 : une habitante du deuxième étage "
            "déclare avoir assisté à la scène depuis sa fenêtre ; elle est convoquée. Au 16 : le "
            "client livré déclare avoir entendu des cris peu après le départ du livreur. Au 18 : "
            "absence des occupants.",
        ),
        audition(
            "Martine PERRIN", "TÉMOIN", "20/05/2033", "14h00", "14h35", APJ_PASTEUR,
            [
                ("Qu'avez-vous vu le 19/05/2033 au soir ?",
                 "J'étais à ma fenêtre au deuxième étage du 14 rue Pasteur. Il était à peu près 21h30. "
                 "J'ai vu un homme en blouson rouge frapper le livreur, qui est tombé."),
                ("Combien étaient-ils ?",
                 "Deux. Le second était plus petit, il tenait le scooter."),
                ("Qui a donné les coups de pied ?",
                 "L'homme au blouson rouge. L'autre a démarré le scooter."),
                ("Dans quelle direction sont-ils partis ?",
                 "Vers le quartier des Tilleuls, tous les deux sur le scooter."),
            ],
        ),
        audition(
            "Jérôme VASSEUR", "TÉMOIN", "20/05/2033", "16h00", "16h30", APJ_PASTEUR,
            [
                ("Qu'avez-vous vu le 19/05/2033 au soir ?",
                 "Je rentrais du sport, il était environ 21h45. J'ai vu deux jeunes autour d'un "
                 "livreur allongé par terre, rue Pasteur."),
                ("Pouvez-vous les décrire ?",
                 "Le plus grand avait une veste noire avec une capuche. Le petit avait une casquette."),
                ("Avez-vous vu des coups ?",
                 "J'ai vu le grand donner un coup de pied au livreur. Ensuite ils sont partis en "
                 "scooter."),
                ("Avez-vous appelé les secours ?",
                 "Oui, j'ai appelé le 17 et je suis resté avec le livreur jusqu'à l'arrivée de la "
                 "police."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE CONSTATATIONS — EXPLOITATION DE LA VIDÉOPROTECTION",
            "Le 20/05/2033, nous, brigadier-chef Olivier DANTEC, procédons à l'exploitation des "
            "images de la caméra municipale numéro 14 orientée vers la rue Pasteur.",
            "À 21h42 et 10 secondes, deux individus se tiennent près d'un scooter stationné. À "
            "21h42 et 35 secondes, un livreur sort de l'immeuble du 16 rue Pasteur.",
            "À 21h43, le plus grand des deux individus, vêtu d'un haut de couleur sombre avec "
            "capuche, porte un coup au visage du livreur, qui chute. Le même individu lui porte "
            "plusieurs coups de pied.",
            "À 21h44, les deux individus quittent les lieux sur le scooter en direction du quartier "
            "des Tilleuls. La plaque n'est pas lisible.",
            "Les captures d'écran sont annexées au présent procès-verbal.",
        ),
        piece(
            "PROCÈS-VERBAL DE DÉCOUVERTE DE VÉHICULE",
            "Le 20/05/2033 à 08h20, nous, brigadier-chef Olivier DANTEC, découvrons au parking du "
            "quartier des Tilleuls à Saint-Amarin un scooter Yamaha immatriculé FT-381-QA, signalé "
            "volé, contact forcé, sans sac isotherme.",
            "Le casque abandonné sous la selle est placé sous scellé pour analyse.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE RESTITUTION",
            "Le 20/05/2033 à 15h30, après constatations et prélèvements, le scooter Yamaha est "
            "restitué à son propriétaire, Damien LACOMBE, qui en donne décharge.",
            "Damien LACOMBE précise que le sac isotherme et un antivol ont disparu.",
        ),
        piece(
            "RÉQUISITION AUX FINS D'EXTRACTION DE VIDÉOPROTECTION",
            "Le 20/05/2033, nous, capitaine Mathilde ROCHE, requérons le centre de supervision "
            "urbaine de Saint-Amarin de nous remettre les enregistrements des caméras numéros 14, "
            "15 et 22 pour la soirée du 19/05/2033, de 21h00 à 23h00.",
            "Les enregistrements sont remis le 20/05/2033 à 11h00 sur support numérique, placé sous "
            "scellé.",
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À OPÉRATEUR DE TÉLÉPHONIE",
            "Le 20/05/2033, nous, capitaine Mathilde ROCHE, requérons la société Nérée Mobile de "
            "nous communiquer le bornage de la ligne 06 44 17 52 80 appartenant à la victime, du "
            "19/05/2033 à 21h00 au 20/05/2033 à 12h00.",
            "Réponse le 20/05/2033 : la ligne a borné à 22h05 sur le relais du quartier des "
            "Tilleuls, puis à 22h31 sur un relais couvrant le 3 impasse des Saules, avant extinction.",
        ),
        piece(
            "PROCÈS-VERBAL D'ANALYSE TÉLÉPHONIQUE",
            "Le 21/05/2033, nous, capitaine Mathilde ROCHE, procédons à l'analyse des bornages "
            "communiqués par la société Nérée Mobile pour la ligne 06 51 08 73 26 attribuée à Kevin "
            "SAUNIER.",
            "Le 19/05/2033, la ligne borne à 21h12 sur le relais couvrant la gare, à 21h38 sur le "
            "relais couvrant la rue Pasteur, puis à 22h03 sur le relais du quartier des Tilleuls.",
            "Entre 21h00 et 22h30, la ligne échange quatre appels avec la ligne 06 30 61 49 85, "
            "attribuée à Dylan MARCOUX, dont le dernier à 21h40, d'une durée de 12 secondes.",
            "Ces éléments sont compatibles avec une présence rue Pasteur au moment des faits.",
        ),
        piece(
            "PROCÈS-VERBAL D'IDENTIFICATION",
            "Le 20/05/2033, nous, capitaine Mathilde ROCHE, établissons que le 3 impasse des Saules "
            "est le domicile de Dylan MARCOUX, né le 09/08/2012, connu des services pour des vols.",
            "Une planche photographique de six personnes est présentée le 20/05/2033 à 18h00 à "
            "Damien LACOMBE, qui désigne la photographie numéro 4, celle de Dylan MARCOUX, en "
            "déclarant : « Je pense que c'est lui, à 80 %, le grand qui m'a parlé. »",
            "L'exploitation des relations de Dylan MARCOUX conduit à identifier Kevin SAUNIER, né le "
            "30/01/2013, vu en sa compagnie sur les images de la vidéoprotection de la gare.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 21/05/2033 à 06h30, nous, capitaine Mathilde ROCHE, officier de police judiciaire, "
            "notifions à Dylan MARCOUX, demeurant 3 impasse des Saules à Saint-Amarin, son "
            "placement en garde à vue pour des faits de vol avec violences.",
            "Interpellation effectuée le 21/05/2033 à 06h15, 3 impasse des Saules à Saint-Amarin.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
            "Le 21/05/2033 à 06h35, nous notifions à Dylan MARCOUX ses droits attachés à la mesure "
            "de garde à vue : droit de faire prévenir un proche, droit d'être examiné par un "
            "médecin, droit d'être assisté par un avocat, droit de se taire.",
            "Dylan MARCOUX demande l'assistance d'un avocat commis d'office.",
        ),
        piece(
            "PROCÈS-VERBAL DE PERQUISITION ET DE SAISIE",
            "Le 21/05/2033 de 06h50 à 07h30, perquisition au domicile de Dylan MARCOUX, 3 impasse "
            "des Saules à Saint-Amarin, en présence de l'intéressé.",
            "Sont saisis : un blouson de couleur rouge à capuche, une paire de baskets portant des "
            "traces brunâtres, un téléphone portable de marque Corvex éteint, dont le numéro IMEI "
            "correspond au téléphone dérobé à la victime.",
        ),
        piece(
            "PROCÈS-VERBAL D'ENTRETIEN AVEC L'AVOCAT",
            "Dylan MARCOUX, placé en garde à vue, a demandé l'assistance d'un avocat.",
            "Demande d'entretien formulée le 21/05/2033 à 06h36.",
            "Entretien avec Maître Romain BERTHIER, avocat commis d'office, réalisé le 21/05/2033 de "
            "08h40 à 09h05.",
        ),
        audition(
            "Dylan MARCOUX", "MIS EN CAUSE", "21/05/2033", "09h20", "10h45", OPJ_PASTEUR,
            [
                ("Vous êtes assisté de Maître Romain BERTHIER. Où étiez-vous le 19/05/2033 au soir ?",
                 "Chez moi, je n'ai pas bougé de la soirée."),
                ("Le téléphone de la victime a été retrouvé chez vous. Comment l'expliquez-vous ?",
                 "Je l'ai acheté à quelqu'un dans la rue le lendemain, pour trente euros."),
                ("La victime vous a désigné sur une planche photographique. Qu'en dites-vous ?",
                 "Elle se trompe. Plein de gens me ressemblent."),
                ("À qui appartient le blouson rouge saisi chez vous ?",
                 "C'est le mien, mais je ne l'ai pas mis ce soir-là."),
                ("Quelle est votre situation ?",
                 "Je vis chez ma mère. J'ai arrêté le lycée l'an dernier, je cherche un apprentissage "
                 "avec la mission locale."),
                ("Connaissez-vous Kevin SAUNIER ?",
                 "Oui, c'est un ami d'enfance, on habitait le même immeuble avant."),
                ("Votre ligne a échangé quatre appels avec la sienne le 19/05/2033 au soir. Pourquoi ?",
                 "On s'appelle tout le temps, je ne me souviens pas de ce soir-là en particulier."),
                ("Qui vous a vendu le téléphone de la victime ?",
                 "Un gars que je ne connais pas, près de la gare. Je ne peux pas le décrire."),
                ("Comment expliquez-vous que le téléphone ait borné près de chez vous dès 22h31 le "
                 "19/05/2033, et non le lendemain ?",
                 "Je ne sais pas. Je n'ai rien à ajouter."),
                ("Avez-vous déjà eu affaire à la justice ?",
                 "Oui, pour un vol de vélo. J'ai fait des travaux d'intérêt général."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE PROLONGATION DE GARDE À VUE",
            "Le 22/05/2033 à 06h20, M. Bertrand LEMAIRE, substitut du procureur de la République de "
            "Saint-Amarin, autorise la prolongation de la garde à vue de Dylan MARCOUX pour une "
            "durée de vingt-quatre heures.",
        ),
        audition(
            "Dylan MARCOUX", "MIS EN CAUSE", "22/05/2033", "08h00", "09h10", OPJ_PASTEUR,
            [
                ("Vous êtes assisté de Maître Romain BERTHIER. Kevin SAUNIER déclare que vous étiez "
                 "avec lui rue Pasteur et que c'est vous qui avez frappé. Qu'en dites-vous ?",
                 "D'accord, j'étais là. Mais c'est Kevin qui a frappé le livreur. Moi je devais "
                 "seulement prendre le scooter."),
                ("Qui portait le blouson rouge ?",
                 "Kevin me l'avait emprunté ce soir-là. Il me l'a rendu chez moi après."),
                ("Qui a donné les coups de pied ?",
                 "Kevin. Je lui ai dit d'arrêter."),
                ("Qu'avez-vous fait du sac isotherme ?",
                 "On l'a jeté dans une benne près de la gare."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE CONFRONTATION",
            "Le 22/05/2033 de 09h30 à 10h20, nous, capitaine Mathilde ROCHE, procédons à la "
            "confrontation de Dylan MARCOUX, assisté de Maître Romain BERTHIER, et de Kevin "
            "SAUNIER, qui a renoncé à l'assistance d'un avocat.",
            "Question à Kevin SAUNIER : Qui portait le blouson rouge le 19/05/2033 ?",
            "Réponse de Kevin SAUNIER : Dylan. C'est son blouson, il le porte tout le temps.",
            "Question à Dylan MARCOUX : Qu'avez-vous à répondre ?",
            "Réponse de Dylan MARCOUX : Kevin l'avait ce soir-là, il avait froid.",
            "Question à Kevin SAUNIER : Qui a porté les coups de pied ?",
            "Réponse de Kevin SAUNIER : Dylan. Moi je tenais le scooter, je ne pouvais pas frapper.",
            "Question à Dylan MARCOUX : Votre ADN n'a pas été retrouvé sur le casque, celui de Kevin "
            "SAUNIER oui. Qu'en dites-vous ?",
            "Réponse de Dylan MARCOUX : Ça prouve que c'est lui qui conduisait, pas que je frappais.",
            "Chacun maintient ses déclarations.",
        ),
        audition(
            "Damien LACOMBE", "VICTIME", "22/05/2033", "14h00", "14h30", OPJ_PASTEUR,
            [
                ("On vous présente les photographies du blouson rouge saisi. Le reconnaissez-vous ?",
                 "Oui, c'est le même modèle, avec la bande blanche sur la manche."),
                ("Êtes-vous certain que l'homme au blouson rouge est celui qui vous a parlé ?",
                 "Oui, c'est le grand, celui qui m'a demandé les clés et qui m'a frappé."),
                ("Avez-vous repris le travail ?",
                 "Non, je suis en arrêt jusqu'à la fin du mois, je ne peux pas porter de charges."),
                ("Souhaitez-vous vous constituer partie civile ?",
                 "Oui, pour mon arrêt de travail et le matériel perdu."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 22/05/2033 à 11h30, il est mis fin à la garde à vue de Dylan MARCOUX, qui est remis "
            "en liberté avec une convocation devant le tribunal correctionnel.",
        ),
        piece(
            "PROCÈS-VERBAL D'INTERPELLATION",
            "Le 21/05/2033 à 09h25, nous, brigadier-chef Olivier DANTEC, agent de police judiciaire, "
            "procédons à l'interpellation de Kevin SAUNIER devant la gare de Saint-Amarin.",
            "L'intéressé tente de s'enfuir avant d'être rattrapé.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 21/05/2033 à 09h40, nous, capitaine Mathilde ROCHE, officier de police judiciaire, "
            "notifions à Kevin SAUNIER, demeurant 22 avenue des Lilas à Saint-Amarin, son placement "
            "en garde à vue pour des faits de vol avec violences.",
            "Interpellation effectuée le 21/05/2033 à 09h10, devant la gare de Saint-Amarin.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
            "Le 21/05/2033 à 09h45, nous notifions à Kevin SAUNIER ses droits attachés à la mesure "
            "de garde à vue : droit de faire prévenir un proche, droit d'être examiné par un "
            "médecin, droit d'être assisté par un avocat, droit de se taire.",
            "Kevin SAUNIER renonce à l'assistance d'un avocat.",
        ),
        audition(
            "Kevin SAUNIER", "MIS EN CAUSE", "21/05/2033", "14h00", "15h30", APJ_PASTEUR,
            [
                ("Vous avez renoncé à l'assistance d'un avocat. Où étiez-vous le 19/05/2033 vers "
                 "21h30 ?",
                 "Rue Pasteur, avec Dylan. Je ne vais pas mentir, on voulait prendre un scooter."),
                ("Qui a frappé le livreur ?",
                 "C'est Dylan. Il avait son blouson rouge. Moi je tenais le scooter."),
                ("Combien de coups ont été portés ?",
                 "Un coup de poing, puis des coups de pied quand le livreur était par terre. C'est "
                 "Dylan qui a tout fait."),
                ("Pourquoi avez-vous tenté de fuir lors de votre interpellation ?",
                 "J'ai paniqué."),
                ("Qu'avez-vous fait du téléphone du livreur ?",
                 "Dylan l'a gardé."),
                ("Quelle est votre situation ?",
                 "Je suis apprenti mécanicien. J'habite chez mes grands-parents, avenue des Lilas."),
                ("Pourquoi vouliez-vous prendre un scooter ?",
                 "Pour le revendre en pièces. Dylan connaissait quelqu'un aux Tilleuls."),
                ("Votre ADN a été retrouvé sur le casque du scooter. Comment l'expliquez-vous ?",
                 "C'est moi qui ai conduit le scooter jusqu'aux Tilleuls. J'ai mis le casque."),
                ("Où avez-vous jeté le sac isotherme ?",
                 "Dans une poubelle près de la gare, le lendemain matin."),
                ("Regrettez-vous les faits ?",
                 "Oui. Je ne pensais pas qu'il frapperait aussi fort. Je veux m'excuser auprès du "
                 "livreur."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE RECHERCHES",
            "Le 21/05/2033 à 17h00, sur les indications recueillies, nous, brigadier-chef Olivier "
            "DANTEC, procédons à des recherches dans les conteneurs situés aux abords de la gare de "
            "Saint-Amarin.",
            "Un sac isotherme de couleur verte portant le logo d'une plateforme de livraison est "
            "découvert dans le conteneur numéro 3. Il est placé sous scellé.",
            scan=True,
        ),
        audition(
            "Sandrine MARCOUX", "TÉMOIN", "21/05/2033", "16h00", "16h40", APJ_PASTEUR,
            [
                ("Vous êtes la mère de Dylan MARCOUX. Était-il chez vous le 19/05/2033 au soir ?",
                 "Il est sorti après le dîner, vers 20h30. Je l'ai entendu rentrer vers 23 heures."),
                ("Portait-il un blouson rouge ?",
                 "Oui, il ne quitte jamais ce blouson. Il l'avait en partant."),
                ("Avez-vous vu un téléphone que vous ne connaissiez pas ?",
                 "Non, je ne fouille pas ses affaires."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 21/05/2033 à 23h10, il est mis fin à la garde à vue de Kevin SAUNIER, qui est remis "
            "en liberté avec une convocation devant le tribunal correctionnel.",
        ),
        piece(
            "RAPPORT D'EXPERTISE GÉNÉTIQUE",
            "Laboratoire de police scientifique de Varennes. Expert : Docteur Agathe MERCIER.",
            "Scellé : casque retrouvé sous la selle du scooter. Deux profils génétiques sont mis "
            "en évidence.",
            "Le profil majoritaire correspond à celui de la victime, Damien LACOMBE. Le profil "
            "minoritaire correspond à celui de Kevin SAUNIER, enregistré au fichier.",
            "Conclusions transmises le 30/05/2033.",
            scan=True,
        ),
        piece(
            "ENQUÊTE DE PERSONNALITÉ — DYLAN MARCOUX",
            "Né le 09/08/2012, vit chez sa mère au 3 impasse des Saules. Déscolarisé depuis un an, "
            "inscrit à la mission locale.",
            "Sa mère indique qu'il sort beaucoup le soir et fréquente Kevin SAUNIER depuis l'enfance.",
        ),
        piece(
            "ENQUÊTE DE PERSONNALITÉ — KEVIN SAUNIER",
            "Né le 30/01/2013, vit chez ses grands-parents au 22 avenue des Lilas. Apprenti "
            "mécanicien dans un garage du centre-ville depuis septembre 2032.",
            "Son maître d'apprentissage le décrit comme travailleur mais influençable.",
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "MARCOUX Dylan, né le 09/08/2012.",
            "Condamnation du 15/01/2032, tribunal correctionnel de Saint-Amarin : vol simple, "
            "travail d'intérêt général de 70 heures.",
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "SAUNIER Kevin, né le 30/01/2013.",
            "Néant.",
            scan=True,
        ),
        piece(
            "SOIT-TRANSMIS",
            "Le 23/05/2033, M. Bertrand LEMAIRE, substitut du procureur de la République de "
            "Saint-Amarin, décide de poursuivre Dylan MARCOUX et Kevin SAUNIER devant le tribunal "
            "correctionnel par convocation par procès-verbal, pour l'audience du 04/07/2033.",
        ),
    ]


def generer_pasteur(dossier: Path) -> VeriteDossier:
    chemin = dossier / "dossier_pasteur.pdf"
    nb, scans = ecrire_dossier(_pieces_pasteur(), chemin, PARQUET_PASTEUR)
    return VeriteDossier(
        chemin_pdf=chemin, nb_pages=nb, pages_scan=scans,
        contradictions_par_regles=(
            ("Interpellation de Kevin SAUNIER", "09h10", "09h25"),
            ("Plaque d'immatriculation", "FT-318-QA", "FT-381-QA"),
        ),
        contradictions_modele=(
            ("itt", "8", "5", "incapacite"),
            ("rouge", "noire", "blouson", "veste"),
        ),
        durees_gav={
            "Dylan MARCOUX": minutes_entre("21/05/2033 06h30", "22/05/2033 11h30"),
            "Kevin SAUNIER": minutes_entre("21/05/2033 09h40", "21/05/2033 23h10"),
        },
        mis_en_cause=("Dylan MARCOUX", "Kevin SAUNIER"),
        jamais_mis_en_cause=(
            "Mathilde ROCHE", "Olivier DANTEC", "Bertrand LEMAIRE", "Romain BERTHIER", "Lucie GARNIER",
            "Agathe MERCIER", "Damien LACOMBE", "Martine PERRIN", "Jérôme VASSEUR", "Sandrine MARCOUX",
        ),
        questions=(
            QuestionAttendue("À quelle heure Kevin SAUNIER a-t-il été interpellé ?", heures=((9, 10), (9, 25))),
            QuestionAttendue("Combien de jours d'ITT ont été retenus pour Damien LACOMBE ?", mots=("8",)),
            QuestionAttendue("Qui a frappé Damien LACOMBE selon les mis en cause ?", mots=("MARCOUX", "SAUNIER")),
        ),
    )


# --- Dossier 3 : faux conseiller bancaire (escroquerie) ------------------------

PARQUET_ARMORINE = "2033/00119"
OPJ_ARMORINE = "lieutenant Hugo LEBRETON, officier de police judiciaire à la brigade financière de Kerlan"
APJ_ARMORINE = "brigadier Chloé MAUGER, agent de police judiciaire"


def _pieces_armorine() -> list[dict]:
    return [
        piece(
            "PROCÈS-VERBAL DE SYNTHÈSE",
            "Brigade financière de Kerlan. Le 10/02/2033, le lieutenant Hugo LEBRETON, officier de "
            "police judiciaire, dresse la synthèse de l'enquête ouverte le 16/01/2033 sur plainte "
            "d'Hélène ROUSSET.",
            "Entre le 14/01/2033 et le 15/01/2033, Hélène ROUSSET, âgée de 84 ans, a été contactée "
            "par un homme se présentant comme conseiller de la Banque Armorine, qui l'a convaincue "
            "d'effectuer des virements vers un compte présenté comme sécurisé. Son préjudice est "
            "évalué à 38 400 euros. Une seconde victime, Gaëlle PRIGENT, a déposé plainte le "
            "20/01/2033 pour un préjudice de 12 750 euros.",
            "Les fonds ont été virés sur un compte ouvert au nom de Lorène VIDAL, puis retirés en "
            "espèces. L'analyse téléphonique a permis d'identifier Sofiane AMRANI comme l'auteur des "
            "appels et Mehdi CARON comme l'auteur des retraits.",
            "Sofiane AMRANI, placé en garde à vue le 03/02/2033, a été présenté au juge "
            "d'instruction le 04/02/2033 et mis en examen.",
        ),
        audition(
            "Hélène ROUSSET", "VICTIME", "16/01/2033", "10h00", "11h45", OPJ_ARMORINE,
            [
                ("Vous déposez plainte. Que s'est-il passé ?",
                 "Le 14/01/2033, vers 10 heures, un monsieur m'a téléphoné en disant qu'il était de la "
                 "Banque Armorine, service des fraudes. Il connaissait mon nom et le numéro de ma carte."),
                ("Que vous a-t-il dit ?",
                 "Que des pirates essayaient de vider mon compte et qu'il fallait mettre mon argent à "
                 "l'abri sur un compte sécurisé de la banque."),
                ("Quel numéro vous appelait ?",
                 "Le numéro affiché était le 07 81 22 45 63. Il m'a dit que c'était sa ligne directe."),
                ("Combien de virements avez-vous effectués ?",
                 "Trois, le 14 et le 15. Il restait au téléphone avec moi pendant que je les faisais "
                 "sur l'ordinateur."),
                ("Vers quel compte ?",
                 "Il m'a dicté le numéro, je l'ai noté sur un papier que je vous remets : FR76 3000 4000 "
                 "1200 0012 3456 789."),
                ("À combien s'élève votre perte ?",
                 "En tout, 38 400 euros. Ce sont les économies de toute ma vie."),
                ("Pouvez-vous décrire la voix ?",
                 "Un homme jeune, très poli, sans accent particulier. Il m'appelait madame ROUSSET."),
                ("Quand avez-vous compris ?",
                 "Le 16 au matin, quand ma conseillère, Mme Agnès LE ROUX, m'a appelée parce que mon "
                 "compte était à découvert."),
                ("Souhaitez-vous vous constituer partie civile ?",
                 "Oui, je veux récupérer mon argent."),
            ],
        ),
        piece(
            "RELEVÉ DE COMPTE — BANQUE ARMORINE",
            "Titulaire : Mme Hélène ROUSSET. Compte courant numéro 0045 2287 914. Période du "
            "01/01/2033 au 16/01/2033.",
            "14/01/2033 — virement émis vers FR76 3000 4000 1200 0012 3456 789, bénéficiaire "
            "L. VIDAL : 12 800,00 euros.",
            "14/01/2033 — virement émis vers FR76 3000 4000 1200 0012 3456 789, bénéficiaire "
            "L. VIDAL : 12 800,00 euros.",
            "15/01/2033 — virement émis vers FR76 3000 4000 1200 0012 3456 789, bénéficiaire "
            "L. VIDAL : 11 300,00 euros.",
            "Total des virements émis sur la période : 36 900,00 euros. Solde au 16/01/2033 : "
            "moins 412,35 euros.",
            scan=True,
        ),
        piece(
            "CERTIFICAT MÉDICAL",
            "Je soussigné, Docteur Yves KERGOAT, médecin traitant, certifie avoir examiné ce jour "
            "17/01/2033 à 09h30 Mme Hélène ROUSSET, âgée de 84 ans.",
            "L'intéressée présente des troubles cognitifs légers, diagnostiqués en 2031, qui "
            "altèrent sa capacité à apprécier les situations nouvelles. Elle vit seule.",
            "Elle présente depuis les faits un état anxieux réactionnel.",
            scan=True,
        ),
        audition(
            "Sandrine ROUSSET", "TÉMOIN", "17/01/2033", "16h00", "16h40", APJ_ARMORINE,
            [
                ("Vous êtes la fille d'Hélène ROUSSET. Comment allait votre mère avant les faits ?",
                 "Elle vit seule et se débrouille, mais elle oublie des choses et elle fait confiance "
                 "à tout le monde au téléphone."),
                ("Vous a-t-elle parlé de ces appels ?",
                 "Non. Le faux conseiller lui avait dit de n'en parler à personne, pas même à sa "
                 "famille, pour ne pas gêner l'enquête de la banque."),
                ("Quel est l'état de votre mère depuis ?",
                 "Elle ne dort plus, elle a honte. Elle ne répond plus au téléphone."),
            ],
        ),
        audition(
            "Agnès LE ROUX", "TÉMOIN", "17/01/2033", "14h00", "14h30", APJ_ARMORINE,
            [
                ("Vous êtes la conseillère d'Hélène ROUSSET. Qu'avez-vous constaté ?",
                 "Le 16/01/2033, une alerte automatique m'a signalé trois virements inhabituels "
                 "vers une banque en ligne. J'ai appelé Mme ROUSSET immédiatement."),
                ("La banque appelle-t-elle ses clients pour déplacer leurs fonds ?",
                 "Jamais. Aucun conseiller ne demande à un client de virer son argent sur un autre "
                 "compte."),
                ("Avez-vous pu bloquer les fonds ?",
                 "Nous avons demandé le rappel des virements le 16/01/2033, mais les fonds avaient "
                 "déjà été retirés."),
            ],
        ),
        audition(
            "Gaëlle PRIGENT", "VICTIME", "20/01/2033", "09h30", "10h40", APJ_ARMORINE,
            [
                ("Que s'est-il passé ?",
                 "Le 18/01/2033 dans l'après-midi, un faux conseiller de la Banque Armorine m'a "
                 "appelée. J'ai fait deux virements, de 6 500 euros et de 6 250 euros."),
                ("Quel numéro vous a appelée ?",
                 "Le 07 81 22 54 63, je l'ai encore dans mon journal d'appels."),
                ("Vers quel compte ?",
                 "Le même nom de bénéficiaire, L. VIDAL. L'IBAN se terminait par 3465 789."),
                ("À combien s'élève votre préjudice ?",
                 "12 750 euros."),
                ("Comment l'appelant s'est-il présenté ?",
                 "Il a dit s'appeler Thomas, du service de sécurité de la Banque Armorine. Il "
                 "connaissait mon adresse et les quatre derniers chiffres de ma carte."),
                ("Combien de temps a duré l'appel ?",
                 "Plus d'une heure. Il me rassurait tout le temps, il disait que la police était au "
                 "courant."),
                ("Avez-vous reçu un SMS ou un courriel ?",
                 "Oui, un SMS avec un code que je lui ai lu, il disait que c'était pour valider la "
                 "sécurisation."),
                ("Quand avez-vous compris la supercherie ?",
                 "Le lendemain, en appelant moi-même mon agence. Ils n'avaient jamais entendu parler "
                 "de ce Thomas."),
                ("Avez-vous d'autres éléments à fournir ?",
                 "Je vous remets la capture de mon journal d'appels et mes relevés de compte."),
            ],
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À ÉTABLISSEMENT BANCAIRE",
            "Le 17/01/2033, nous, lieutenant Hugo LEBRETON, requérons la société Néovia Banque de "
            "nous communiquer l'identité du titulaire du compte FR76 3000 4000 1200 0012 3456 789 et "
            "ses relevés depuis son ouverture.",
            "Réponse le 19/01/2033 : compte ouvert en ligne le 02/12/2032 au nom de Lorène VIDAL, née "
            "le 11/04/2004, demeurant 8 rue des Ajoncs à Kerlan.",
        ),
        piece(
            "RELEVÉ DE COMPTE — NÉOVIA BANQUE",
            "Titulaire : Mme Lorène VIDAL. Compte FR76 3000 4000 1200 0012 3456 789. Période du "
            "02/12/2032 au 23/01/2033.",
            "02/12/2032 — ouverture du compte, versement initial de 10,00 euros.",
            "14/01/2033 — virement reçu de Mme Hélène ROUSSET : 12 800,00 euros.",
            "14/01/2033 — virement reçu de Mme Hélène ROUSSET : 12 800,00 euros.",
            "15/01/2033 — virement reçu de Mme Hélène ROUSSET : 11 300,00 euros.",
            "18/01/2033 — virement reçu de Mme Gaëlle PRIGENT : 6 500,00 euros.",
            "18/01/2033 — virement reçu de Mme Gaëlle PRIGENT : 6 250,00 euros.",
            "14/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "14/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "15/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "15/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "16/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "16/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "17/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "17/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "18/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "18/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "19/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "19/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "20/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "20/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "21/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "21/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,400 euros.".replace(",", " "),
            "22/01/2033 — retrait d'espèces au distributeur Néovia rue de Brest à Kerlan : 1 000,00 euros.",
            "22/01/2033 — achat de cartes prépayées Paysafe, point de vente tabac du Port : 2,200 euros.".replace(",", " "),
            "Solde au 23/01/2033 : 84,12 euros.",
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À ÉMETTEUR DE CARTES PRÉPAYÉES",
            "Le 25/01/2033, nous, lieutenant Hugo LEBRETON, requérons la société Paysafe de nous "
            "communiquer l'utilisation des codes des cartes prépayées achetées au tabac du Port à "
            "Kerlan entre le 14/01/2033 et le 22/01/2033.",
            "Réponse le 30/01/2033 : les codes ont été utilisés sur des sites de paris en ligne et "
            "sur une plateforme d'échange de cryptomonnaies, depuis une adresse IP attribuée à une "
            "box internet installée au 31 boulevard de la Mer à Kerlan.",
        ),
        piece(
            "RAPPORT D'ANALYSE DES COMPTES",
            "Le 24/01/2033, nous, brigadier Chloé MAUGER, procédons à l'analyse des relevés du compte "
            "de Lorène VIDAL communiqués par Néovia Banque.",
            "Crédits : 12 800 euros le 14/01/2033 à 10h52, 12 800 euros le 14/01/2033 à 11h18, "
            "11 300 euros le 15/01/2033 à 09h41, 6 500 euros et 6 250 euros le 18/01/2033, tous en "
            "provenance des comptes des deux victimes.",
            "Débits : retraits d'espèces de 1 000 euros, montant maximal autorisé, effectués "
            "quotidiennement du 14/01/2033 au 22/01/2033 dans des distributeurs de Kerlan, et "
            "achat de cartes prépayées pour 21 400 euros.",
            "Le solde du compte au 23/01/2033 est de 84,12 euros.",
        ),
        piece(
            "RÉQUISITION JUDICIAIRE À OPÉRATEUR DE TÉLÉPHONIE",
            "Le 18/01/2033, nous, lieutenant Hugo LEBRETON, requérons la société Nérée Mobile de nous "
            "communiquer l'identité du titulaire et les fadettes de la ligne 07 81 22 45 63.",
            "Réponse le 21/01/2033 : ligne prépayée activée le 05/01/2033 sans identité vérifiée. "
            "Appel sortant le 14/01/2033 à 10h47 vers la ligne fixe d'Hélène ROUSSET, d'une durée de "
            "52 minutes.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL D'ANALYSE TÉLÉPHONIQUE",
            "Le 27/01/2033, nous, brigadier Chloé MAUGER, procédons à l'analyse des fadettes de la "
            "ligne 07 81 22 45 63.",
            "La ligne a borné du 14/01/2033 au 18/01/2033 sur un relais couvrant le 31 boulevard de "
            "la Mer à Kerlan, domicile de Sofiane AMRANI.",
            "Le téléphone utilisé porte un numéro IMEI également associé, en décembre 2032, à une "
            "carte SIM ouverte au nom de Sofiane AMRANI.",
            "La ligne a échangé 41 appels avec la ligne 06 27 83 15 90, attribuée à Mehdi CARON.",
        ),
        piece(
            "PROCÈS-VERBAL D'EXPLOITATION DE VIDÉOPROTECTION BANCAIRE",
            "Le 28/01/2033, nous, brigadier Chloé MAUGER, exploitons les images des distributeurs de "
            "la Banque Néovia rue de Brest à Kerlan.",
            "Les retraits des 14/01/2033, 16/01/2033 et 19/01/2033 sont effectués par un homme "
            "portant une casquette grise et une doudoune noire, dont la morphologie correspond à "
            "celle de Mehdi CARON. Les retraits du 15/01/2033 sont effectués par une jeune femme "
            "aux cheveux longs.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 03/02/2033 à 10h20, nous, lieutenant Hugo LEBRETON, officier de police judiciaire, "
            "notifions à Sofiane AMRANI, né le 22/09/1999 à Kerlan, demeurant 31 boulevard de la Mer "
            "à Kerlan, son placement en garde à vue pour des faits d'escroquerie, mesure prenant "
            "effet à compter du 03/02/2033 à 09h45, heure de son appréhension.",
            "Interpellation effectuée le 03/02/2033 à 09h45, 31 boulevard de la Mer à Kerlan.",
        ),
        piece(
            "PROCÈS-VERBAL D'INTERPELLATION",
            "Le 03/02/2033 à 09h30, nous, brigadier Chloé MAUGER, agent de police judiciaire, "
            "procédons à l'interpellation de Sofiane AMRANI à la sortie de son domicile, 31 "
            "boulevard de la Mer à Kerlan.",
            "Un téléphone portable est trouvé dans la poche de son blouson.",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
            "Le 03/02/2033 à 10h25, nous notifions à Sofiane AMRANI ses droits attachés à la mesure "
            "de garde à vue : droit de faire prévenir un proche, droit d'être examiné par un "
            "médecin, droit d'être assisté par un avocat, droit de se taire.",
            "Sofiane AMRANI demande l'assistance de Maître Clément AUBRY, avocat au barreau de Kerlan.",
        ),
        piece(
            "PROCÈS-VERBAL DE PERQUISITION ET DE SAISIE",
            "Le 03/02/2033 de 10h40 à 11h50, perquisition au domicile de Sofiane AMRANI, 31 "
            "boulevard de la Mer à Kerlan, en présence de l'intéressé.",
            "Sont saisis : deux téléphones portables, dont un dont l'IMEI correspond à celui de la "
            "ligne 07 81 22 45 63, un carnet comportant des noms de personnes âgées et des numéros "
            "de téléphone, une somme de 6 200 euros en espèces et quatorze cartes prépayées.",
        ),
        piece(
            "PROCÈS-VERBAL D'ENTRETIEN AVEC L'AVOCAT",
            "Sofiane AMRANI, placé en garde à vue, a demandé l'assistance de son avocat.",
            "Demande d'entretien formulée le 03/02/2033 à 10h26.",
            "Entretien avec Maître Clément AUBRY, avocat choisi, réalisé le 03/02/2033 de 12h30 à 13h00.",
        ),
        audition(
            "Sofiane AMRANI", "MIS EN CAUSE", "03/02/2033", "13h15", "15h30", OPJ_ARMORINE,
            [
                ("Vous êtes assisté de Maître Clément AUBRY. Quelle est votre situation ?",
                 "Je suis vendeur dans une boutique de téléphonie, en contrat à durée déterminée."),
                ("Utilisez-vous la ligne 07 81 22 45 63 ?",
                 "Non. Ce téléphone, on me l'a prêté. Je ne sais pas qui avait la puce avant."),
                ("Le 14/01/2033 à 10h47, cette ligne a appelé Hélène ROUSSET pendant 52 minutes. "
                 "Étiez-vous l'appelant ?",
                 "Non, je ne connais pas cette dame."),
                ("Comment expliquez-vous le carnet de noms de personnes âgées saisi chez vous ?",
                 "Ce sont des clients de la boutique, pour des relances commerciales."),
                ("Et les 6 200 euros en espèces ?",
                 "C'est de l'argent gagné en revendant des téléphones d'occasion."),
                ("Connaissez-vous Lorène VIDAL ?",
                 "Non, ce nom ne me dit rien."),
                ("Connaissez-vous Mehdi CARON ?",
                 "C'est un ami. On joue au football ensemble."),
                ("Votre ligne a échangé 41 appels avec la sienne. Pourquoi ?",
                 "On s'organise pour le foot, on s'appelle souvent."),
                ("La ligne 07 81 22 45 63 a borné chez vous du 14/01/2033 au 18/01/2033. Comment "
                 "l'expliquez-vous ?",
                 "Le téléphone était chez moi, mais d'autres personnes viennent chez moi."),
                ("Quelles personnes ?",
                 "Des amis. Je ne veux pas donner de noms."),
                ("Les codes de cartes prépayées ont été utilisés depuis votre box internet. Qu'en "
                 "dites-vous ?",
                 "Je joue en ligne, comme tout le monde. Je ne sais pas d'où viennent ces cartes."),
                ("Avez-vous des dettes ?",
                 "Un peu, à cause des paris. Je rembourse petit à petit."),
                ("Que contiennent les quatorze cartes prépayées saisies chez vous ?",
                 "Je ne sais pas, je ne les ai pas encore utilisées."),
                ("Avez-vous déjà été condamné ?",
                 "Oui, en 2030, pour une histoire de vente sur internet. J'ai eu du sursis."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE PROLONGATION DE GARDE À VUE",
            "Le 04/02/2033 à 09h20, M. Yannick DROUET, vice-procureur de la République de Kerlan, "
            "autorise la prolongation de la garde à vue de Sofiane AMRANI pour une durée de "
            "vingt-quatre heures.",
        ),
        audition(
            "Sofiane AMRANI", "MIS EN CAUSE", "04/02/2033", "10h00", "11h20", OPJ_ARMORINE,
            [
                ("Vous êtes assisté de Maître Clément AUBRY. Mehdi CARON déclare que vous lui donniez "
                 "les cartes bancaires de Lorène VIDAL pour retirer l'argent. Qu'en dites-vous ?",
                 "D'accord. J'ai passé des appels, oui. Mais c'est quelqu'un d'autre qui me donnait les "
                 "listes de noms et qui prenait la plus grosse part."),
                ("Combien de personnes avez-vous appelées ?",
                 "Une dizaine. Seulement deux ont fait des virements."),
                ("Qui est cette autre personne ?",
                 "Je ne donnerai pas son nom, j'ai peur pour ma famille."),
                ("Combien avez-vous perçu ?",
                 "Environ 8 000 euros en tout."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 4 février 2033 à 18h30, il est mis fin à la garde à vue de Sofiane AMRANI, qui est "
            "conduit devant le juge d'instruction du tribunal judiciaire de Kerlan.",
        ),
        piece(
            "PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
            "Le 03/02/2033 à 11h00, nous, brigadier Chloé MAUGER, agent de police judiciaire, sous "
            "le contrôle du lieutenant Hugo LEBRETON, notifions à Mehdi CARON, né le 07/06/2001 à "
            "Kerlan, son placement en garde à vue pour des faits de blanchiment.",
            "Interpellation effectuée le 03/02/2033 à 10h50, sur son lieu de travail.",
        ),
        audition(
            "Mehdi CARON", "MIS EN CAUSE", "03/02/2033", "16h00", "17h45", APJ_ARMORINE,
            [
                ("Vous avez renoncé à l'assistance d'un avocat. Avez-vous effectué des retraits avec "
                 "la carte de Lorène VIDAL ?",
                 "Oui. Sofiane me donnait la carte et le code, je retirais 1 000 euros par jour et je "
                 "lui rendais tout. Il me donnait 50 euros à chaque fois."),
                ("Saviez-vous d'où venait l'argent ?",
                 "Il disait que c'était de l'argent de la revente de téléphones."),
                ("Qui est Lorène VIDAL ?",
                 "C'est la copine de mon cousin. Elle avait ouvert le compte pour Sofiane contre 300 euros."),
                ("Qui effectuait les retraits du 15/01/2033 ?",
                 "Lorène, ce jour-là je travaillais."),
                ("Qui passait les appels aux victimes ?",
                 "Sofiane. Je l'ai entendu une fois, il disait qu'il était de la banque."),
            ],
        ),
        piece(
            "PROCÈS-VERBAL DE FIN DE GARDE À VUE",
            "Le 03/02/2033 à 22h40, il est mis fin à la garde à vue de Mehdi CARON, qui est remis en "
            "liberté et convoqué devant le juge d'instruction.",
        ),
        audition(
            "Lorène VIDAL", "MIS EN CAUSE", "05/02/2033", "09h00", "10h30", APJ_ARMORINE,
            [
                ("Vous êtes entendue librement. Avez-vous ouvert un compte chez Néovia Banque le "
                 "02/12/2032 ?",
                 "Oui. Un ami de mon copain, Sofiane, m'a proposé 300 euros pour ouvrir un compte en "
                 "ligne et lui donner la carte."),
                ("Saviez-vous à quoi servirait ce compte ?",
                 "Il disait que c'était pour recevoir des paiements de clients à l'étranger. Je n'ai "
                 "pas posé de questions."),
                ("Avez-vous effectué des retraits le 15/01/2033 ?",
                 "Oui, une fois, Sofiane m'a demandé de retirer 1 000 euros. Je lui ai tout donné."),
                ("Vous a-t-il remis d'autres sommes ?",
                 "Seulement les 300 euros du début."),
            ],
            libre=True,
        ),
        piece(
            "CORRESPONDANCE SAISIE — MESSAGES SMS",
            "Extraction du téléphone saisi chez Sofiane AMRANI, placé sous scellé numéro 1.",
            "Message du 14/01/2033 à 11h25 envoyé au 06 27 83 15 90 : « La vieille a envoyé deux "
            "fois. Retire demain matin. »",
            "Message du 15/01/2033 à 09h50 envoyé au 06 27 83 15 90 : « Troisième passé. Lorène "
            "s'occupe du retrait aujourd'hui. »",
            "Message du 18/01/2033 à 16h12 reçu d'un numéro masqué : « La liste de la semaine "
            "arrive. Garde ta part, envoie le reste par carte prépayée. »",
            scan=True,
        ),
        piece(
            "PROCÈS-VERBAL DE TRANSCRIPTION DU CARNET SAISI",
            "Le 04/02/2033, nous, brigadier Chloé MAUGER, transcrivons le carnet placé sous scellé "
            "numéro 3, saisi au domicile de Sofiane AMRANI.",
            "Le carnet comporte 38 lignes, chacune avec un nom, un prénom, un numéro de téléphone fixe "
            "et parfois une année de naissance, toutes antérieures à 1950.",
            "La ligne 12 porte : « ROUSSET Hélène — 1949 — Armorine — OK 3 vir. ». La ligne 27 porte : "
            "« PRIGENT Gaëlle — Armorine — OK 2 ».",
            "Huit autres lignes portent la mention « rappeler », trois la mention « méfiante ».",
            "Aucune des personnes mentionnées n'est cliente de la boutique de téléphonie où travaille "
            "Sofiane AMRANI, d'après le registre fourni par son employeur.",
        ),
        piece(
            "PROCÈS-VERBAL DE CONFRONTATION",
            "Le 04/02/2033 de 14h00 à 14h50, nous, lieutenant Hugo LEBRETON, procédons à la "
            "confrontation de Sofiane AMRANI, assisté de Maître Clément AUBRY, et de Mehdi CARON.",
            "Question à Mehdi CARON : Maintenez-vous que Sofiane AMRANI vous remettait la carte ?",
            "Réponse de Mehdi CARON : Oui. Il me donnait la carte et le code, et je lui rendais l'argent "
            "le soir même.",
            "Question à Sofiane AMRANI : Qu'avez-vous à répondre ?",
            "Réponse de Sofiane AMRANI : C'est vrai pour la carte. Mais Mehdi savait d'où venait "
            "l'argent, il a lu mes messages.",
            "Question à Mehdi CARON : Saviez-vous que l'argent provenait de personnes âgées ?",
            "Réponse de Mehdi CARON : Non, je ne l'ai compris qu'en garde à vue.",
            "Chacun maintient ses déclarations.",
        ),
        piece(
            "PROCÈS-VERBAL D'INTERROGATOIRE DE PREMIÈRE COMPARUTION",
            "Le 05/02/2033 à 14h00, devant nous, Mme Bérénice FAURE, juge d'instruction au tribunal "
            "judiciaire de Kerlan, assistée de Mme Laure PICHON, greffière, comparaît Sofiane AMRANI, "
            "assisté de Maître Clément AUBRY.",
            "Nous lui faisons connaître les faits dont nous sommes saisie et l'informons qu'il peut "
            "se taire, faire des déclarations ou être interrogé.",
            "Sofiane AMRANI déclare : « Je reconnais avoir passé les appels à Mme ROUSSET et à Mme "
            "PRIGENT. Je regrette. Je n'étais pas le chef. »",
            "Nous mettons en examen Sofiane AMRANI des chefs d'escroquerie au préjudice de "
            "personnes vulnérables et de blanchiment, et le plaçons sous contrôle judiciaire.",
        ),
        piece(
            "ORDONNANCE DE PLACEMENT SOUS CONTRÔLE JUDICIAIRE",
            "Le 05/02/2033, Mme Bérénice FAURE, juge d'instruction, ordonne le placement sous "
            "contrôle judiciaire de Sofiane AMRANI, avec les obligations suivantes : se présenter "
            "chaque semaine au commissariat de Kerlan, ne pas entrer en contact avec Hélène ROUSSET, "
            "Gaëlle PRIGENT, Mehdi CARON et Lorène VIDAL, verser un cautionnement de 5 000 euros.",
        ),
        piece(
            "PROCÈS-VERBAL D'AUDITION DE PARTIE CIVILE — CONSTITUTION",
            "Le 06/02/2033, Mme Gaëlle PRIGENT déclare se constituer partie civile et sollicite le "
            "remboursement de 12 750 euros ainsi que 2 000 euros au titre du préjudice moral.",
            "Le 06/02/2033, Mme Hélène ROUSSET, représentée par sa fille Sandrine ROUSSET, déclare se "
            "constituer partie civile et sollicite le remboursement de 38 400 euros.",
        ),
        piece(
            "ENQUÊTE DE PERSONNALITÉ — SOFIANE AMRANI",
            "Né le 22/09/1999 à Kerlan, célibataire, vit seul au 31 boulevard de la Mer. Titulaire "
            "d'un BTS commercial, vendeur en téléphonie depuis 2022.",
            "Il déclare des dettes de jeux en ligne d'environ 15 000 euros.",
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "AMRANI Sofiane, né le 22/09/1999 à Kerlan.",
            "Condamnation du 03/05/2030, tribunal correctionnel de Kerlan : escroquerie, 8 mois "
            "d'emprisonnement avec sursis.",
            scan=True,
        ),
        piece(
            "EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
            "CARON Mehdi, né le 07/06/2001 à Kerlan.",
            "Néant.",
        ),
    ]


def generer_armorine(dossier: Path) -> VeriteDossier:
    chemin = dossier / "dossier_armorine.pdf"
    nb, scans = ecrire_dossier(_pieces_armorine(), chemin, PARQUET_ARMORINE)
    return VeriteDossier(
        chemin_pdf=chemin, nb_pages=nb, pages_scan=scans,
        contradictions_par_regles=(
            ("Interpellation de Sofiane AMRANI", "09h30", "09h45"),
            ("Téléphone", "07 81 22 45 63", "07 81 22 54 63"),
        ),
        contradictions_modele=(
            ("38 400", "36 900", "prejudice", "montant"),
        ),
        durees_gav={
            "Sofiane AMRANI": minutes_entre("03/02/2033 09h45", "04/02/2033 18h30"),
            "Mehdi CARON": minutes_entre("03/02/2033 11h00", "03/02/2033 22h40"),
        },
        mis_en_cause=("Sofiane AMRANI", "Mehdi CARON", "Lorène VIDAL"),
        jamais_mis_en_cause=(
            "Hugo LEBRETON", "Chloé MAUGER", "Yannick DROUET", "Bérénice FAURE", "Laure PICHON",
            "Clément AUBRY", "Hélène ROUSSET", "Gaëlle PRIGENT", "Agnès LE ROUX", "Yves KERGOAT",
            "Sandrine ROUSSET",
        ),
        questions=(
            QuestionAttendue("À quelle heure Sofiane AMRANI a-t-il été interpellé ?", heures=((9, 30), (9, 45))),
            QuestionAttendue("Quel montant Hélène ROUSSET a-t-elle perdu ?", mots=("38 400", "36 900")),
            QuestionAttendue("Qui effectuait les retraits d'espèces ?", mots=("CARON", "VIDAL")),
        ),
    )


DOSSIERS_COMPLEXES = (generer_ressac, generer_pasteur, generer_armorine)


if __name__ == "__main__":
    import sys

    sortie = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/dossiers_complexes")
    for generer in DOSSIERS_COMPLEXES:
        v = generer(sortie)
        print(f"{v.chemin_pdf} : {v.nb_pages} pages, numérisées : {v.pages_scan}")
