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
import sqlite3
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from depouille.chrono import calculer_durees
from depouille.conformite import detecter_signalements
from depouille.recoupements import detecter_recoupements

from . import schemas
from .pipeline import NOMS_LIVRABLES, traiter_dossier
from .supabase_client import client_service, client_utilisateur

app = FastAPI(title="Depouille — API pilote")

_ORIGINES_PAR_DEFAUT = "https://antoine-zoller.onrender.com,http://localhost:5173"
_origines = [o.strip() for o in os.environ.get("FRONTEND_ORIGIN", _ORIGINES_PAR_DEFAUT).split(",") if o.strip()]
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


def _traiter_puis_nettoyer(dossier_id: str, chemins_pdf: list[Path], repertoire: Path, offline: bool) -> None:
    try:
        traiter_dossier(dossier_id, chemins_pdf, offline=offline)
    finally:
        shutil.rmtree(repertoire, ignore_errors=True)


@app.get("/health")
def sante() -> dict:
    return {"ok": True}


def _nom_disponible(repertoire: Path, nom_souhaite: str) -> str:
    """Un vrai dossier pénal arrive souvent en plusieurs PDF distincts, mais
    rien n'empêche deux fichiers de porter le même nom (deux exports "PV.pdf"
    par exemple) : on désambiguïse plutôt que d'écraser silencieusement l'un
    des deux en local."""
    chemin = repertoire / nom_souhaite
    if not chemin.exists():
        return nom_souhaite
    tige, suffixe = Path(nom_souhaite).stem, Path(nom_souhaite).suffix
    compteur = 2
    while (repertoire / f"{tige}_{compteur}{suffixe}").exists():
        compteur += 1
    return f"{tige}_{compteur}{suffixe}"


@app.post("/api/dossiers", response_model=schemas.DossierResume, status_code=201)
async def creer_dossier(
    background_tasks: BackgroundTasks,
    fichiers: list[UploadFile] = File(...),
    nom: str = Form(...),
    reference: str | None = Form(None),
    mode_offline: bool = Form(False),
    contexte: tuple[Client, str] = Depends(_contexte_utilisateur),
) -> dict:
    supabase, utilisateur_id = contexte

    if not fichiers:
        raise HTTPException(status_code=400, detail="Au moins un fichier PDF est requis.")
    for fichier in fichiers:
        if fichier.content_type not in ("application/pdf", "application/x-pdf"):
            raise HTTPException(status_code=400, detail=f"'{fichier.filename}' n'est pas un PDF.")

    resultat = supabase.table("dossiers").insert(
        {"nom": nom, "reference": reference, "owner_id": utilisateur_id}
    ).execute()
    dossier = resultat.data[0]
    dossier_id = dossier["id"]

    repertoire_local = Path(tempfile.mkdtemp(prefix=f"depouille_upload_{dossier_id}_"))
    try:
        chemins_locaux: list[Path] = []
        bucket_source = client_service().storage.from_("dossiers-source")
        for fichier in fichiers:
            nom_fichier = _nom_disponible(repertoire_local, fichier.filename or "dossier.pdf")
            chemin_local = repertoire_local / nom_fichier
            with chemin_local.open("wb") as f:
                shutil.copyfileobj(fichier.file, f)
            await fichier.close()
            chemins_locaux.append(chemin_local)

            chemin_storage = f"{utilisateur_id}/{dossier_id}/{nom_fichier}"
            bucket_source.upload(chemin_storage, str(chemin_local), {"upsert": "true", "content-type": "application/pdf"})

        supabase.table("dossiers").update(
            {"fichier_source_path": f"{utilisateur_id}/{dossier_id}/"}
        ).eq("id", dossier_id).execute()
    except Exception as exc:  # noqa: BLE001
        # Un fichier sur plusieurs qui échoue à l'envoi (réseau, Storage
        # indisponible) laissait sinon un dossier fantôme en base — jamais
        # traité puisqu'on n'atteint la planification de la tâche de fond
        # qu'après cette boucle — et son répertoire temporaire local fuyait
        # sur le disque, faute d'être jamais nettoyé par _traiter_puis_nettoyer.
        shutil.rmtree(repertoire_local, ignore_errors=True)
        supabase.table("dossiers").delete().eq("id", dossier_id).execute()
        raise HTTPException(status_code=502, detail="Échec de l'envoi des fichiers, réessaie.") from exc

    background_tasks.add_task(_traiter_puis_nettoyer, dossier_id, chemins_locaux, repertoire_local, mode_offline)

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


