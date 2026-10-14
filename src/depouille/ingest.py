"""Étape 1 — Ingestion : découpage en pages, extraction de texte, OCR si
nécessaire, détection de cote.

Une page passe en OCR si son texte natif fait moins de 50 caractères. Le
texte final stocké est toujours celui effectivement lu sur la page (natif
ou OCR) — jamais une estimation.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import sqlite3
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pdfplumber
import pymupdf
from rich.console import Console
from rich.table import Table

from .regex_patterns import detecter_cote

SEUIL_OCR_CARACTERES = 50

# Une page scannée peut porter un texte natif plus long qu'une simple cote :
# mention de copie certifiée, pagination et juridiction ajoutées par le
# logiciel du greffe (« Copie certifiée conforme — Tribunal judiciaire de
# Lyon — page 12/2000 » dépasse 50 caractères). Une page couverte aux
# trois cinquièmes au moins par une image, et dont le texte natif reste
# sous ce second seuil, est traitée comme scannée.
SEUIL_OCR_PAGE_IMAGE = 300
COUVERTURE_IMAGE_SCAN = 0.6

RE_ESPACES_MULTIPLES = re.compile(r"[ \t]{2,}")


def _texte_page(page: pdfplumber.page.Page) -> str:
    """Extrait le texte d'une page en respectant la position horizontale des
    fragments (layout=True) plutôt que le seul ordre de lecture par défaut de
    pdfplumber, qui suppose un texte à une colonne. Sur des en-têtes à deux
    colonnes (ex. "N° Procédure : ... / Date : ... / Officier : ...") ou en
    présence d'un élément décoratif (logo, tampon) mal positionné dans le
    flux du PDF, l'ordre par défaut recolle des fragments sans rapport les
    uns aux autres sur une même ligne, ou les intercale au milieu d'une
    phrase — cassant aussi bien la détection du titre de la pièce que la
    citation exacte des faits. layout=True introduit en échange de larges
    espaces destinés à préserver l'alignement visuel des colonnes ; on les
    réduit à une espace simple, qui ne change rien à la position relative
    des mots dans une phrase normale."""
    brut = page.extract_text(layout=True) or ""
    lignes = [RE_ESPACES_MULTIPLES.sub(" ", ligne.strip()) for ligne in brut.splitlines()]
    return "\n".join(lignes).strip("\n")


# Pages lues par ouverture du fichier. pdfminer (sous pdfplumber) garde en
# cache, tant que le fichier reste ouvert, chaque objet déjà lu — images des
# pages scannées comprises. Mesuré sur 2 000 pages scannées lues d'une
# traite : 1,4 Go de mémoire, soit trois fois un serveur à 512 Mo.
# Rouvrir le fichier toutes les 100 pages borne ce cache.
PAGES_PAR_OUVERTURE = 100


def _fusionner(texte_natif: str, texte_ocr: str) -> str:
    """Page reconnue : le peu de texte natif qu'elle portait (moins de 50
    caractères, typiquement la cote « D12 » apposée numériquement, que le
    rendu pour l'OCR retire — voir _rendre_page) précède le texte reconnu."""
    natif = texte_natif.strip()
    return f"{natif}\n{texte_ocr}" if natif else texte_ocr


def _extraire_textes_natifs(chemin_pdf: Path, numeros: set[int] | None = None) -> list[str]:
    """Libère chaque page après l'avoir lue, et rouvre le fichier par blocs
    (voir PAGES_PAR_OUVERTURE) : sans quoi la mémoire croît avec la taille
    du dossier jusqu'à ce que le noyau tue le run — et un run tué ne rend
    aucun livrable. `numeros` : ne relire que ces pages (les autres restent
    vides)."""
    with pymupdf.open(chemin_pdf) as doc:
        nb_pages = doc.page_count
    textes = [""] * nb_pages
    a_lire = [n for n in range(1, nb_pages + 1) if numeros is None or n in numeros]
    for i in range(0, len(a_lire), PAGES_PAR_OUVERTURE):
        bloc = a_lire[i : i + PAGES_PAR_OUVERTURE]
        with pdfplumber.open(chemin_pdf, pages=bloc) as pdf:
            for page in pdf.pages:
                textes[page.page_number - 1] = _texte_page(page)
                page.close()
    return textes


