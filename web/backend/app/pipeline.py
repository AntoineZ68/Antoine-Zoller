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
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

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


T = TypeVar("T")


def _avec_retries(requete: Callable[[], T], tentatives: int = 3, delai_s: float = 2.0) -> T:
    """Un traitement dure plusieurs minutes et écrit sa progression vers
    Supabase des dizaines de fois — une passerelle qui répond mal une seule
    fois (504 Gateway Timeout, panne transitoire) ne doit pas faire échouer
    tout un traitement par ailleurs réussi. Observé en réel : un dossier
    entièrement traité (les 6 étapes, tous les livrables déjà envoyés dans
    le Storage) est parti en erreur à cause d'un seul timeout sur une simple
    mise à jour de statut."""
    derniere_erreur: Exception | None = None
    for tentative in range(tentatives):
        try:
            return requete()
        except Exception as exc:  # noqa: BLE001
            derniere_erreur = exc
            if tentative < tentatives - 1:
                time.sleep(delai_s * (tentative + 1))
    assert derniere_erreur is not None
    raise derniere_erreur


def _maj_dossier(supabase, dossier_id: str, **champs) -> None:
    _avec_retries(lambda: supabase.table("dossiers").update(champs).eq("id", dossier_id).execute())


def _maj_etape(supabase, dossier_id: str, etape: str, **champs) -> None:
    _avec_retries(
        lambda: supabase.table("traitement_etapes").upsert(
            {"dossier_id": dossier_id, "etape": etape, **champs},
            on_conflict="dossier_id,etape",
        ).execute()
    )


# Le traitement tourne DANS le processus web. Si ce processus meurt en
# cours de route — mémoire saturée, redémarrage lors d'un déploiement, mise
# en veille par l'hébergeur —, le traitement disparaît avec lui sans qu'aucun
# code n'ait l'occasion d'écrire « erreur » : le dossier restait affiché
# « en cours » indéfiniment, indiscernable d'un traitement simplement lent.
# Tant que le processus vit, ce battement rafraîchit `mis_a_jour_le` (via le
# déclencheur de la table) ; son silence prolongé est ce qui permet à l'API
# de conclure que le traitement est mort (voir main._cloturer_orphelins).
INTERVALLE_BATTEMENT_S = 30


class _Battement:
    def __init__(self, dossier_id: str) -> None:
        self.dossier_id = dossier_id
        self.etape: str | None = None
        self._arret = threading.Event()
        self._fil = threading.Thread(target=self._boucle, name=f"battement-{dossier_id}", daemon=True)

    def demarrer(self) -> None:
        self._fil.start()

    def arreter(self) -> None:
        self._arret.set()
        if self._fil.is_alive():
            self._fil.join(timeout=5)

    def _boucle(self) -> None:
        # Client distinct de celui du traitement : les deux fils écrivent en
        # parallèle.
        supabase = client_service()
        while not self._arret.wait(INTERVALLE_BATTEMENT_S):
            try:
                supabase.table("dossiers").update({"etape_courante": self.etape}).eq("id", self.dossier_id).execute()
            except Exception as exc:  # noqa: BLE001 — un battement manqué n'est pas grave, le suivant rattrapera
                print(f"[pipeline] battement manqué pour {self.dossier_id} : {exc}")


def _rappel_progression_ocr(supabase, dossier_id: str) -> Callable[[int, int, str], None]:
    """Écrit « Pages numérisées reconnues : 23 / 105 » sur l'étape de
    lecture. Au mieux : une progression perdue ne doit jamais faire échouer
    un traitement — et si la colonne `detail` n'existe pas encore (migration
    0004 non appliquée), on le signale une fois puis on se tait."""
    actif = True

    def rappel(faites: int, total: int, fichier: str) -> None:
        nonlocal actif
        if not actif:
            return
        try:
            supabase.table("traitement_etapes").upsert(
                {"dossier_id": dossier_id, "etape": "ingest",
                 "detail": f"Pages numérisées reconnues : {faites} / {total}"},
                on_conflict="dossier_id,etape",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            actif = False
            print(f"[pipeline] progression OCR non enregistrée (migration 0004 appliquée ?) : {exc}")

    return rappel


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

    try:
        _maj_dossier(supabase, dossier_id, resultat_db_path=f"{dossier_id}/depouille.db")
    except Exception as exc:  # noqa: BLE001 — champ purement informatif, jamais relu
        # ailleurs (donnees_dossier reconstruit le chemin directement à partir
        # de dossier_id) : son échec, même après retries, ne doit pas faire
        # basculer en erreur un dossier dont tous les fichiers sont pourtant
        # déjà bien présents dans le Storage à ce stade.
        print(f"[pipeline] échec non bloquant de la mise à jour de resultat_db_path pour {dossier_id} : {exc}")


def traiter_dossier(dossier_id: str, chemins_pdf_locaux: list[Path], offline: bool = False) -> None:
    """Point d'entrée appelé en tâche de fond après l'upload d'un dossier.

    Utilise le client service_role (accès élevé) : cette fonction tourne
    côté serveur uniquement, jamais déclenchée directement par le
    navigateur — l'endpoint qui l'invoque a déjà vérifié, via le client
    utilisateur et la RLS, que le dossier appartient bien à l'appelant.
    """
    supabase = client_service()
    # quiet=True supprimait TOUT l'affichage du pipeline, y compris les
    # messages d'erreur destinés au développeur (ex. "[chrono] échec
    # extraction des faits...") — une vraie panne (clé LLM invalide, réponse
    # mal formée) devenait alors indiscernable d'un cas où il n'y avait
    # simplement rien à extraire, sans aucune trace dans les logs Render.
    console = Console()
    config = _config_llm(offline)

    with tempfile.TemporaryDirectory(prefix="depouille_") as tmp:
        affaire_dir = Path(tmp) / dossier_id
        db = ouvrir_db(affaire_dir / "depouille.db")

        etapes_pipeline = (
            ("ingest", lambda: lancer_ingestion(
                db, chemins_pdf_locaux, affaire_dir, force=False, console=console,
                progression=_rappel_progression_ocr(supabase, dossier_id),
            )),
            ("classify", lambda: lancer_classification(db, config, force=False, console=console)),
            ("index", lambda: construire_index(db, affaire_dir, console=console)),
            ("chrono", lambda: lancer_chrono(db, config, force=False, console=console)),
            ("decl", lambda: lancer_declarations(db, config, force=False, console=console)),
            ("build", lambda: construire_livrables(db, affaire_dir, config, console=console)),
        )

        battement = _Battement(dossier_id)
        try:
            _maj_dossier(supabase, dossier_id, statut="en_cours")
            battement.demarrer()

            for nom_etape, fonction in etapes_pipeline:
                battement.etape = nom_etape
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
            try:
                _maj_dossier(
                    supabase, dossier_id,
                    statut="erreur",
                    message_erreur=(str(exc) + "\n" + traceback.format_exc())[-1000:],
                )
            except Exception as exc_signalement:  # noqa: BLE001 — au pire ce
                # dossier reste bloqué "en cours" (récupérable via le bouton
                # Supprimer), mais la tâche de fond ne doit jamais planter
                # sans laisser au moins une trace dans les logs.
                print(f"[pipeline] échec du signalement d'erreur pour {dossier_id} : {exc_signalement}")
        finally:
            battement.arreter()
            db.close()