def _cle_tri_date(date_jj_mm_aaaa: str | None) -> tuple[int, int, int, int]:
    """Convertit une date JJ/MM/AAAA en clé triable chronologiquement — les
    faits sans date connue passent en dernier plutôt qu'en tête (tri sur
    chaîne brute donnerait un ordre alphabétique dénué de sens)."""
    if not date_jj_mm_aaaa:
        return (1, 0, 0, 0)
    jour, mois, annee = date_jj_mm_aaaa.split("/")
    return (0, int(annee), int(mois), int(jour))


@app.get("/api/dossiers/{dossier_id}/donnees", response_model=schemas.DonneesDossier)
def donnees_dossier(dossier_id: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)) -> dict:
    """Données structurées (personnes, chronologies) pour l'affichage type
    tableau de bord — lit directement le fichier SQLite de l'affaire, sans
    jamais rejouer son contenu dans des tables Postgres (voir le choix
    d'architecture documenté dans web/backend/README.md)."""
    supabase, _ = contexte
    dossier = _dossier_ou_404(supabase, dossier_id)
    if dossier["statut"] != "termine":
        raise HTTPException(status_code=404, detail="Le traitement de ce dossier n'est pas encore terminé.")

    try:
        contenu_db = client_service().storage.from_("dossiers-resultats").download(f"{dossier_id}/depouille.db")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Résultats introuvables pour ce dossier.") from exc

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        tmp.write(contenu_db)
        tmp.flush()
        db = sqlite3.connect(tmp.name)
        db.row_factory = sqlite3.Row
        try:
            try:
                ligne_resume = db.execute("SELECT texte FROM resume_affaire WHERE id = 1").fetchone()
                resume = ligne_resume["texte"] if ligne_resume else None
            except sqlite3.OperationalError:
                # Dossier traité avant l'introduction de cette table.
                resume = None
            personnes = [dict(r) for r in db.execute("SELECT nom, role FROM personnes ORDER BY role, nom")]
            faits = [
                dict(r)
                for r in db.execute(
                    """SELECT ef.page, ef.citation, ef.description, p.nom AS personne,
                              pi.date_apparente AS date, pi.heure_apparente AS heure
                       FROM evenements_faits ef
                       LEFT JOIN personnes p ON p.id = ef.personne_id_source
                       JOIN pieces pi ON pi.id = ef.piece_id
                       WHERE ef.statut_verif = 'verifie'
                       ORDER BY ef.page"""
                )
            ]
            # Un fait hérite de la date/heure de sa pièce (formule d'ouverture
            # du PV, détectée de façon déterministe pendant la classification)
            # — trier là-dessus donne une vraie chronologie, pas seulement
            # l'ordre d'arrivée des documents. Heure inconnue = début de
            # journée par convention, pour ne pas casser le tri par date
            # quand seule l'heure manque.
            faits.sort(key=lambda f: (_cle_tri_date(f["date"]), f["heure"] or "00h00", f["page"]))
            procedure = [
                dict(r)
                for r in db.execute(
                    """SELECT ep.date, ep.heure, ep.nature, ep.page, ep.citation, p.nom AS personne
                       FROM evenements_procedure ep LEFT JOIN personnes p ON p.id = ep.personne_id
                       WHERE ep.statut_verif = 'verifie'
                       ORDER BY ep.date, ep.heure"""
                )
            ]

            # Chronologie de garde à vue et signalements structurels : mêmes
            # calculs que ceux déjà utilisés pour 02_chronologie_procedure.docx
            # et 06_signalements_procedure.docx, réutilisés tels quels. Jamais
            # de qualification juridique ici non plus — ni "nullité" ni
            # "irrégularité" : seulement des délais chiffrés et des absences
            # structurelles, sourcés, à charge de l'avocat de qualifier.
            duree_garde_a_vue = None
            a_une_garde_a_vue = db.execute(
                "SELECT 1 FROM evenements_procedure WHERE nature = 'placement_garde_a_vue' AND statut_verif = 'verifie' LIMIT 1"
            ).fetchone()
            if a_une_garde_a_vue:
                duree_garde_a_vue = calculer_durees(db)

            signalements = [
                {
                    "titre": s.titre,
                    "description": s.description,
                    "page_reference": s.page_reference,
                    "citation_reference": s.citation_reference,
                }
                for s in detecter_signalements(db)
            ]

            # Confrontation : les mêmes points factuels abordés par des
            # personnes différentes, présentés côte à côte — jamais qualifiés
            # de "contradiction", l'avocat en juge lui-même à la lecture.
            lignes_decl = [
                dict(r)
                for r in db.execute(
                    """SELECT d.point_factuel, d.page, d.citation, p.nom AS personne
                       FROM declarations d LEFT JOIN personnes p ON p.id = d.personne_id
                       WHERE d.statut_verif = 'verifie'
                       ORDER BY d.point_factuel, p.nom"""
                )
            ]
            groupes_confrontation: dict[str, list[dict]] = {}
            for ligne in lignes_decl:
                groupes_confrontation.setdefault(ligne["point_factuel"], []).append(
                    {"personne": ligne["personne"], "page": ligne["page"], "citation": ligne["citation"]}
                )
            confrontations = [
                {"point_factuel": point_factuel, "declarations": decls}
                for point_factuel, decls in groupes_confrontation.items()
                if len({d["personne"] for d in decls if d["personne"]}) >= 2
            ]

            # Recoupement d'identifiants exacts (téléphone, plaque, IBAN,
            # adresse) sur l'ensemble du texte du dossier — entièrement
            # déterministe, complémentaire des confrontations ci-dessus qui
            # ne portent que sur les déclarations déjà extraites.
            recoupements = [
                {
                    "type_entite": e.type_entite,
                    "valeur": e.valeur,
                    "occurrences": [
                        {"page": o.page, "citation": o.citation, "valeur_brute": o.valeur_brute}
                        for o in e.occurrences
                    ],
                }
                for e in detecter_recoupements(db)
            ]
        finally:
            db.close()

    return {
        "resume": resume,
        "personnes": personnes,
        "chronologie_faits": faits,
        "chronologie_procedure": procedure,
        "duree_garde_a_vue": duree_garde_a_vue,
        "signalements": signalements,
        "confrontations": confrontations,
        "recoupements": recoupements,
    }


