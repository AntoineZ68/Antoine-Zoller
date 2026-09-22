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


class OccurrenceEntite(BaseModel):
    page: int
    citation: str
    valeur_brute: str


class EntiteCommune(BaseModel):
    type_entite: str
    valeur: str
    occurrences: list[OccurrenceEntite]


class SourcePage(BaseModel):
    """Référence de traçabilité d'UNE page du dossier fusionné : de quel
    fichier d'origine elle vient, à quelle page de ce fichier, sous quelle
    cote, et dans quelle pièce elle tombe.

    Exposée comme une table de correspondance page -> source plutôt que
    recopiée dans chaque élément extrait : tous les éléments (faits,
    évènements de procédure, signalements, déclarations, recoupements)
    portent déjà leur numéro de page, et peuvent donc résoudre leur propre
    référence. Ça évite d'élargir toutes les structures existantes — et ça
    couvre du même coup celles qui seront ajoutées plus tard."""

    page: int
    fichier_source: str
    page_fichier: int
    cote: str | None = None
    type_piece: str | None = None
    # Le texte reconnu sur cette page s'écarte trop de la forme d'un texte
    # écrit pour être tenu pour fidèle (page manuscrite, scan dégradé) : tout
    # ce qui en est extrait doit être relu sur le document d'origine.
    illisible: bool = False


class PieceIndex(BaseModel):
    """Une entrée de l'index du classeur : une pièce et son intervalle de
    pages dans le dossier fusionné."""

    type: str
    page_debut: int
    page_fin: int
    date: str | None = None
    heure: str | None = None
    cote: str | None = None
    fichier_source: str | None = None


class DonneesDossier(BaseModel):
    resume: str | None = None
    personnes: list[Personne] = []
    chronologie_faits: list[EvenementFait] = []
    chronologie_procedure: list[EvenementProcedure] = []
    duree_garde_a_vue: DureesGardeAVue | None = None
    signalements: list[Signalement] = []
    confrontations: list[PointConfrontation] = []
    recoupements: list[EntiteCommune] = []
    sources: list[SourcePage] = []
    index_pieces: list[PieceIndex] = []
