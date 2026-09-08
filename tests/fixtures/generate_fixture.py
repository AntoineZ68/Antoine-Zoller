"""Générateur du jeu d'essai : un faux dossier de procédure pénale.

Tout est inventé : noms, lieux, dates (2031), numéros de procédure. Le but
est de fournir une entrée réaliste pour tester le pipeline sans jamais
utiliser de données réelles couvertes par le secret professionnel.

Le dossier généré contient :
- 15 pièces sur 18 pages, dans un ordre volontairement non chronologique
  (le PV de synthèse, rédigé en dernier, est placé en première page).
- 2 personnes principales (1 mis en cause, 1 victime) + intervenants.
- 3 auditions : 2 du mis en cause (qui divergent sur l'heure d'arrivée sur
  les lieux) et 1 de la victime.
- Des horaires de garde à vue cohérents entre eux, pour vérifier le calcul
  des durées.
- 3 pages rendues comme des images sans couche texte, pour forcer le
  déclenchement de l'OCR à l'ingestion.

`generer_dossier_fictif()` renvoie un objet `VeriteTerrain` : les valeurs
attendues, utilisées par les tests pour vérifier que le pipeline retrouve
bien ce qui a été injecté, sans plus ni moins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

LARGEUR_PAGE, HAUTEUR_PAGE = A4
COTE_PROCEDURE = "N° PARQUET 2031/00458"
POLICE_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Citation exacte extraite de chaque audition du mis en cause sur le même
# point factuel (heure d'arrivée sur les lieux) : c'est la divergence que
# l'étape `decl` doit détecter et présenter côte à côte, sans la qualifier.
CITATION_AUDITION_1 = (
    "Je suis arrivé sur les lieux du hangar de la rue des Tanneurs vers 22 heures."
)
CITATION_AUDITION_2 = (
    "Je suis arrivé sur les lieux vers 23 heures 30, pas avant, "
    "j'ai dû me tromper la dernière fois."
)


@dataclass
class VeriteTerrain:
    """Valeurs de référence injectées dans le faux dossier, pour les tests."""

    nb_pages: int
    pages_scan: list[int]  # pages rendues en image (sans couche texte)
    cote_procedure: str
    cotes_par_page: dict[int, str]
    date_placement_gav: str
    heure_placement_gav: str
    date_fin_gav: str
    heure_fin_gav: str
    duree_gav_heures: float
    date_notification_droits: str
    heure_notification_droits: str
    delai_placement_notification_minutes: float
    date_demande_medecin: str
    heure_demande_medecin: str
    date_realisation_medecin: str
    heure_realisation_medecin: str
    delai_demande_realisation_medecin_minutes: float
    mis_en_cause: str
    victime: str
    citation_audition_1: str
    page_audition_1: int
    citation_audition_2: str
    page_audition_2: int
    point_factuel_divergent: str


def _police(taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(POLICE_PATH, taille)


def _dessiner_page_texte(c: pdfcanvas.Canvas, lignes: list[str], cote: str, numero: int) -> None:
    c.setFont("Helvetica", 10)
    y = HAUTEUR_PAGE - 25 * mm
    for ligne in lignes:
        gras = ligne.startswith("##")
        texte = ligne[2:].strip() if gras else ligne
        c.setFont("Helvetica-Bold" if gras else "Helvetica", 11 if gras else 10)
        for sous_ligne in _decouper_ligne(texte, 95):
            c.drawString(20 * mm, y, sous_ligne)
            y -= 6 * mm
        y -= 2 * mm
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 12 * mm, f"{COTE_PROCEDURE} — Cote {cote} — Page {numero}")
    c.showPage()


def _decouper_ligne(texte: str, largeur_max: int) -> list[str]:
    mots = texte.split(" ")
    lignes: list[str] = []
    courante = ""
    for mot in mots:
        if len(courante) + len(mot) + 1 > largeur_max:
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    if courante:
        lignes.append(courante)
    return lignes or [""]


def _rendre_image_page(lignes: list[str], cote: str, numero: int) -> Image.Image:
    """Simule une page scannée : le texte est dessiné dans une image, sans
    aucune couche de texte PDF. L'ingestion doit détecter moins de 50
    caractères de texte natif sur cette page et déclencher l'OCR."""
    largeur_px, hauteur_px = 1654, 2339  # A4 à 200 DPI environ
    img = Image.new("RGB", (largeur_px, hauteur_px), "white")
    dessin = ImageDraw.Draw(img)
    y = 150
    for ligne in lignes:
        gras = ligne.startswith("##")
        texte = ligne[2:].strip() if gras else ligne
        police = _police(30 if gras else 26)
        for sous_ligne in _decouper_ligne(texte, 70):
            dessin.text((120, y), sous_ligne, fill="black", font=police)
            y += 42
        y += 14
    dessin.text(
        (120, hauteur_px - 100),
        f"{COTE_PROCEDURE} — Cote {cote} — Page {numero}",
        fill="black",
        font=_police(20),
    )
    return img