def _empreinte(texte: str, secours: bytes = b"") -> str:
    contenu = texte.strip().encode("utf-8") if texte.strip() else secours
    return hashlib.sha256(contenu).hexdigest()


def _longueur_contenu(texte: str) -> int:
    """Longueur du texte hors tout espacement — l'extraction en layout=True
    (voir _texte_page) reproduit fidèlement les espaces blancs visuels d'une
    page sous forme de lignes vides, qui gonfleraient artificiellement la
    longueur mesurée sans qu'il y ait le moindre caractère de contenu réel
    en plus. Le seuil de déclenchement de l'OCR doit rester une mesure de
    contenu, pas de mise en page."""
    return len("".join(texte.split()))


# Chaque page en cours de reconnaissance est décompressée en bitmap plein
# format : c'est le poste de dépense mémoire du pipeline, et il est
# proportionnel au nombre de pages traitées EN PARALLÈLE. Mesuré sur un
# dossier de 47 pages dont 5 scannées : 338 Mo de pic à 4 pages en
# parallèle, 166 Mo à une seule. Sur un conteneur à 512 Mo, un dossier
# entièrement scanné de 60 pages y laisserait le processus tué par le
# noyau — et un run tué ne rend rien du tout, alors que 0,3 s de plus par
# page ne coûte qu'une poignée de secondes sur un dossier entier.
MEMOIRE_BASE_MO = 250
# Un processus Tesseract sur une page A4 rendue à 300 dpi : 69 Mo de pic
# mesuré, plus les images en attente (deux par travailleur, ≈ 3 Mo chacune)
# et une marge.
MEMOIRE_PAR_TRAVAILLEUR_MO = 150


CHEMINS_LIMITE_CGROUP = (
    "/sys/fs/cgroup/memory.max",  # cgroup v2
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
)


def _memoire_disponible_mo(chemins: tuple[str, ...] = CHEMINS_LIMITE_CGROUP) -> int:
    """Limite mémoire réellement applicable au processus : celle du conteneur
    (cgroup) si elle existe, sinon la mémoire physique de la machine. Sur
    Render, la machine hôte est grande et le conteneur petit — lire la
    seconde donnerait une réponse fausse et dangereuse."""
    for chemin in chemins:
        try:
            valeur = Path(chemin).read_text().strip()
        except OSError:
            continue
        if valeur == "max":
            break
        try:
            octets = int(valeur)
        except ValueError:
            continue
        # Une limite absurdement grande signifie « pas de limite » : les
        # cgroups non plafonnés portent la valeur maximale d'un entier 64 bits.
        if 0 < octets < (1 << 60):
            return octets // (1024 * 1024)
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) // (1024 * 1024)
    except (ValueError, OSError):
        return MEMOIRE_BASE_MO + MEMOIRE_PAR_TRAVAILLEUR_MO


CHEMINS_QUOTA_CPU = (
    ("/sys/fs/cgroup/cpu.max", None),  # cgroup v2 : « quota période » ou « max période »
    ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us"),  # cgroup v1
)


def _coeurs_disponibles(chemins: tuple = CHEMINS_QUOTA_CPU) -> int:
    """Cœurs réellement accordés au processus. Même piège que pour la
    mémoire : os.cpu_count() compte les cœurs de la machine hôte (souvent
    16 ou plus), pas le quota du conteneur (1 ou 2 sur Render). Lancer
    16 Tesseract sur un seul cœur ne va pas plus vite, mais multiplie la
    mémoire consommée par 16."""
    for chemin_quota, chemin_periode in chemins:
        try:
            morceaux = Path(chemin_quota).read_text().split()
            if chemin_periode is not None:
                morceaux = [morceaux[0], Path(chemin_periode).read_text().strip()]
            quota, periode = int(morceaux[0]), int(morceaux[1])
        except (OSError, ValueError, IndexError):
            continue
        # « max » (v2) lève ValueError ci-dessus ; -1 (v1) signifie sans quota.
        if quota > 0 and periode > 0:
            return max(1, math.ceil(quota / periode))
    return os.cpu_count() or 1


