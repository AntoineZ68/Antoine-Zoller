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


class Personne(BaseModel):
    nom: str
    role: str


class EvenementFait(BaseModel):
    page: int
    citation: str
    description: str
    personne: str | None = None
    date: str | None = None
    heure: str | None = None


class EvenementProcedure(BaseModel):
    date: str | None = None
    heure: str | None = None
    nature: str
    page: int
    citation: str
    personne: str | None = None


class DureesGardeAVue(BaseModel):
    duree_totale_garde_a_vue: str
    delai_placement_notification_droits: str
    delai_demande_realisation_examen_medical: str
    delai_demande_realisation_entretien_avocat: str


class Signalement(BaseModel):
    titre: str
    description: str
    page_reference: int | None = None
    citation_reference: str | None = None


class DeclarationConfrontation(BaseModel):
    personne: str | None = None
    page: int
    citation: str


class PointConfrontation(BaseModel):
    point_factuel: str
    declarations: list[DeclarationConfrontation]


class DonneesDossier(BaseModel):
    resume: str | None = None
    personnes: list[Personne] = []
    chronologie_faits: list[EvenementFait] = []
    chronologie_procedure: list[EvenementProcedure] = []
    duree_garde_a_vue: DureesGardeAVue | None = None
    signalements: list[Signalement] = []
    confrontations: list[PointConfrontation] = []