def _page_image_vers_pdf(img: Image.Image) -> bytes:
    tampon_image = BytesIO()
    img.save(tampon_image, format="PNG")
    tampon_image.seek(0)

    tampon_pdf = BytesIO()
    c = pdfcanvas.Canvas(tampon_pdf, pagesize=A4)
    c.drawImage(
        ImageReaderCompat(tampon_image),
        0,
        0,
        width=LARGEUR_PAGE,
        height=HAUTEUR_PAGE,
    )
    c.showPage()
    c.save()
    tampon_pdf.seek(0)
    return tampon_pdf.read()


def ImageReaderCompat(tampon):
    from reportlab.lib.utils import ImageReader

    return ImageReader(tampon)


def _pieces() -> list[dict]:
    """Définit les 15 pièces / 18 pages du faux dossier, dans l'ordre
    physique (non chronologique) où elles apparaissent dans le PDF."""
    return [
        {
            "type": "PV de synthèse",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE SYNTHÈSE",
                    "Brigade de gendarmerie de Vaucressin.",
                    "Le 16/03/2031, la capitaine Élodie BASTIER, officier de police "
                    "judiciaire, dresse la synthèse de l'enquête ouverte le 14/03/2031 "
                    "à la suite des faits dénoncés par Camille ARZANO.",
                    "Julien MORVANNEC a été placé en garde à vue le 14/03/2031 à "
                    "08h15 puis remis en liberté le 15/03/2031 à 20h15.",
                    "Les investigations se poursuivent sur commission rogatoire.",
                ]
            ],
        },
        {
            "type": "Soit-transmis",
            "scan": False,
            "pages": [
                [
                    "## SOIT-TRANSMIS",
                    "Le 14/03/2031, le Procureur de la République de Vaucressin, "
                    "M. Antoine ROQUIER, transmet à la brigade de gendarmerie de "
                    "Vaucressin la plainte déposée par Camille ARZANO pour ouverture "
                    "d'enquête.",
                ]
            ],
        },
        {
            "type": "PV de notification de placement en garde à vue",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE NOTIFICATION DE PLACEMENT EN GARDE À VUE",
                    "Le 14/03/2031 à 08h15, nous, capitaine Élodie BASTIER, officier "
                    "de police judiciaire à la brigade de Vaucressin, notifions à "
                    "Julien MORVANNEC, né le 03/07/1998 à Lourville, demeurant 14 rue "
                    "des Tanneurs à Vaucressin, son placement en garde à vue.",
                    "Interpellation effectuée le 14/03/2031 à 07h50, 14 rue des "
                    "Tanneurs à Vaucressin.",
                ]
            ],
        },
        {
            "type": "PV de notification des droits",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE NOTIFICATION DES DROITS",
                    "Le 14/03/2031 à 08h20, nous notifions à Julien MORVANNEC ses "
                    "droits attachés à la mesure de garde à vue : droit de faire "
                    "prévenir un proche, droit à l'examen par un médecin, droit à "
                    "l'assistance d'un avocat.",
                    "Julien MORVANNEC sollicite l'assistance d'un avocat et un "
                    "examen médical.",
                ]
            ],
        },
        {
            "type": "PV de perquisition et saisie",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE PERQUISITION ET DE SAISIE",
                    "Le 14/03/2031 de 09h00 à 09h40, perquisition au domicile de "
                    "Julien MORVANNEC, 14 rue des Tanneurs à Vaucressin, en présence "
                    "de l'intéressé.",
                    "Sont saisis : un téléphone portable de marque Corvex, un "
                    "couteau de cuisine, un carnet manuscrit.",
                ]
            ],
        },
        {
            "type": "Certificat médical",
            "scan": True,
            "pages": [
                [
                    "## CERTIFICAT MÉDICAL",
                    "Je soussigné, Docteur Hugo SALINIER, certifie avoir examiné ce "
                    "jour 14/03/2031 à 09h45, sur réquisition reçue à 08h30, Julien "
                    "MORVANNEC dans le cadre de sa garde à vue.",
                    "Son état de santé est compatible avec la mesure de garde à vue.",
                ]
            ],
        },
        {
            "type": "PV d'audition libre",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL D'AUDITION DE JULIEN MORVANNEC (MIS EN CAUSE)",
                    "Le 14/03/2031, audition débutée à 10h00.",
                    "Question : Où étiez-vous le soir des faits ?",
                    f"Réponse : {CITATION_AUDITION_1}",
                    "Question : Que s'est-il passé ensuite ?",
                    "Réponse : J'ai discuté avec Camille pendant une vingtaine de "
                    "minutes puis je suis reparti à pied.",
                ],
                [
                    "Question : Connaissiez-vous Camille ARZANO auparavant ?",
                    "Réponse : Oui, nous nous connaissons depuis environ deux ans.",
                    "Audition close à 11h15.",
                ],
            ],
        },
        {
            "type": "PV d'entretien avocat",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL D'ENTRETIEN AVEC L'AVOCAT",
                    "Demande d'entretien formulée le 14/03/2031 à 08h25.",
                    "Entretien avec Maître Nadia FERRAND, avocate commise d'office, "
                    "réalisé le 14/03/2031 de 09h00 à 09h20.",
                ]
            ],
        },
        {
            "type": "PV d'audition",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL D'AUDITION DE CAMILLE ARZANO (VICTIME)",
                    "Le 14/03/2031, audition débutée à 14h00, close à 14h30.",
                    "Question : Que s'est-il passé le soir des faits ?",
                    "Réponse : J'ai vu Julien arriver vers 22 heures 30 devant le "
                    "hangar de la rue des Tanneurs. Nous avons discuté puis la "
                    "situation s'est envenimée.",
                ]
            ],
        },
        {
            "type": "PV de prolongation de garde à vue",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE PROLONGATION DE GARDE À VUE",
                    "Le 15/03/2031 à 08h10, M. Antoine ROQUIER, Procureur de la "
                    "République de Vaucressin, autorise la prolongation de la garde "
                    "à vue de Julien MORVANNEC pour une durée de vingt-quatre heures.",
                ]
            ],
        },
        {
            "type": "PV d'audition libre",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL D'AUDITION DE JULIEN MORVANNEC (MIS EN CAUSE)",
                    "Le 15/03/2031, audition débutée à 14h00.",
                    "Question : Vous confirmez vos précédentes déclarations sur "
                    "l'heure de votre arrivée ?",
                    f"Réponse : {CITATION_AUDITION_2}",
                ],
                [
                    "Question : Pourquoi cette différence avec votre première "
                    "audition ?",
                    "Réponse : J'ai réfléchi depuis et je pense m'être trompé.",
                    "Audition close à 14h45.",
                ],
            ],
        },
        {
            "type": "PV de fin de garde à vue",
            "scan": False,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE FIN DE GARDE À VUE",
                    "Le 15/03/2031 à 20h15, il est mis fin à la garde à vue de "
                    "Julien MORVANNEC, qui est remis en liberté.",
                ]
            ],
        },
        {
            "type": "Casier judiciaire",
            "scan": True,
            "pages": [
                [
                    "## EXTRAIT DE CASIER JUDICIAIRE — BULLETIN N°1",
                    "MORVANNEC Julien, né le 03/07/1998 à Lourville.",
                    "Néant.",
                ]
            ],
        },
        {
            "type": "Enquête de personnalité",
            "scan": False,
            "pages": [
                [
                    "## ENQUÊTE DE PERSONNALITÉ — JULIEN MORVANNEC",
                    "Situation familiale : célibataire, sans enfant, vit seul 14 rue "
                    "des Tanneurs à Vaucressin.",
                    "Situation professionnelle : sans emploi depuis six mois, "
                    "précédemment magasinier chez Corvex Logistique.",
                ],
                [
                    "Antécédents : néant au dossier.",
                    "Entourage : entretien avec la mère de l'intéressé, Mme Sylvie "
                    "MORVANNEC, qui le décrit comme quelqu'un de calme.",
                ],
            ],
        },
        {
            "type": "PV de constatations",
            "scan": True,
            "pages": [
                [
                    "## PROCÈS-VERBAL DE CONSTATATIONS",
                    "Le 14/03/2031, constatations et prises de vues photographiques "
                    "réalisées au hangar de la rue des Tanneurs à Vaucressin.",
                ]
            ],
        },
    ]