def _lister_fichiers_recursif(bucket, prefixe: str) -> list[str]:
    """Le Storage de Supabase ne liste qu'un niveau à la fois (les
    sous-dossiers apparaissent comme des entrées sans id) — on descend donc
    récursivement pour retrouver tous les fichiers réellement présents sous
    un préfixe, plutôt que de deviner leurs noms."""
    chemins: list[str] = []
    for entree in bucket.list(prefixe.rstrip("/") or None) or []:
        chemin = f"{prefixe}{entree['name']}"
        if entree.get("id") is None:
            chemins.extend(_lister_fichiers_recursif(bucket, chemin + "/"))
        else:
            chemins.append(chemin)
    return chemins


@app.delete("/api/dossiers/{dossier_id}", status_code=204)
def supprimer_dossier(dossier_id: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)) -> None:
    """Supprime un dossier : d'abord ses fichiers dans le Storage (source et
    résultats), puis sa ligne en base — jamais l'inverse, pour ne pas se
    retrouver avec des fichiers orphelins qu'aucune ligne ne référence plus
    et qu'on ne pourrait donc plus jamais retrouver pour les nettoyer."""
    supabase, utilisateur_id = contexte
    _dossier_ou_404(supabase, dossier_id)  # vérifie l'appartenance via la RLS

    service = client_service()
    try:
        for nom_bucket, prefixe in (
            ("dossiers-source", f"{utilisateur_id}/{dossier_id}/"),
            ("dossiers-resultats", f"{dossier_id}/"),
        ):
            bucket = service.storage.from_(nom_bucket)
            chemins = _lister_fichiers_recursif(bucket, prefixe)
            if chemins:
                bucket.remove(chemins)
    except Exception:  # noqa: BLE001 — un fichier orphelin dans le Storage est
        # rattrapable manuellement ; laisser un dossier bloqué dans la liste
        # de l'avocat parce que le nettoyage du Storage a échoué ne l'est pas.
        pass

    supabase.table("dossiers").delete().eq("id", dossier_id).execute()