def _travailleurs_ocr() -> int:
    budget = _memoire_disponible_mo() - MEMOIRE_BASE_MO
    par_memoire = budget // MEMOIRE_PAR_TRAVAILLEUR_MO
    return max(1, min(par_memoire, _coeurs_disponibles()))


# Résolution de rendu des pages numérisées avant reconnaissance : celle
# pour laquelle le modèle français de Tesseract est entraîné. En dessous,
# les petits caractères des en-têtes de PV (numéros, heures) se dégradent.
DPI_OCR = 300

# Délai accordé à Tesseract par page avant abandon. Sur un serveur à
# fraction de cœur, une page dense peut dépasser plusieurs minutes ; une
# page abandonnée ressort sans texte (signalée, jamais silencieuse).
DELAI_TESSERACT_PAR_PAGE_S = 900


def _tesseract(png: bytes) -> tuple[bytes, str]:
    """Reconnaît une page rendue en image, en un seul passage, et renvoie :
    - son calque de texte invisible, PDF d'une page SANS image
      (textonly_pdf), chaque mot placé à l'endroit exact du scan — pour le
      surlignage ;
    - son texte dans l'ordre de lecture établi par Tesseract — pour
      l'analyse. Relu depuis le calque, un scan penché de 2° voyait deux
      lignes voisines entremêlées (« L R ' E a N n AR d D eu… ») : texte
      de la page perdu (observé, dossier d'essai).

    Un seul fil par appel (OMP_THREAD_LIMIT=1) : le parallélisme se fait
    entre pages, bien plus efficace que le multi-fil interne de Tesseract
    sur une page isolée."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ocr_") as dossier:
        base = Path(dossier) / "page"
        subprocess.run(
            ["tesseract", "stdin", str(base), "-l", "fra", "--dpi", str(DPI_OCR), "-c", "textonly_pdf=1", "pdf", "txt"],
            input=png,
            capture_output=True,
            timeout=DELAI_TESSERACT_PAR_PAGE_S,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            check=True,
        )
        chemin_pdf, chemin_txt = base.with_suffix(".pdf"), base.with_suffix(".txt")
        calque = chemin_pdf.read_bytes() if chemin_pdf.exists() else b""
        if not calque.startswith(b"%PDF"):
            raise subprocess.SubprocessError("Tesseract n'a pas produit de calque de texte.")
        texte = chemin_txt.read_text(encoding="utf-8", errors="replace") if chemin_txt.exists() else ""
    return calque, texte


# Confiance minimale de la détection d'orientation de Tesseract (OSD) pour
# retourner une page : en dessous, une page presque vide ou très dégradée
# serait tournée au hasard.
CONFIANCE_ORIENTATION_MIN = 2.0


def _orientation(png: bytes) -> int:
    """Degrés, dans le sens horaire, dont tourner l'image pour que le texte
    soit droit : 0, 90, 180 ou 270. Une page numérisée couchée, sans
    indication de rotation dans le PDF, donnait un OCR inutilisable
    (observé : témoignage d'une riveraine entièrement perdu). Toute erreur
    de détection (page sans texte, Tesseract en échec) vaut 0 : la page est
    reconnue telle quelle, comme avant."""
    try:
        resultat = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "0", "-l", "osd"],
            input=png,
            capture_output=True,
            timeout=DELAI_TESSERACT_PAR_PAGE_S,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except (subprocess.SubprocessError, OSError):
        return 0
    sortie = resultat.stdout.decode(errors="replace")
    rotation = re.search(r"Rotate:\s*(\d+)", sortie)
    confiance = re.search(r"Orientation confidence:\s*([\d.]+)", sortie)
    if not rotation or not confiance or float(confiance.group(1)) < CONFIANCE_ORIENTATION_MIN:
        return 0
    return int(rotation.group(1)) % 360


# Biais en deçà duquel une page n'est pas redressée (degrés) : Tesseract
# l'absorbe seul. Au-delà, sur un scan penché de 1,8°, il entremêlait la fin
# d'une ligne et le début de la suivante (« donnant sur pesée. la ruelle »).
BIAIS_MIN_DEGRES = 0.5
BIAIS_MAX_DEGRES = 6


def _biais(image) -> float:
    """Angle (degrés, sens trigonométrique) dont tourner l'image pour que
    ses lignes soient horizontales : celui qui rend le profil de projection
    horizontal le plus contrasté. Sur une vignette au 1/5 : ≈ 0,25 s."""
    from PIL import Image

    vignette = image.convert("L").resize((max(1, image.width // 5), max(1, image.height // 5)))

    def contraste(angle: float) -> int:
        tournee = vignette.rotate(angle, fillcolor=255, resample=Image.BILINEAR).point(lambda v: 1 if v < 160 else 0)
        largeur, hauteur = tournee.size
        octets = tournee.tobytes()
        lignes = [sum(octets[y * largeur:(y + 1) * largeur]) for y in range(hauteur)]
        return sum((b - a) ** 2 for a, b in zip(lignes, lignes[1:]))

    # À contraste égal (page blanche), l'angle le plus proche de zéro.
    grossier = max(range(-BIAIS_MAX_DEGRES, BIAIS_MAX_DEGRES + 1), key=lambda a: (contraste(a), -abs(a)))
    return max((grossier + d / 4 for d in range(-4, 5)), key=lambda a: (contraste(a), -abs(a)))


def _tourner_calque(calque: bytes, angle: float) -> bytes:
    """Tourne le contenu du calque de `angle` degrés (sens trigonométrique)
    autour du centre de la page : le texte reconnu sur l'image redressée
    retombe sur les mots tels qu'ils sont, penchés, sur le scan."""
    import math

    with pymupdf.open("pdf", calque) as doc:
        page = doc[0]
        xrefs = page.get_contents()
        contenu = b"\n".join(doc.xref_stream(x) for x in xrefs)
        cx, cy = page.mediabox.width / 2, page.mediabox.height / 2
        t = math.radians(angle)
        a, b, c, d = math.cos(t), math.sin(t), -math.sin(t), math.cos(t)
        e, f = cx - a * cx - c * cy, cy - b * cx - d * cy
        doc.update_stream(xrefs[0], f"q {a:.6f} {b:.6f} {c:.6f} {d:.6f} {e:.4f} {f:.4f} cm\n".encode() + contenu + b"\nQ")
        for xref in xrefs[1:]:
            doc.update_stream(xref, b"")
        return doc.tobytes()


# Petits mots du français courant : sur une page lue à l'endroit, ils font
# 25 à 37 % des mots reconnus ; sur une page couchée ou à l'envers, lue
# telle quelle, 5 à 18 % (mesuré sur des scans de PV). En dessous du seuil,
# ou avec trop peu de mots pour en juger, l'orientation est vérifiée.
PETITS_MOTS = frozenset(
    "le la les de des du un une et en au aux que qui ne pas pour par sur dans est il elle "
    "nous vous je se son sa ses ce cette avec été mais ou où plus qu".split()
)
PART_PETITS_MOTS_MIN = 0.22
MOTS_MIN_POUR_JUGER = 10


def _lecture_plausible(texte: str) -> float | None:
    """Part des petits mots courants parmi les mots reconnus, ou None si la
    page en compte trop peu pour en juger."""
    mots = [m.lower() for m in re.findall(r"[A-Za-zÀ-ÿ]{2,}", texte)]
    if len(mots) < MOTS_MIN_POUR_JUGER:
        return None
    return sum(m in PETITS_MOTS for m in mots) / len(mots)


def _lire_image(image, png: bytes | None = None) -> tuple[bytes, str]:
    """Reconnaît une image (`png` : la même, déjà encodée), après en avoir
    corrigé le léger biais : image redressée pour Tesseract, calque tourné
    en sens inverse pour rester aligné sur le scan."""
    from io import BytesIO

    from PIL import Image

    angle = _biais(image)
    redresser = abs(angle) >= BIAIS_MIN_DEGRES
    if redresser or png is None:
        if redresser:
            image = image.rotate(angle, fillcolor=255, resample=Image.BICUBIC)
        tampon = BytesIO()
        image.save(tampon, "PNG")
        png = tampon.getvalue()
    calque, texte = _tesseract(png)
    if redresser:
        calque = _tourner_calque(calque, -angle)
    return calque, texte


def _reconnaitre_page(png: bytes) -> tuple[bytes, str, int]:
    """Calque de texte de la page, son texte dans l'ordre de lecture, et la
    rotation (multiple de 90°) appliquée à l'image avant reconnaissance — à
    défaire en posant le calque, voir _poser_calques.

    La page est d'abord lue telle quelle : la détection d'orientation coûte
    à elle seule plus que la lecture (1,45 s contre 0,87 s par page,
    mesuré), pour des pages presque toutes à l'endroit. Elle n'est lancée
    que si le texte lu n'a pas l'allure du français (page couchée, à
    l'envers) ; la lecture tournée n'est gardée que si elle est meilleure."""
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(png))
    calque, texte = _lire_image(image, png)
    part = _lecture_plausible(texte)
    if part is not None and part >= PART_PETITS_MOTS_MIN:
        return calque, texte, 0
    rotation = _orientation(png)
    if not rotation:
        return calque, texte, 0
    calque_tourne, texte_tourne = _lire_image(image.rotate(-rotation, expand=True))
    if (_lecture_plausible(texte_tourne) or 0) > (part or 0):
        return calque_tourne, texte_tourne, rotation
    return calque, texte, 0


