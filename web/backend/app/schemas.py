from __future__ import annotations

from pydantic import BaseModel


class DossierResume(BaseModel):
    id: str
    nom: str
    reference: str | None = None
    statut: str
    etape_courante: str | None = None
    message_erreur: str | None = None
    nb_pages: int | None = None
    cree_le: str
    mis_a_jour_le: str


class EtapeTraitement(BaseModel):
    etape: str
    statut: str
    debut: str | None = None
    fin: str | None = None
    tokens_in: int
    tokens_out: int
    cout_usd: float
    message_erreur: str | None = None


class DossierDetail(DossierResume):
    etapes: list[EtapeTraitement] = []


class CouleursSurlignage(BaseModel):
    procedure: str
    declaration: str
    faits: str
