"""Exécute le pipeline depouille en tâche de fond pour un dossier donné, et
synchronise la progression et les résultats vers Supabase.

Le moteur (src/depouille, déjà testé — 61 tests, validé avec un vrai appel
au modèle) n'est pas modifié : ce module l'enveloppe, il ne le réécrit pas.
Le contenu extrait d'un dossier (pièces, déclarations, événements) continue
de vivre dans le fichier SQLite par affaire ; seuls ce fichier et les
livrables générés sont envoyés vers Supabase Storage — jamais rejoués dans
des tables Postgres. Voir web/backend/README.md.
"""

from __future__ import annotations

import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from depouille.build_deliverables import construire_livrables
from depouille.chrono import lancer_chrono
from depouille.classify import lancer_classification
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.declarations import lancer_declarations
from depouille.index_builder import construire_index
from depouille.ingest import lancer_ingestion

from .supabase_client import client_service

NOMS_LIVRABLES = (
    "00_dossier_surligne.pdf",
    "01_index.xlsx",
    "02_chronologie_procedure.docx",
    "03_chronologie_faits.docx",
    "04_declarations.xlsx",
    "05_personnalite.docx",
    "06_signalements_procedure.docx",
    "99_controle.md",
)


def _config_llm(offline: bool) -> Config:
    if offline:
        return Config(offline=True, provider="offline")
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    variable_cle = "ANTHROPIC_API_KEY" if provider == "anthropic" else "MISTRAL_API_KEY"
    return Config(
        offline=False,
        provider=provider,
        api_key=os.environ.get(variable_cle, ""),
        modele_classification=os.environ.get("MODELE_CLASSIFICATION", "claude-haiku-4-5-20251001"),
        modele_analyse=os.environ.get("MODELE_ANALYSE", "claude-sonnet-5"),
        seuil_confiance=0.7,
        seuil_flou_ocr=97,
    )


def _maj_dossier(supabase, dossier_id: str, **champs) -> None:
    supabase.table("dossiers").update(champs).eq("id", dossier_id).execute()


def _maj_etape(supabase, dossier_id: str, etape: str, **champs) -> None:
    supabase.table("traitement_etapes").upsert(
        {"dossier_id": dossier_id, "etape": etape, **champs},
        on_conflict="dossier_id,etape",
    ).execute()


def _cout_etape(db, nom_etape: str) -> tuple[float, int, int]:
    """L'index et l'assemblage des livrables n'appellent pas le modèle : pas
    de ligne dans run_log pour eux, coût nul — ce n'est pas une erreur."""
    row = db.execute(
        "SELECT cout_usd, tokens_in, tokens_out FROM run_log WHERE etape = ? ORDER BY id DESC LIMIT 1",
        (nom_etape,),
    ).fetchone()
    if row is None:
        return 0.0, 0, 0
    return row["cout_usd"], row["tokens_in"], row["tokens_out"]


_TYPES_MIME = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".db": "application/octet-stream",
}


def _options_upload(chemin: Path) -> dict:
    # Sans "content-type" explicite, l'API Storage retombe sur text/plain :
    # le fichier arrive intact, mais le navigateur refuse de l'afficher en
    # PDF/Word/Excel et affiche une erreur de chargement.
    type_mime = _TYPES_MIME.get(chemin.suffix, "application/octet-stream")
    return {"upsert": "true", "content-type": type_mime}


def _televerser_resultats(supabase, dossier_id: str, affaire_dir: Path) -> None:
    bucket = supabase.storage.from_("dossiers-resultats")

    chemin_db = affaire_dir / "depouille.db"
    if chemin_db.exists():
        bucket.upload(f"{dossier_id}/depouille.db", str(chemin_db), _options_upload(chemin_db))

    dossier_out = affaire_dir / "out"
    for nom in NOMS_LIVRABLES:
        chemin = dossier_out / nom
        if chemin.exists():
            bucket.upload(f"{dossier_id}/out/{nom}", str(chemin), _options_upload(chemin))

    _maj_dossier(supabase, dossier_id, resultat_db_path=f"{dossier_id}/depouille.db")


def traiter_dossier(dossier_id: str, chemins_pdf_locaux: list[Path], offline: bool = False) -> None:
    """Point d'entrée appelé en tâche de fond après l'upload d'un dossier.

    Utilise le client service_role (accès élevé) : cette fonction tourne
    côté serveur uniquement, jamais déclenchée directement par le
    navigateur — l'endpoint qui l'invoque a déjà vérifié, via le client
    utilisateur et la RLS, que le dossier appartient bien à l'appelant.
    """
    supabase = client_service()
    console = Console(quiet=True)
    config = _config_llm(offline)

    with tempfile.TemporaryDirectory(prefix="depouille_") as tmp:
        affaire_dir = Path(tmp) / dossier_id
        db = ouvrir_db(affaire_dir / "depouille.db")

        etapes_pipeline = (
            ("ingest", lambda: lancer_ingestion(db, chemins_pdf_locaux, affaire_dir, force=False, console=console)),
            ("classify", lambda: lancer_classification(db, config, force=False, console=console)),
            ("index", lambda: construire_index(db, affaire_dir, console=console)),
            ("chrono", lambda: lancer_chrono(db, config, force=False, console=console)),
            ("decl", lambda: lancer_declarations(db, config, force=False, console=console)),
            ("build", lambda: construire_livrables(db, affaire_dir, config, console=console)),
        )

        try:
            _maj_dossier(supabase, dossier_id, statut="en_cours")

            for nom_etape, fonction in etapes_pipeline:
                _maj_dossier(supabase, dossier_id, etape_courante=nom_etape)
                _maj_etape(
                    supabase, dossier_id, nom_etape,
                    statut="en_cours", debut=datetime.now(timezone.utc).isoformat(),
                )
                try:
                    fonction()
                except Exception as exc:  # noqa: BLE001
                    _maj_etape(
                        supabase, dossier_id, nom_etape,
                        statut="erreur", fin=datetime.now(timezone.utc).isoformat(),
                        message_erreur=str(exc)[:500],
                    )
                    raise

                cout, tokens_in, tokens_out = _cout_etape(db, nom_etape)
                _maj_etape(
                    supabase, dossier_id, nom_etape,
                    statut="termine", fin=datetime.now(timezone.utc).isoformat(),
                    tokens_in=tokens_in, tokens_out=tokens_out, cout_usd=cout,
                )

            _televerser_resultats(supabase, dossier_id, affaire_dir)

            nb_pages = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            _maj_dossier(supabase, dossier_id, statut="termine", etape_courante=None, nb_pages=nb_pages)

        except Exception as exc:  # noqa: BLE001
            _maj_dossier(
                supabase, dossier_id,
                statut="erreur",
                message_erreur=(str(exc) + "\n" + traceback.format_exc())[-1000:],
            )
        finally:
            db.close()
