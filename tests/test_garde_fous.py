"""Garde-fous contre l'invention : tout élément identifiant d'un texte
rédigé par le modèle (heure, durée, nombre, nom, nature des faits, stade de
la procédure) doit figurer dans ses sources.

Les cas « inventés » viennent d'un vrai résumé produit par Mistral Large sur
le dossier de contrôle, que l'ancien contrôle laissait passer."""

from __future__ import annotations

import pytest

from depouille.garde_fous import contient_jugement, elements_absents, problemes_redaction

SOURCES = (
    "Julien MORVANNEC (mis_en_cause). Camille ARZANO (victime).\n"
    "Je suis arrivé sur les lieux du hangar de la rue des Tanneurs vers 22 heures.\n"
    "J'ai discuté avec Camille pendant une vingtaine de minutes puis je suis reparti à pied.\n"
    "Oui, nous nous connaissons depuis environ deux ans.\n"
    "Je suis arrivé sur les lieux vers 23 heures 30, pas avant.\n"
    "Interpellation effectuée le 14/03/2031 à 07h50. Perquisition de 09h00 à 09h40.\n"
    "Le 13/03/2031 à 21h10, présence du véhicule.\n"
    "Le 15/03/2031 à 08h10, prolongation pour une durée de vingt-quatre heures.\n"
    "Enquête ouverte à la suite des faits dénoncés par Camille ARZANO."
)


@pytest.mark.parametrize("texte, invente", [
    ("Il reconnaît être arrivé vers 21h00 au hangar.", "21h00"),
    ("Il dit avoir discuté une dizaine de minutes.", "dizaine"),
    ("Il la connaît depuis environ un an.", "un an"),
    ("Il la connaît depuis trois ans.", "trois ans"),
    ("Des faits de violences volontaires lui sont reprochés.", "violences"),
    ("Sous la qualification retenue par le réquisitoire.", "requisitoire"),
    ("Après la plainte de Camille ARZANO, une enquête est ouverte.", "plainte"),
    ("Il est mis en examen.", "mis en examen"),
    ("Julien MORVANNEC et Antoine ROQUIER sont en cause.", "ROQUIER"),
    ("Le témoin évoque une arrivée vers 22 heures 15.", "22h15"),
])
def test_element_invente_detecte(texte, invente) -> None:
    assert invente in elements_absents(texte, SOURCES)


@pytest.mark.parametrize("texte", [
    "Julien MORVANNEC déclare d'abord être arrivé vers 22 heures, puis vers 23h30.",
    "Il dit avoir discuté une vingtaine de minutes avec Camille ARZANO, qu'il connaît depuis 2 ans.",
    "Il a été interpellé le 14/03/2031 à 07h50. La perquisition a duré de 9h à 9h40.",
    "La garde à vue a été prolongée de 24 heures le 15/03/2031.",
    "Une enquête est ouverte. Lors de sa seconde audition, il rectifie l'heure.",
    "L'enquête fait suite à des faits dénoncés par Camille ARZANO.",
])
def test_reformulation_fidele_acceptee(texte) -> None:
    assert elements_absents(texte, SOURCES) == []


def test_entre_deux_et_trois_n_est_pas_cinq() -> None:
    assert elements_absents("Il y est resté entre deux et trois jours.", "entre deux et trois jours") == []


def test_mention_n_est_pas_un_mensonge() -> None:
    assert not contient_jugement("Mention de la poursuite des investigations sous commission rogatoire.")
    assert contient_jugement("Il a menti aux enquêteurs.")


@pytest.mark.parametrize("texte, probleme", [
    ("Entre mars 2031 et mars 2031, une enquête est ouverte.", "deux bornes"),
    ("Les faits se sont produits au [adresse].", "crochets"),
    ("La garde à vue est entachée de nullité.", "qualification"),
    ("Il avoue les faits.", "jugement"),
])
def test_problemes_redaction(texte, probleme) -> None:
    assert any(probleme in p for p in problemes_redaction(texte, texte))


def test_description_de_fait_inventee_remplacee_par_la_citation() -> None:
    from depouille.chrono import description_affichable

    piece = "Le 14/03/2031 à 08h05, nous procédons à l'interpellation de Julien MORVANNEC."
    citation = "nous procédons à l'interpellation de Julien MORVANNEC"
    assert description_affichable("Interpellation de Julien MORVANNEC.", citation, piece) == "Interpellation de Julien MORVANNEC."
    assert description_affichable("Interpellation de Julien MORVANNEC à 07h50.", citation, piece) == citation
    assert description_affichable("Julien MORVANNEC avoue les faits.", citation, piece) == citation
    assert description_affichable("", citation, piece) == citation


@pytest.mark.parametrize("texte", [
    "Julien MORVANNEC a été interpellé à deux heures différentes selon les procès-verbaux : à 07h50 et à 08h05 le 14/03/2031.",
    "Deux versions du véhicule sont rapportées : une Renault Clio grise et une Renault Clio blanche.",
])
def test_mots_de_comptage_acceptes(texte) -> None:
    """Observé sur le banc : les bonnes réponses, qui donnaient les deux
    versions du dossier, étaient écartées pour « deux »."""
    sources = (
        "Interpellation effectuée le 14/03/2031 à 07h50. Le 14/03/2031 à 08h05, procédons à "
        "l'interpellation de Julien MORVANNEC. Renault Clio de couleur grise. Renault Clio de "
        "couleur blanche. procès-verbaux"
    )
    assert elements_absents(texte, sources) == []