@app.get("/api/dossiers/{dossier_id}/documents", response_model=list[str])
def lister_documents_transmis(dossier_id: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)) -> list[str]:
    """Noms des PDF sources tels que transmis par l'avocat pour ce dossier —
    lus directement depuis le Storage, jamais depuis une copie en base, et
    donc disponibles même pendant que le traitement est en cours."""
    supabase, utilisateur_id = contexte
    dossier = _dossier_ou_404(supabase, dossier_id)  # vérifie l'appartenance via la RLS
    prefixe = dossier.get("fichier_source_path") or f"{utilisateur_id}/{dossier_id}/"
    bucket = client_service().storage.from_("dossiers-source")
    chemins = _lister_fichiers_recursif(bucket, prefixe)
    return sorted(chemin[len(prefixe):] for chemin in chemins)


@app.get("/api/dossiers/{dossier_id}/documents/{nom_fichier}")
def telecharger_document_transmis(
    dossier_id: str, nom_fichier: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)
) -> dict:
    supabase, utilisateur_id = contexte
    dossier = _dossier_ou_404(supabase, dossier_id)  # vérifie l'appartenance via la RLS
    prefixe = dossier.get("fichier_source_path") or f"{utilisateur_id}/{dossier_id}/"
    bucket = client_service().storage.from_("dossiers-source")

    # nom_fichier vient du client : on ne construit le chemin Storage qu'à
    # partir d'un nom réellement listé sous le préfixe du dossier, jamais
    # directement, pour ne pas laisser un ".." ou un chemin absolu s'échapper
    # du dossier de cet avocat.
    noms_disponibles = {chemin[len(prefixe):] for chemin in _lister_fichiers_recursif(bucket, prefixe)}
    if nom_fichier not in noms_disponibles:
        raise HTTPException(status_code=404, detail="Document introuvable pour ce dossier.")

    try:
        reponse = bucket.create_signed_url(f"{prefixe}{nom_fichier}", 300)
        url = reponse.get("signedURL") or reponse.get("signedUrl")
    except Exception:  # noqa: BLE001
        url = None
    if not url:
        raise HTTPException(status_code=404, detail="Impossible de générer un lien pour ce document.")
    return {"url": url}


@app.get("/api/dossiers/{dossier_id}/livrables/{nom_fichier}")
def telecharger_livrable(
    dossier_id: str, nom_fichier: str, contexte: tuple[Client, str] = Depends(_contexte_utilisateur)
) -> dict:
    supabase, _ = contexte
    _dossier_ou_404(supabase, dossier_id)  # vérifie l'appartenance via la RLS

    if nom_fichier not in NOMS_LIVRABLES:
        raise HTTPException(status_code=404, detail="Livrable inconnu.")

    chemin = f"{dossier_id}/out/{nom_fichier}"
    try:
        reponse = client_service().storage.from_("dossiers-resultats").create_signed_url(chemin, 300)
        url = reponse.get("signedURL") or reponse.get("signedUrl")
    except Exception:  # noqa: BLE001
        url = None
    if not url:
        raise HTTPException(
            status_code=404,
            detail="Ce livrable n'a pas été généré pour ce dossier (le document ne contenait "
            "probablement aucune pièce de procédure pénale reconnue).",
        )
    return {"url": url}
