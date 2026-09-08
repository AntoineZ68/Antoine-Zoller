"""Tests de généralisation ajoutés après un essai sur un dossier plus
réaliste (dates en toutes lettres, heures espacées, cotes avec tiret,
capitales sans accent, pièces qui se citent entre elles). Chaque cas ici
correspond à un bug réellement observé, pas à une anticipation théorique."""

from __future__ import annotations

from depouille.chrono import RE_RETROACTIF
from depouille.classify import _classifier_type_deterministe, _entete_etendu
from depouille.regex_patterns import (
    detecter_cote,
    detecter_date_acte,
    normaliser_date,
    normaliser_heure,
    trouver_heures,
)


def test_normaliser_date_accepte_le_format_chiffres() -> None:
    assert normaliser_date("14/03/2031") == "14/03/2031"
    assert normaliser_date("4/3/2031") == "04/03/2031"


def test_normaliser_date_accepte_les_mois_en_lettres() -> None:
    assert normaliser_date("14 novembre 2024") == "14/11/2024"
    assert normaliser_date("1er mai 2024") == "01/05/2024"
    assert normaliser_date("3 août 2024") == "03/08/2024"


def test_normaliser_date_renvoie_none_si_non_reconnue() -> None:
    assert normaliser_date("un jour de la semaine dernière") is None


def test_normaliser_heure_tolere_les_espaces() -> None:
    assert normaliser_heure("16 h 45") == "16h45"
    assert normaliser_heure("08h15") == "08h15"
    assert normaliser_heure("6h05") == "06h05"


def test_trouver_heures_avec_espaces() -> None:
    assert trouver_heures("Le rendez-vous est fixé à 09 h 45 puis à 14h30.") == ["09h45", "14h30"]


def test_detecter_date_acte_avec_mois_en_lettres() -> None:
    texte = "Le 12 novembre 2024 à 09 h 45, nous notifions la mesure."
    assert detecter_date_acte(texte) == "12/11/2024"


def test_detecter_cote_avec_tiret_et_capitales() -> None:
    """"COTE D-012" (majuscules, tiret) doit être reconnu tout comme "Cote D0012"."""
    assert detecter_cote("COTE D-012 / PV INTERROGATOIRE") == "D012"
    assert detecter_cote("Cote D0012 — Page 7") == "D0012"


def test_classification_insensible_aux_accents_manquants() -> None:
    """Les capitales françaises omettent souvent les accents en pratique :
    "GARDE A VUE" doit être reconnu comme "GARDE À VUE"."""
    entete = "COTE C-022 / PROCES-VERBAL DE PROLONGATION DE LA GARDE A VUE"
    type_, confiance = _classifier_type_deterministe(entete)
    assert type_ == "PV de prolongation de garde à vue"
    assert confiance == 1.0


def test_classification_ne_regarde_que_lentete_pas_le_corps() -> None:
    """Régression : une pièce qui *mentionne* un autre acte en passant dans
    son corps de texte ("le mandat de dépôt a été requis par réquisitoire
    introductif...") ne doit pas être classée sous ce mot-clé — seul
    l'intitulé compte."""
    texte_page = (
        "COTE D-012 / PV INTERROGATOIRE DE PREMIERE COMPARUTION\n"
        "TRIBUNAL JUDICIAIRE DE LILLE\n"
        "Ce jour, le 14 novembre 2024 à 16 h 45.\n"
        "Mentionnons que le mandat de dépôt a été requis par réquisitoire "
        "introductif en date du 14 novembre 2024.\n"
    )
    entete = _entete_etendu(texte_page)
    assert "REQUISITOIRE" not in entete.upper()
    type_, confiance = _classifier_type_deterministe(entete)
    assert type_ == "PV d'interrogatoire de première comparution"


def test_retroactivite_garde_a_vue_detectee() -> None:
    """Quand la mesure de garde à vue prend effet rétroactivement à l'heure
    de l'interpellation (art. 63 CPP), c'est cette heure-là qu'il faut
    retenir comme point de départ réel — pas l'heure de rédaction du PV de
    notification, plus tardive."""
    texte = (
        "Lui indiquons que cette mesure prend effet rétroactivement à "
        "compter de son interpellation, soit le 12 novembre 2024 à 06 h 18."
    )
    m = RE_RETROACTIF.search(texte)
    assert m is not None
    assert normaliser_date(m.group(1)) == "12/11/2024"
    assert normaliser_heure(m.group(2)) == "06h18"


def test_notification_de_mesure_reconnue_comme_placement() -> None:
    """Un dossier réel a phrasé cet acte "NOTIFICATION DE MESURE DE GARDE A
    VUE" plutôt que "NOTIFICATION DE PLACEMENT..." (le mot de mon jeu
    d'essai) : les deux doivent être reconnus."""
    entete = "COTE C-010 / PROCES-VERBAL DE NOTIFICATION DE MESURE DE GARDE A VUE"
    type_, _ = _classifier_type_deterministe(entete)
    assert type_ == "PV de notification de placement en garde à vue"
