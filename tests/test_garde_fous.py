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


def test_description_de_fait_inventee_retiree() -> None:
    """Retirée, l'interface affiche la citation seule, entre guillemets."""
    from depouille.chrono import description_affichable

    piece = "Le 14/03/2031 à 08h05, nous procédons à l'interpellation de Julien MORVANNEC."
    assert description_affichable("Interpellation de Julien MORVANNEC.", piece) == "Interpellation de Julien MORVANNEC."
    assert description_affichable("Interpellation de Julien MORVANNEC à 07h50.", piece) == ""
    assert description_affichable("Julien MORVANNEC avoue les faits.", piece) == ""


DISCORDANCES = [
    ("Interpellation de Julien MORVANNEC", ["07h50", "08h05"]),
    ("Plaque d'immatriculation", ["GH-428-KL", "GH-482-KL"]),
]


@pytest.mark.parametrize("texte, tue", [
    ("Julien MORVANNEC a été interpellé le 14/03/2031 à 07h50.", True),
    ("Julien MORVANNEC a été interpellé à 07h50 selon un PV, à 08h05 selon un autre.", False),
    ("Interpellé à 7 h 50 ou à 8 h 05 selon les pièces.", False),
    ("Le véhicule immatriculé GH 428 KL est stationné devant le domicile.", True),
    ("Il a été placé en garde à vue à 08h15.", False),
])
def test_version_unique_d_une_discordance_signalee(texte, tue) -> None:
    from depouille.garde_fous import versions_tues

    assert bool(versions_tues(texte, DISCORDANCES)) is tue


def test_nom_complete_seulement_s_il_est_deja_en_partie_dans_la_reference() -> None:
    from depouille.garde_fous import avec_noms_completes

    noms = ["Camille ARZANO", "Lucas MARTINON", "Pierre DUVAL"]
    reference = avec_noms_completes("J'ai discuté avec Camille. Une pierre a été lancée.", noms)
    assert elements_absents("Il a discuté avec Camille ARZANO.", reference) == []
    assert "MARTINON" in elements_absents("Il a discuté avec Lucas MARTINON.", reference), "aucune partie du nom n'y figure"
    assert "DUVAL" in elements_absents("Pierre DUVAL a lancé une pierre.", reference), "« pierre » n'y est pas un nom propre"


def test_coups_est_un_fait_repréhensible_un_jugement() -> None:
    """Observé : « coups » exigé dans les sources poussait le modèle vers
    « chacun attribue les actes répréhensibles à l'autre »."""
    assert "coups" not in elements_absents("Chacun attribue les coups à l'autre.", "Kevin a frappé le livreur.")
    assert contient_jugement("Chacun attribue les actes répréhensibles à l'autre.")
