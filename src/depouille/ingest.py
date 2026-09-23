"""Étape 1 — Ingestion : découpage en pages, extraction de texte, OCR si
nécessaire, détection de cote.

Une page passe en OCR si son texte natif fait moins de 50 caractères. Le
texte final stocké est toujours celui effectivement lu sur la page (natif
ou OCR) — jamais une estimation.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pdfplumber
from rich.console import Console
from rich.table import Table

from .regex_patterns import detecter_cote

SEUIL_OCR_CARACTERES = 50

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


def _extraire_textes_natifs(chemin_pdf: Path) -> list[str]:
    """Libère chaque page après l'avoir lue : pdfplumber garde sinon en
    mémoire, pour toute la durée de l'ouverture, la liste des objets de
    CHAQUE page déjà parcourue (caractères, tracés, images). Sur un dossier
    de quelques pages ça ne se voit pas ; sur un dossier de soixante pages
    scannées, cette accumulation est ce qui sépare un run qui aboutit d'un
    run tué par le noyau — et un run tué ne rend aucun livrable."""
    textes = []
    with pdfplumber.open(chemin_pdf) as pdf:
        for page in pdf.pages:
            textes.append(_texte_page(page))
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


def _travailleurs_ocr() -> int:
    budget = _memoire_disponible_mo() - MEMOIRE_BASE_MO
    par_memoire = budget // MEMOIRE_PAR_TRAVAILLEUR_MO
    return max(1, min(par_memoire, os.cpu_count() or 1))


# Pages reconnues par appel. L'OCR est la phase la plus longue de tout le
# traitement — sur un petit serveur, compter de l'ordre de la minute par
# page numérisée —, et un seul appel pour tout le fichier la rendait
# opaque : un dossier de 100 pages scannées affichait « Lecture du
# document » pendant une heure sans que rien ne distingue un traitement
# lent d'un traitement mort. Par tranches, on peut dire où on en est.
# Mesuré sur 20 pages scannées : même durée qu'en un seul appel (-2 %),
# même texte reconnu à 99,3 % des mots près.
PAGES_OCR_PAR_TRANCHE = 5

# Délai accordé à Tesseract par page avant qu'il abandonne. La valeur par
# défaut d'ocrmypdf (180 s) est calibrée pour une machine de bureau ; sur un
# serveur à fraction de cœur, une page dense peut la dépasser — et la page
# ressort alors SANS AUCUN TEXTE, sans erreur ni avertissement visible.
DELAI_TESSERACT_PAR_PAGE_S = 900


def _plage_pages(numeros: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] -> "1-3,7,9-10", format attendu par ocrmypdf."""
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


def _ocr_si_necessaire(
    chemin_pdf: Path,
    textes_natifs: list[str],
    dossier_travail: Path,
    console: Console,
    progression: Callable[[int, int, str], None] | None = None,
) -> Path:
    pages_a_ocr = [i + 1 for i, t in enumerate(textes_natifs) if _longueur_contenu(t) < SEUIL_OCR_CARACTERES]
    if not pages_a_ocr:
        return chemin_pdf

    travailleurs = _travailleurs_ocr()
    console.print(
        f"  [OCR] {len(pages_a_ocr)} page(s) sous le seuil de {SEUIL_OCR_CARACTERES} caractères "
        f"({chemin_pdf.name}) -> passage à l'OCR (français), {travailleurs} page(s) en parallèle "
        f"pour {_memoire_disponible_mo()} Mo de mémoire disponible."
    )
    import ocrmypdf

    dossier_travail.mkdir(parents=True, exist_ok=True)
    chemin_ocr = dossier_travail / f"ocr_{chemin_pdf.stem}.pdf"
    total = len(pages_a_ocr)
    if progression:
        progression(0, total, chemin_pdf.name)

    # Chaque tranche repart de la sortie de la précédente : les pages déjà
    # reconnues portent désormais du texte et sont ignorées (skip_text), le
    # fichier final est un PDF complet, identique à un appel unique — c'est
    # lui que le surlignage réutilise pour situer les citations.
    courant = chemin_pdf
    faites = 0
    for i in range(0, total, PAGES_OCR_PAR_TRANCHE):
        tranche = pages_a_ocr[i : i + PAGES_OCR_PAR_TRANCHE]
        sortie = dossier_travail / f"ocr_{chemin_pdf.stem}.tranche.pdf"
        ocrmypdf.ocr(
            str(courant),
            str(sortie),
            language="fra",
            skip_text=True,
            progress_bar=False,
            jobs=travailleurs,
            pages=_plage_pages(tranche),
            tesseract_timeout=DELAI_TESSERACT_PAR_PAGE_S,
        )
        sortie.replace(chemin_ocr)
        courant = chemin_ocr
        faites += len(tranche)
        console.print(f"  [OCR] {faites}/{total} page(s) reconnue(s) ({chemin_pdf.name})")
        if progression:
            progression(faites, total, chemin_pdf.name)
    return chemin_ocr


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
        chemin_effectif = _ocr_si_necessaire(chemin_local, textes_natifs, dossier_travail, console, progression)

        if chemin_effectif != chemin_local:
            textes_finaux = _extraire_textes_natifs(chemin_effectif)
        else:
            textes_finaux = textes_natifs

        numero_global = db.execute("SELECT COALESCE(MAX(numero_global), 0) FROM pages").fetchone()[0]

        for i, texte in enumerate(textes_finaux):
            numero_global += 1
            page_fichier = i + 1
            ocr_applique = _longueur_contenu(textes_natifs[i]) < SEUIL_OCR_CARACTERES
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