def generer_dossier_fictif(dossier_sortie: Path) -> VeriteTerrain:
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    chemin_pdf = dossier_sortie / "dossier_fictif.pdf"

    pieces = _pieces()
    ecrivain = PdfWriter()
    numero_global = 0
    pages_scan: list[int] = []
    cotes_par_page: dict[int, str] = {}
    page_audition_1 = 0
    page_audition_2 = 0

    for piece in pieces:
        for lignes in piece["pages"]:
            numero_global += 1
            cote = f"D{numero_global:04d}"
            cotes_par_page[numero_global] = cote

            if piece["type"] == "PV d'audition libre" and CITATION_AUDITION_1 in " ".join(lignes):
                page_audition_1 = numero_global
            if piece["type"] == "PV d'audition libre" and CITATION_AUDITION_2 in " ".join(lignes):
                page_audition_2 = numero_global

            if piece["scan"]:
                pages_scan.append(numero_global)
                img = _rendre_image_page(lignes, cote, numero_global)
                pdf_bytes = _page_image_vers_pdf(img)
                lecteur = PdfReader(BytesIO(pdf_bytes))
                ecrivain.add_page(lecteur.pages[0])
            else:
                tampon = BytesIO()
                c = pdfcanvas.Canvas(tampon, pagesize=A4)
                _dessiner_page_texte(c, lignes, cote, numero_global)
                c.save()
                tampon.seek(0)
                lecteur = PdfReader(tampon)
                ecrivain.add_page(lecteur.pages[0])

    with chemin_pdf.open("wb") as f:
        ecrivain.write(f)

    return VeriteTerrain(
        nb_pages=numero_global,
        pages_scan=pages_scan,
        cote_procedure=COTE_PROCEDURE,
        cotes_par_page=cotes_par_page,
        date_placement_gav="14/03/2031",
        heure_placement_gav="08h15",
        date_fin_gav="15/03/2031",
        heure_fin_gav="20h15",
        duree_gav_heures=36.0,
        date_notification_droits="14/03/2031",
        heure_notification_droits="08h20",
        delai_placement_notification_minutes=5.0,
        date_demande_medecin="14/03/2031",
        heure_demande_medecin="08h30",
        date_realisation_medecin="14/03/2031",
        heure_realisation_medecin="09h45",
        delai_demande_realisation_medecin_minutes=75.0,
        mis_en_cause="Julien MORVANNEC",
        victime="Camille ARZANO",
        citation_audition_1=CITATION_AUDITION_1,
        page_audition_1=page_audition_1,
        citation_audition_2=CITATION_AUDITION_2,
        page_audition_2=page_audition_2,
        point_factuel_divergent="heure d'arrivée sur les lieux",
    )


if __name__ == "__main__":
    import sys

    from rich.console import Console

    console = Console()
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/output")
    verite = generer_dossier_fictif(dest)
    console.print(f"Dossier fictif généré : {dest / 'dossier_fictif.pdf'}")
    console.print(f"  Pages totales : {verite.nb_pages}")
    console.print(f"  Pages scannées (sans couche texte) : {verite.pages_scan}")
    console.print(f"  Durée GAV attendue : {verite.duree_gav_heures} h")
    console.print(f"  Page audition n°1 (citation à vérifier) : {verite.page_audition_1}")
    console.print(f"  Page audition n°2 (citation à vérifier) : {verite.page_audition_2}")
