"""Étape 1 — Ingestion : découpage en pages, extraction de texte, OCR si
nécessaire, détection de cote.

Une page passe en OCR si son texte natif fait moins de 50 caractères. Le
texte final stocké est toujours celui effectivement lu sur la page (natif
ou OCR) — jamais une estimation.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
from rich.console import Console
from rich.table import Table

from .regex_patterns import detecter_cote

SEUIL_OCR_CARACTERES = 50


def _extraire_textes_natifs(chemin_pdf: Path) -> list[str]:
    with pdfplumber.open(chemin_pdf) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def _empreinte(texte: str, secours: bytes = b"") -> str:
    contenu = texte.strip().encode("utf-8") if texte.strip() else secours
    return hashlib.sha256(contenu).hexdigest()


def _ocr_si_necessaire(chemin_pdf: Path, textes_natifs: list[str], dossier_travail: Path, console: Console) -> Path:
    pages_a_ocr = [i + 1 for i, t in enumerate(textes_natifs) if len(t.strip()) < SEUIL_OCR_CARACTERES]
    if not pages_a_ocr:
        return chemin_pdf

    console.print(
        f"  [OCR] {len(pages_a_ocr)} page(s) sous le seuil de {SEUIL_OCR_CARACTERES} caractères "
        f"({chemin_pdf.name}) -> passage à l'OCR (français)."
    )
    import ocrmypdf

    dossier_travail.mkdir(parents=True, exist_ok=True)
    chemin_ocr = dossier_travail / f"ocr_{chemin_pdf.stem}.pdf"
    ocrmypdf.ocr(
        str(chemin_pdf),
        str(chemin_ocr),
        language="fra",
        skip_text=True,
        progress_bar=False,
    )
    return chemin_ocr


def lancer_ingestion(
    db: sqlite3.Connection,
    sources: list[Path],
    affaire_dir: Path,
    force: bool,
    console: Console,
) -> None:
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
        chemin_effectif = _ocr_si_necessaire(chemin_local, textes_natifs, dossier_travail, console)

        if chemin_effectif != chemin_local:
            textes_finaux = _extraire_textes_natifs(chemin_effectif)
        else:
            textes_finaux = textes_natifs

        numero_global = db.execute("SELECT COALESCE(MAX(numero_global), 0) FROM pages").fetchone()[0]

        for i, texte in enumerate(textes_finaux):
            numero_global += 1
            page_fichier = i + 1
            ocr_applique = len(textes_natifs[i].strip()) < SEUIL_OCR_CARACTERES
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
