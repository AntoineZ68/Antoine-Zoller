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


def _tesseract_texte_seul(png: bytes) -> bytes:
    """Reconnaît une page rendue en image et renvoie son calque de texte
    invisible, sous forme d'un PDF d'une page SANS image (textonly_pdf) :
    chaque mot y est placé à l'endroit exact où il figure sur le scan.

    Un seul fil par appel (OMP_THREAD_LIMIT=1) : le parallélisme se fait
    entre pages, bien plus efficace que le multi-fil interne de Tesseract
    sur une page isolée."""
    resultat = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "fra", "--dpi", str(DPI_OCR), "-c", "textonly_pdf=1", "pdf"],
        input=png,
        capture_output=True,
        timeout=DELAI_TESSERACT_PAR_PAGE_S,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        check=True,
    )
    if not resultat.stdout.startswith(b"%PDF"):
        raise subprocess.SubprocessError("Tesseract n'a pas produit de calque de texte.")
    return resultat.stdout


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


def _ocr_si_necessaire(
    chemin_pdf: Path,
    textes_natifs: list[str],
    dossier_travail: Path,
    console: Console,
    progression: Callable[[int, int, str], None] | None = None,
    pages_a_ocr: list[int] | None = None,
) -> Path:
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
        return chemin_pdf
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
    echecs: list[int] = []
    faites = 0
    pas = _intervalle_rappels(total)

    def recolter(en_cours: dict) -> None:
        nonlocal faites
        termines, _ = wait(en_cours, return_when=FIRST_COMPLETED)
        for futur in termines:
            numero = en_cours.pop(futur)
            try:
                calques[numero] = futur.result()
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
            en_cours[executeur.submit(_tesseract_texte_seul, _rendre_page(doc, numero))] = numero
        while en_cours:
            recolter(en_cours)

    if echecs:
        console.print(
            f"  [OCR] {len(echecs)} page(s) non reconnue(s) ({chemin_pdf.name}), laissées sans texte : "
            f"{_plage_pages(echecs)}"
        )

    dossier_travail.mkdir(parents=True, exist_ok=True)
    chemin_ocr = dossier_travail / f"ocr_{chemin_pdf.stem}.pdf"
    _poser_calques(chemin_pdf, calques, chemin_ocr, dossier_travail)
    return chemin_ocr


def _poser_calques(chemin_pdf: Path, calques: dict[int, bytes], sortie: Path, dossier_travail: Path) -> None:
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
            page.show_pdf_page(page.rect * page.derotation_matrix, reunis, index, overlay=True, rotate=page.rotation)
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
        chemin_effectif = _ocr_si_necessaire(
            chemin_local, textes_natifs, dossier_travail, console, progression, a_reconnaitre
        )

        if chemin_effectif != chemin_local:
            # Seules les pages reconnues ont changé : relire les 2 000 pages
            # d'un gros dossier pour en retrouver 1 990 identiques coûterait
            # plusieurs minutes pour rien.
            relus = _extraire_textes_natifs(chemin_effectif, reconnues)
            textes_finaux = [relus[i] if (i + 1) in reconnues else t for i, t in enumerate(textes_natifs)]
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
