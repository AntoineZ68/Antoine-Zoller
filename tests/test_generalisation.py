"""Tests de généralisation ajoutés après un essai sur un dossier plus
réaliste (dates en toutes lettres, heures espacées, cotes avec tiret,
capitales sans accent, pièces qui se citent entre elles). Chaque cas ici
correspond à un bug réellement observé, pas à une anticipation théorique."""

from __future__ import annotations

import sqlite3

from depouille.chrono import RE_RETROACTIF, _personne_par_nom
from depouille.classify import _classifier_type_deterministe, _detecter_pieces_par_page, _entete_etendu, _est_titre
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


def test_entete_titre_apres_des_lignes_de_reference() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur : certains PV
    placent "N° Procédure", "Feuillet N° 1/2", "Date :", "Heure de début :"
    (casse mixte) AVANT le titre plutôt qu'après. Sans tolérance pour ces
    lignes de référence, la lecture de l'en-tête s'arrêtait avant d'atteindre
    "AUDITION", classant la pièce en "Non identifié" — et par ricochet,
    aucune déclaration Q/R n'était jamais extraite de cette pièce."""
    texte_page = (
        "DIRECTION CENTRALE DE LA POLICE JUDICIAIRE\n"
        "BRIGADE DES STUPÉFIANTS - LYON\n"
        "N° Procédure : 2026-LY-4521\n"
        "Feuillet N° 1/2\n"
        "Date : 02/09/2026\n"
        "Heure de début : 14h30\n"
        "PROCÈS-VERBAL D'AUDITION (GARDE À VUE)\n"
        "L'an deux mille vingt-six, le deux septembre à quatorze heures trente.\n"
    )
    entete = _entete_etendu(texte_page)
    assert "AUDITION" in entete.upper()
    type_, confiance = _classifier_type_deterministe(entete)
    assert type_ == "PV d'audition"
    assert confiance == 1.0


def test_entete_titre_pollue_par_colonne_voisine() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur : certains PV
    mettent le titre et les repères (feuillet, date, heure) sur deux
    colonnes ; pdfplumber, qui lit par position verticale, recolle les
    deux colonnes sur une même ligne physique
    ("DIRECTION ... POLICE JUDICIAIRE Feuillet N° 1/2"). Le fragment de
    droite, en casse mixte, empêchait de reconnaître la ligne de gauche
    comme un intitulé — et donc de détecter le début d'une nouvelle pièce,
    fusionnant deux PV en un seul."""
    assert _est_titre("DIRECTION CENTRALE DE LA POLICE JUDICIAIRE Feuillet N° 1/2")
    assert _est_titre("BRIGADE DES STUPÉFIANTS - LYON Date : 02/09/2026")

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE pages (numero_global INTEGER, texte TEXT)")
    db.executemany(
        "INSERT INTO pages VALUES (?, ?)",
        [
            (1, "PROCÈS-VERBAL DE SAISINE\nUn premier acte.\n"),
            (2, "Suite du premier acte, sans nouvel intitulé.\n"),
            (
                3,
                "DIRECTION CENTRALE DE LA POLICE JUDICIAIRE Feuillet N° 1/2\n"
                "BRIGADE DES STUPÉFIANTS - LYON Date : 02/09/2026\n"
                "N° Procédure : 2026-LY-4521 Heure de début : 14h30\n"
                "PROCÈS-VERBAL D'AUDITION (GARDE À VUE)\n"
                "L'an deux mille vingt-six...\n",
            ),
        ],
    )
    pages = db.execute("SELECT * FROM pages ORDER BY numero_global").fetchall()
    groupes = _detecter_pieces_par_page(pages)
    assert len(groupes) == 2
    assert groupes[1]["page_debut"] == 3


def test_frontiere_piece_apres_tampon_de_fax() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur : une page
    reçue par fax porte un tampon de transmission au-dessus du titre
    ("FAX FROM: ... -- PAGE 1/2"). Cette ligne contient des ":", exclus
    d'office du test de titre, donc regarder uniquement la toute première
    ligne de la page ratait le titre juste en dessous — fusionnant deux
    pièces distinctes (et par ricochet, perdant l'identification de la
    personne concernée par la première pièce)."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE pages (numero_global INTEGER, texte TEXT)")
    db.executemany(
        "INSERT INTO pages VALUES (?, ?)",
        [
            (1, "PROCÈS-VERBAL D'AUDITION\nUn premier acte.\n"),
            (
                2,
                "FAX FROM: COMMISSARIAT VENISSIEUX -- TO: PJ LYON STUPS -- DATE: 03/09/2026 09:42 -- PAGE 1/2\n"
                "PROCÈS-VERBAL D'AUDITION DE TÉMOIN\n"
                "(ARTICLE 62 DU CODE DE PROCÉDURE PÉNALE)\n"
                "L'an deux mille vingt-six...\n",
            ),
        ],
    )
    pages = db.execute("SELECT * FROM pages ORDER BY numero_global").fetchall()
    groupes = _detecter_pieces_par_page(pages)
    assert len(groupes) == 2
    assert groupes[1]["page_debut"] == 2


def test_personne_par_nom_ignore_lordre_nom_prenom() -> None:
    """Un PV écrit "BENALI Karim" (NOM Prénom) ; l'appel séparé qui extrait
    les faits narratifs reformule spontanément en "Karim Benali" — même
    personne, ordre différent. Le rapprochement doit fonctionner sans
    jamais créer de nouvelle personne ni matcher un nom sans rapport."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE personnes (id INTEGER PRIMARY KEY, nom TEXT, role TEXT)")
    db.execute("INSERT INTO personnes (nom, role) VALUES ('BENALI Karim', 'mis_en_cause')")

    assert _personne_par_nom(db, "Karim Benali") == 1
    assert _personne_par_nom(db, "BENALI Karim") == 1
    assert _personne_par_nom(db, "Karim Dupont") is None
    assert _personne_par_nom(db, "") is None


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
