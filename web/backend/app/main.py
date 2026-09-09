"""API HTTP du pilote : upload d'un dossier, suivi de traitement, récupération
des livrables. Ne fait tourner aucune logique métier elle-même — délègue tout
au moteur (via pipeline.py) et à Postgres (via les clients Supabase).

Toutes les routes `/api/dossiers*` passent par le client authentifié comme
l'avocat appelant (`client_utilisateur`), pour que la Row Level Security de
Postgres décide seule de ce qu'il peut voir ou modifier : on ne réimplémente
aucune vérification d'appartenance côté application.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from . import schemas
from .pipeline import NOMS_LIVRABLES, traiter_dossier
from .supabase_client import client_service, client_utilisateur

app = FastAPI(title="Depouille — API pilote")

_origines = [o.strip() for o in os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origines,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _contexte_utilisateur(authorization: str | None = Header(None)) -> tuple[Client, str]:
    """Valide le jeton reçu auprès de Supabase Auth et renvoie un client
    Postgres agissant comme cet utilisateur, plus son id. Ne fait jamais
    confiance à un id fourni par le client lui-même."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="En-tête Authorization manquant.")
    jeton = authorization.split(" ", 1)[1].strip()
    supabase = client_utilisateur(jeton)
    try:
        reponse = supabase.auth.get_user(jeton)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Jeton invalide.") from exc
    if reponse is None or reponse.user is None:
        raise HTTPException(status_code=401, detail="Jeton invalide.")
    return supabase, reponse.user.id


def _dossier_ou_404(supabase: Client, dossier_id: str) -> dict:
    """La RLS filtre déjà par propriétaire : un dossier absent du résultat
    signifie soit qu'il n'existe pas, soit qu'il n'appartient pas à
    l'appelant — les deux cas renvoient un simple 404, jamais un 403 qui
    confirmerait l'existence du dossier d'un tiers."""
    resultat = supabase.table("dossiers").select("*").eq("id", dossier_id).execute()
    if not resultat.data:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return resultat.data[0]


def _traiter_puis_nettoyer(dossier_id: str, chemin_pdf: Path, repertoire: Path, offline: bool) -> None:
    try:
        traiter_dossier(dossier_id, chemin_pdf, offline=offline)
    finally:
        shutil.rmtree(repertoire, ignore_errors=True)


@app.get("/health")
def sante() -> dict:
    return {"ok": True}


@app.post("/api/dossiers", response_model=schemas.DossierResume, status_code=201)
async def creer_dossier(
    background_tasks: BackgroundTasks,
    fichier: UploadFile = File(...),
    nom: str = Form(...),
    reference: str | None = Form(None),
    mode_offline: bool = Form(False),
    contexte: tuple[Client, str] = Depends(_contexte_utilisateur),
) -> dict:
    supabase, utilisateur_id = contexte

    if fichier.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    resultat = supabase.table("dossiers").insert(
        {"nom": nom, "reference": reference, "owner_id": utilisateur_id}
    ).execute()
    dossier = resultat.data[0]
    dossier_id = dossier["id"]

    repertoire_local = Path(tempfile.mkdtemp(prefix=f"depouille_upload_{dossier_id}_"))
    chemin_local = repertoire_local / (fichier.filename or "dossier.pdf")
    with chemin_local.open("wb") as f:
        shutil.copyfileobj(fichier.file, f)
    await fichier.close()

    chemin_storage = f"{utilisateur_id}/{dossier_id}/{chemin_local.name}"
    client_service().storage.from_("dossiers-source").upload(
        chemin_storage, str(chemin_local), {"upsert": "true"}
    )
    supabase.table("dossiers").update({"fichier_source_path": chemin_storage}).eq("id", dossier_id).execute()

    background_tasks.add_task(_traiter_puis_nettoyer, dossier_id, chemin_local, repertoire_local, mode_offline)

    return dossier


@app.get("/api/dossiers", response_model=list[schemas.DossierResume])
def lister_dossiers(contexte: tuple[Client, str] = Depends(_contexte_utilisateur)) -> list[dict]:
    supabase, _ = contexte
    resultat = supabase.table("dossiers").select("*").order("cree_le", desc=True).execute()
    return resultat.data


@app.get("/api/dossiers/{dossier_id}", response_model=schemas.DossierDetail)
def detail_dossier(dossier_id: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)) -> dict:
    supabase, _ = contexte
    dossier = _dossier_ou_404(supabase, dossier_id)
    etapes = (
        supabase.table("traitement_etapes")
        .select("*")
        .eq("dossier_id", dossier_id)
        .order("debut")
        .execute()
        .data
    )
    dossier["etapes"] = etapes
    return dossier


@app.get("/api/dossiers/{dossier_id}/livrables/{nom_fichier}")
def telecharger_livrable(
    dossier_id: str, nom_fichier: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)
) -> dict:
    supabase, _ = contexte
    _dossier_ou_404(supabase, dossier_id)  # vérifie l'appartenance via la RLS

    if nom_fichier not in NOMS_LIVRABLES:
        raise HTTPException(status_code=404, detail="Livrable inconnu.")

    chemin = f"{dossier_id}/out/{nom_fichier}"
    reponse = client_service().storage.from_("dossiers-resultats").create_signed_url(chemin, 300)
    url = reponse.get("signedURL") or reponse.get("signedUrl")
    if not url:
        raise HTTPException(status_code=404, detail="Livrable pas encore disponible — traitement en cours ?")
    return {"url": url}