def _rendre_page(doc: "pymupdf.Document", numero: int) -> bytes:
    """Image de la page à reconnaître, SANS son texte natif. Une cote
    apposée en texte numérique (« D12 ») serait sinon lue une seconde fois
    par l'OCR, au même endroit, et les deux lectures s'entremêlent à
    l'extraction : « DD1122 » — cote perdue. Le texte natif est retiré de
    cette copie en mémoire seulement (jamais enregistrée) ; les images ne
    sont pas touchées."""
    page = doc[numero - 1]
    mots = page.get_text("words")
    if mots:
        for mot in mots:
            page.add_redact_annot(pymupdf.Rect(mot[:4]), fill=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
    # La page telle qu'elle s'affiche (rotation comprise) : c'est ce sens de
    # lecture que l'avocat voit, et celui dans lequel le texte est droit.
    png = page.get_pixmap(dpi=DPI_OCR, colorspace=pymupdf.csGRAY).tobytes("png")
    # MuPDF garde en cache les images déjà décodées, jusqu'à 256 Mo : inutile
    # ici, chaque page n'est rendue qu'une fois. Mesuré : 347 Mo de pic sans
    # vidage, 134 Mo avec, au même débit.
    pymupdf.TOOLS.store_shrink(100)
    return png


def _plage_pages(numeros: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] -> "1-3,7,9-10", pour des messages lisibles."""
    morceaux = []
    for debut, fin in _plages_consecutives(numeros):
        morceaux.append(str(debut) if debut == fin else f"{debut}-{fin}")
    return ",".join(morceaux)


def _plages_consecutives(numeros: list[int]) -> list[tuple[int, int]]:
    plages: list[tuple[int, int]] = []
    for n in sorted(numeros):
        if plages and n == plages[-1][1] + 1:
            plages[-1] = (plages[-1][0], n)
        else:
            plages.append((n, n))
    return plages


def _intervalle_rappels(total: int) -> int:
    # Une écriture de progression par page sur 2 000 pages, c'est 2 000
    # requêtes vers la base pour une information que personne ne lit à
    # l'unité : une tous les 0,5 % suffit, cinq pages au minimum.
    return max(5, total // 200)


def _couverture_image(page: "pymupdf.Page") -> float:
    """Part de la page couverte par des images (0 à 1)."""
    surface = abs(page.rect)
    if not surface:
        return 0.0
    couverte = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info())
    return min(1.0, couverte / surface)


def _texte_invisible(page: "pymupdf.Page") -> bool:
    """Calque de texte invisible déjà présent : le PDF a été reconnu en
    amont (scanner du greffe ou du cabinet). Le relire poserait un second
    calque sur le premier, et les deux s'entremêleraient à l'extraction."""
    return any(trace.get("type") == 3 for trace in page.get_texttrace())


def _pages_a_reconnaitre(chemin_pdf: Path, textes_natifs: list[str]) -> list[int]:
    pages: list[int] = []
    with pymupdf.open(chemin_pdf) as doc:
        for i, texte in enumerate(textes_natifs):
            longueur = _longueur_contenu(texte)
            if longueur < SEUIL_OCR_CARACTERES:
                pages.append(i + 1)
            elif longueur < SEUIL_OCR_PAGE_IMAGE:
                page = doc[i]
                if _couverture_image(page) >= COUVERTURE_IMAGE_SCAN and not _texte_invisible(page):
                    pages.append(i + 1)
    return pages


def _ocr_si_necessaire(*args, **kwargs) -> Path:
    """Comme _ocr_avec_textes, sans les textes : le PDF à relire."""
    resultat = _ocr_avec_textes(*args, **kwargs)
    return resultat[0] if isinstance(resultat, tuple) else resultat


def _ocr_avec_textes(
    chemin_pdf: Path,
    textes_natifs: list[str],
    dossier_travail: Path,
    console: Console,
    progression: Callable[[int, int, str], None] | None = None,
    pages_a_ocr: list[int] | None = None,
) -> tuple[Path, dict[int, str]]:
    """Reconnaît les pages numérisées, page par page, en parallèle, et pose
    sur chacune son calque de texte invisible — par-dessus la page
    d'origine, dont les images ne sont jamais réencodées.

    Remplace un passage par ocrmypdf qui, à l'échelle de gros dossiers,
    cumulait trois défauts mesurés :
    - une page numérisée portant déjà un fragment de texte natif (cote ou
      tampon ajouté par le logiciel du greffe, « D12 ») n'était JAMAIS
      reconnue : ocrmypdf saute toute page qui contient du texte. Or c'est
      le cas de chaque page d'un dossier coté numériquement ;
    - environ 7 s de calcul par page contre 2 s pour Tesseract seul
      (conversion PDF/A par Ghostscript, tranches de 5 pages qui laissaient
      des cœurs inoccupés) ;
    - la conversion PDF/A réencodait les images du dossier : le PDF montré
      à l'avocat n'était plus exactement la pièce reçue."""
    if pages_a_ocr is None:
        pages_a_ocr = _pages_a_reconnaitre(chemin_pdf, textes_natifs)
    if not pages_a_ocr:
        return chemin_pdf, {}
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            f"{len(pages_a_ocr)} page(s) numérisée(s) à reconnaître dans {chemin_pdf.name}, "
            "mais Tesseract n'est pas installé (paquets tesseract-ocr et tesseract-ocr-fra)."
        )

    travailleurs = _travailleurs_ocr()
    total = len(pages_a_ocr)
    console.print(
        f"  [OCR] {total} page(s) numérisée(s) "
        f"({chemin_pdf.name}) -> passage à l'OCR (français), {travailleurs} page(s) en parallèle "
        f"pour {_memoire_disponible_mo()} Mo de mémoire disponible."
    )
    if progression:
        progression(0, total, chemin_pdf.name)

    calques: dict[int, bytes] = {}
    textes: dict[int, str] = {}
    rotations: dict[int, int] = {}
    echecs: list[int] = []
    faites = 0
    pas = _intervalle_rappels(total)

    def recolter(en_cours: dict) -> None:
        nonlocal faites
        termines, _ = wait(en_cours, return_when=FIRST_COMPLETED)
        for futur in termines:
            numero = en_cours.pop(futur)
            try:
                calques[numero], textes[numero], rotations[numero] = futur.result()
            except subprocess.SubprocessError as exc:
                echecs.append(numero)
                console.print(f"  [OCR] page {numero} ({chemin_pdf.name}) non reconnue : {type(exc).__name__}")
            faites += 1
            if faites % pas == 0 or faites == total:
                console.print(f"  [OCR] {faites}/{total} page(s) reconnue(s) ({chemin_pdf.name})")
                if progression:
                    progression(faites, total, chemin_pdf.name)

    # PyMuPDF ne se partage pas entre fils : le rendu (≈ 0,2 s par page)
    # reste sur ce fil, seuls les processus Tesseract tournent en
    # parallèle. Au plus deux images en attente par travailleur : la
    # mémoire reste bornée quelle que soit la taille du dossier.
    with pymupdf.open(chemin_pdf) as doc, ThreadPoolExecutor(max_workers=travailleurs) as executeur:
        en_cours: dict = {}
        for numero in pages_a_ocr:
            while len(en_cours) >= 2 * travailleurs:
                recolter(en_cours)
            en_cours[executeur.submit(_reconnaitre_page, _rendre_page(doc, numero))] = numero
        while en_cours:
            recolter(en_cours)

    if echecs:
        console.print(
            f"  [OCR] {len(echecs)} page(s) non reconnue(s) ({chemin_pdf.name}), laissées sans texte : "
            f"{_plage_pages(echecs)}"
        )
    redressees = sorted(n for n, r in rotations.items() if r)
    if redressees:
        console.print(f"  [OCR] {len(redressees)} page(s) numérisée(s) dans le mauvais sens, redressée(s) : {_plage_pages(redressees)}")

    dossier_travail.mkdir(parents=True, exist_ok=True)
    chemin_ocr = dossier_travail / f"ocr_{chemin_pdf.stem}.pdf"
    _poser_calques(chemin_pdf, calques, chemin_ocr, dossier_travail, rotations)
    return chemin_ocr, textes


def _poser_calques(
    chemin_pdf: Path, calques: dict[int, bytes], sortie: Path, dossier_travail: Path,
    rotations: dict[int, int] | None = None,
) -> None:
    """Chaque calque produit par Tesseract embarque sa propre copie de la
    même police invisible. Posés tels quels, 100 calques donnent 100 polices
    que pdfplumber analyse une à une à la relecture : mesuré, 1,4 Go de
    mémoire pour 100 pages — fatal sur un serveur à 512 Mo. On les réunit
    d'abord dans un document à part, dédoublonné (une seule police), puis on
    les pose depuis ce document : PyMuPDF ne recopie alors qu'une fois les
    objets partagés. Mesuré : 99 Mo à la relecture, deux fois plus rapide."""
    ordre = sorted(calques)
    chemin_calques = dossier_travail / f"calques_{chemin_pdf.stem}.pdf"
    with pymupdf.open() as reunis:
        for numero in ordre:
            with pymupdf.open("pdf", calques[numero]) as calque:
                reunis.insert_pdf(calque)
        reunis.save(chemin_calques, garbage=4, deflate=True)

    temporaire = sortie.with_suffix(".tmp.pdf")
    with pymupdf.open(chemin_pdf) as doc, pymupdf.open(chemin_calques) as reunis:
        for index, numero in enumerate(ordre):
            page = doc[numero - 1]
            # Le calque est dans le sens d'affichage ; show_pdf_page attend
            # le repère non tourné de la page.
            # Une image redressée avant reconnaissance (page couchée) : le
            # calque est tourné d'autant en sens inverse pour retomber sur
            # le texte tel qu'il apparaît sur la page.
            correction = (rotations or {}).get(numero, 0)
            page.show_pdf_page(
                page.rect * page.derotation_matrix, reunis, index, overlay=True,
                rotate=(page.rotation + correction) % 360,
            )
        doc.save(temporaire, garbage=1, deflate=True)
    temporaire.replace(sortie)
    chemin_calques.unlink()


def lancer_ingestion(
    db: sqlite3.Connection,
    sources: list[Path],
    affaire_dir: Path,
    force: bool,
    console: Console,
    progression: Callable[[int, int, str], None] | None = None,
) -> None:
    """`progression(faites, total, fichier)` est appelé pendant l'OCR, la
    seule phase dont la durée se compte en dizaines de minutes : l'appelant
    (l'application web) s'en sert pour montrer à l'avocat que le traitement
    avance, plutôt qu'un libellé figé qu'on ne distingue pas d'une panne."""
    debut = datetime.now(timezone.utc)
    dossier_source = affaire_dir / "source"
    dossier_travail = affaire_dir / "work"
    dossier_source.mkdir(parents=True, exist_ok=True)

    if force:
        db.execute("DELETE FROM pages")
        db.commit()
        fichiers_existants: set[str] = set()
    else:
        fichiers_existants = {
            row[0] for row in db.execute("SELECT DISTINCT fichier_source FROM pages")
        }

    nb_pages_traitees = 0
    nb_pages_ocr = 0
    nb_cotes_trouvees = 0

    for source in sources:
        if source.name in fichiers_existants:
            console.print(f"  [ingest] {source.name} déjà ingéré, ignoré (utilise --force pour retraiter).")
            continue

        chemin_local = dossier_source / source.name
        if chemin_local.resolve() != source.resolve():
            shutil.copy2(source, chemin_local)

        textes_natifs = _extraire_textes_natifs(chemin_local)
        a_reconnaitre = _pages_a_reconnaitre(chemin_local, textes_natifs)
        reconnues = set(a_reconnaitre)
        chemin_effectif, textes_ocr = _ocr_avec_textes(
            chemin_local, textes_natifs, dossier_travail, console, progression, a_reconnaitre
        )

        if chemin_effectif != chemin_local:
            # Seules les pages reconnues ont changé. Leur texte est celui que
            # Tesseract rend dans l'ordre de lecture ; le texte natif de la
            # page (une cote tamponnée en numérique) est ajouté en tête.
            textes_finaux = [
                _fusionner(t, textes_ocr.get(i + 1, "")) if (i + 1) in reconnues else t
                for i, t in enumerate(textes_natifs)
            ]
        else:
            textes_finaux = textes_natifs

        numero_global = db.execute("SELECT COALESCE(MAX(numero_global), 0) FROM pages").fetchone()[0]

        for i, texte in enumerate(textes_finaux):
            numero_global += 1
            page_fichier = i + 1
            ocr_applique = page_fichier in reconnues
            cote = detecter_cote(texte)

            db.execute(
                """INSERT INTO pages
                   (numero_global, fichier_source, page_fichier, texte, ocr_applique,
                    empreinte_sha256, cote_detectee, statut)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'ingere')""",
                (
                    numero_global,
                    source.name,
                    page_fichier,
                    texte,
                    int(ocr_applique),
                    _empreinte(texte),
                    cote,
                ),
            )
            nb_pages_traitees += 1
            if ocr_applique:
                nb_pages_ocr += 1
            if cote:
                nb_cotes_trouvees += 1

        db.commit()

    fin = datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO run_log (etape, statut, debut, fin) VALUES (?, ?, ?, ?)",
        ("ingest", "termine", debut.isoformat(), fin.isoformat()),
    )
    db.commit()

    table = Table(title="Ingestion")
    table.add_column("Indicateur")
    table.add_column("Valeur", justify="right")
    table.add_row("Pages traitées (ce run)", str(nb_pages_traitees))
    table.add_row("Pages passées en OCR (ce run)", str(nb_pages_ocr))
    table.add_row("Cotes détectées (ce run)", str(nb_cotes_trouvees))
    total_en_base = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    table.add_row("Total de pages en base", str(total_en_base))
    console.print(table)
