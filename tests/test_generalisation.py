"""Tests de généralisation ajoutés après un essai sur un dossier plus
réaliste (dates en toutes lettres, heures espacées, cotes avec tiret,
capitales sans accent, pièces qui se citent entre elles). Chaque cas ici
correspond à un bug réellement observé, pas à une anticipation théorique."""

from __future__ import annotations

import sqlite3

from depouille.chrono import RE_RETROACTIF, _extraire_evenements_piece, _personne_par_nom
from depouille.classify import (
    _classifier_type_deterministe,
    _detecter_pieces_par_page,
    _entete_etendu,
    _est_titre,
    _premiere_mention_hors_titres,
    _premiere_mention_ou_creation,
    _upsert_personne,
)
from depouille.regex_patterns import (
    _normaliser_heure_libre,
    detecter_cote,
    detecter_date_acte,
    detecter_date_heure_acte,
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


def test_formule_ouverture_annee_separee_toutes_lettres() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur : la formule
    d'ouverture la plus répandue des PV français déclare l'année une fois
    ("L'an deux mille vingt-six") puis le jour et le mois SANS année
    accolée ("le deux septembre à six heures et quinze minutes") — jamais
    reconnue par l'ancien motif, qui exige une date complète en un bloc."""
    texte = (
        "L'An deux mille vingt-six, le deux septembre à six heures et quinze minutes.\n"
        "Nous, Capitaine Martin DUPONT, Officier de Police Judiciaire..."
    )
    assert detecter_date_heure_acte(texte) == ("02/09/2026", "06h15")


def test_formule_ouverture_heure_sans_minutes() -> None:
    texte = "L'an deux mille vingt-six, le trois septembre à neuf heures.\nDevant nous, Lieutenant BERNIER Lucas..."
    assert detecter_date_heure_acte(texte) == ("03/09/2026", "09h00")


def test_formule_ouverture_retrocompatible_avec_date_chiffree() -> None:
    """L'ancienne écriture ("Le 12 novembre 2024 à 09 h 45") doit continuer
    à fonctionner via la nouvelle fonction combinée."""
    texte = "Le 12 novembre 2024 à 09 h 45, nous notifions la mesure."
    assert detecter_date_heure_acte(texte) == ("12/11/2024", "09h45")


def test_formule_ouverture_absente_ne_renvoie_rien() -> None:
    assert detecter_date_heure_acte("Un texte quelconque sans aucune formule de date.") == (None, None)


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


def test_reconnait_les_pieces_documentaires_saisies() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur (affaire
    financière) : un dossier pénal économique repose souvent davantage sur
    des pièces saisies comme preuve (factures, relevés bancaires) que sur
    des PV classiques — sans catégorie ni règle dédiées, ces pièces
    tombaient systématiquement en "Non identifié", qu'elles soient
    reconnaissables ou non."""
    entete_facture_fr = _entete_etendu("FACTURE D'INTERVENTION N° F-25-884\nALSACE CAFÉ PRO SERVICES\n")
    assert _classifier_type_deterministe(entete_facture_fr) == ("Facture", 1.0)

    entete_facture_en = _entete_etendu("KRONOS CONSULTING LTD INVOICE\nStrategic Management Advisory\n")
    assert _classifier_type_deterministe(entete_facture_en) == ("Facture", 1.0)

    entete_releve = _entete_etendu("EXTRAIT DE COMPTE COURANT PROFESSIONNEL\nPériode du 01/09/2025 au 30/09/2025\n")
    assert _classifier_type_deterministe(entete_releve) == ("Relevé bancaire", 1.0)


def test_entete_titre_apres_champ_libre_non_enumere() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur : la ligne
    "Officier : Capitaine MOREL David, OPJ" arrêtait la lecture de l'en-tête
    juste avant "PROCÈS-VERBAL D'AUDITION..." car "Officier" ne figurait pas
    dans la liste énumérée de mots-clés de champs (N°, Feuillet, Date,
    Heure, Procédure) — aucune énumération de libellés de champ ne peut
    être exhaustive face à la variété des PV réels (Rédacteur, Enquêteur,
    Unité, Grade...) : on reconnaît maintenant n'importe quel champ de la
    forme "Libellé : valeur" plutôt qu'une liste figée."""
    texte_page = (
        "DIRECTION ZONALE DE LA POLICE NATIONALE EST\n"
        "Procédure N° : 2026/DEF/8904\n"
        "Date : 12 Octobre 2026\n"
        "Heure : 10h15\n"
        "Officier : Capitaine MOREL David, OPJ\n"
        "PROCÈS-VERBAL D'AUDITION DE GARDE À VUE\n"
        "L'an deux mille vingt-six, le douze octobre à dix heures quinze.\n"
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
    db.execute("CREATE TABLE pages (numero_global INTEGER, fichier_source TEXT, texte TEXT)")
    db.executemany(
        "INSERT INTO pages VALUES (?, ?, ?)",
        [
            (1, "dossier.pdf", "PROCÈS-VERBAL DE SAISINE\nUn premier acte.\n"),
            (2, "dossier.pdf", "Suite du premier acte, sans nouvel intitulé.\n"),
            (
                3,
                "dossier.pdf",
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


def test_frontiere_piece_a_chaque_nouveau_fichier_source() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur (affaire
    financière à plusieurs PDF) : un relevé bancaire et un e-mail saisi,
    envoyés comme deux fichiers PDF distincts, se retrouvaient fusionnés en
    une seule pièce — l'e-mail ("De :", "Envoyé :", en casse mixte) ne
    contient aucun intitulé en capitales sur ses premières lignes. Le type,
    la date apparente et les citations de l'un se retrouvaient alors
    attribués à l'autre. Un changement de fichier source doit à lui seul
    déclencher une nouvelle pièce, même sans intitulé reconnu."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE pages (numero_global INTEGER, fichier_source TEXT, texte TEXT)")
    db.executemany(
        "INSERT INTO pages VALUES (?, ?, ?)",
        [
            (
                1,
                "releve_cic.pdf",
                "CIC EST\nAGENCE STRASBOURG KLÉBER\nEXTRAIT DE COMPTE COURANT PROFESSIONNEL\n",
            ),
            (
                2,
                "email_saisi.pdf",
                "De: Marc Vandal <m.vandal@bati-est.fr>\n"
                "Envoyé: Jeudi 4 septembre 2025 18:42\n"
                "À: Sylvie RENAUD (Comptabilité)\n",
            ),
        ],
    )
    pages = db.execute("SELECT * FROM pages ORDER BY numero_global").fetchall()
    groupes = _detecter_pieces_par_page(pages)
    assert len(groupes) == 2
    assert groupes[0]["page_debut"] == 1 and groupes[0]["page_fin"] == 1
    assert groupes[1]["page_debut"] == 2 and groupes[1]["page_fin"] == 2


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
    db.execute("CREATE TABLE pages (numero_global INTEGER, fichier_source TEXT, texte TEXT)")
    db.executemany(
        "INSERT INTO pages VALUES (?, ?, ?)",
        [
            (1, "dossier.pdf", "PROCÈS-VERBAL D'AUDITION\nUn premier acte.\n"),
            (
                2,
                "dossier.pdf",
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


def test_premiere_mention_ignore_lavocat_honorifique_abrege() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur (escroquerie
    Colmar) : "Me SCHMITT" (l'avocat de la défense, mentionné en passant)
    matchait le même motif Prénom-NOM que "Jean-Marc TARDIEU" — "Me" a une
    majuscule suivie d'une minuscule, "SCHMITT" est tout en capitales.
    Résultat : l'avocat était pris pour le mis en cause. "TARDIEU
    Jean-Marc" lui-même, écrit NOM Prénom dans un champ encadré
    ("Personne : ..."), n'était pas reconnu du tout par l'ancien motif —
    les deux bugs se combinaient pour identifier la mauvaise personne."""
    texte = (
        "Personne : TARDIEU Jean-Marc\n"
        "Informons M. TARDIEU de son placement en garde à vue ce jour à 07h00.\n"
        "Demande à s'entretenir avec un avocat (Me SCHMITT). Avocat avisé à 11h50.\n"
    )
    assert _premiere_mention_hors_titres(texte) == ("Jean-Marc", "TARDIEU")


def test_date_acte_reconnue_dans_un_champ_encadre() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur (escroquerie
    Colmar) : certains PV donnent la date/l'heure de l'acte dans un champ
    encadré en tête de document ("Date de placement : 15 Octobre 2026 à
    07h00") plutôt que dans la formule de prose "Le [date] à [heure]" —
    seule forme reconnue jusqu'ici. Sans ce troisième motif, l'heure de
    placement en garde à vue n'était jamais détectée, et par ricochet
    aucune durée ni aucun signalement n'était jamais calculé."""
    assert detecter_date_heure_acte("Date de placement : 15 Octobre 2026 à 07h00") == ("15/10/2026", "07h00")
    assert detecter_date_heure_acte("Date et Heure : 15 Octobre 2026 à 06h15") == ("15/10/2026", "06h15")
    # Une date de naissance n'est jamais la date de l'acte lui-même.
    assert detecter_date_heure_acte("Date de naissance : 14/05/1972") == (None, None)


def test_heure_en_chiffres_suivie_du_mot_heures_minutes() -> None:
    """Régression sur un vrai dossier testé par l'utilisateur (Lyon,
    BENALI Sofiane) : "17 heures 52 minutes" (heure/minute en chiffres,
    mais avec les mots "heures"/"minutes" plutôt que l'abréviation "h")
    n'était reconnue ni par normaliser_heure (qui exige la lettre "h"), ni
    par la conversion toutes-lettres (qui exige un nombre entièrement en
    toutes lettres, ex. "dix-sept heures") — un troisième format, tout
    aussi courant en style administratif français."""
    texte = "L'an deux mille vingt-six, le 14 septembre, à 19 heures 45 minutes.\nNous, Gilles GAUTHIER..."
    assert detecter_date_heure_acte(texte) == ("14/09/2026", "19h45")


def test_ouverture_lan_ne_cede_pas_a_une_mention_non_liee_plus_bas() -> None:
    """Régression exacte sur le dossier Lyon : la pièce ne réutilisait pas
    la fonction centrale de détection d'ouverture (3 formules) pour les
    évènements de procédure simples, mais un motif local ne couvrant que
    "Le [date] à [heure]" — en l'absence de correspondance pour "L'an ...",
    ce motif tombait alors sur une mention non liée plus bas sur la même
    page ("Avis donné à Madame la Procureure ... le 14 septembre 2026 à
    20h15"), attribuant à tort cette heure au placement en garde à vue."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE pieces (id INTEGER, type TEXT, personne_principale_id INTEGER)")
    db.execute("INSERT INTO pieces VALUES (1, 'PV de notification de placement en garde à vue', 1)")
    piece = db.execute("SELECT * FROM pieces").fetchone()

    db.execute("CREATE TABLE pages (numero_global INTEGER, texte TEXT)")
    texte_page = (
        "PROCÈS-VERBAL DE PLACEMENT EN GARDE À VUE ET NOTIFICATION DES DROITS\n"
        "L'an deux mille vingt-six, le 14 septembre, à 19 heures 45 minutes.\n"
        "Nous, Gilles GAUTHIER, Capitaine de Police, Officier de Police Judiciaire.\n"
        "Informons l'intéressé qu'il est placé sous le régime de la GARDE À VUE à "
        "compter du 14 septembre 2026 à 17 heures 52 minutes (heure de son "
        "interpellation effective), pour une durée de 24 heures.\n"
        "Avis donné à Madame la Procureure de la République adjointe le 14 "
        "septembre 2026 à 20h15 par voie électronique et téléphonique.\n"
    )
    db.execute("INSERT INTO pages VALUES (1, ?)", (texte_page,))
    pages = db.execute("SELECT * FROM pages").fetchall()

    evenements = _extraire_evenements_piece(db, piece, pages)
    placement = next(e for e in evenements if e["nature"] == "placement_garde_a_vue")
    assert placement["heure"] == "17h52"
    assert "avis" not in placement["citation"].lower()


def test_retroactivite_sans_le_mot_retroactivement() -> None:
    """"à compter du [date] à [heure] (heure de son interpellation
    effective)" exprime le même effet rétroactif (art. 63 CPP) que
    "rétroactivement", sans jamais employer ce mot — la formulation
    réellement rencontrée sur le dossier Lyon."""
    texte = (
        "il est placé sous le régime de la GARDE À VUE à compter du 14 septembre "
        "2026 à 17 heures 52 minutes (heure de son interpellation effective), pour "
        "une durée de 24 heures."
    )
    m = RE_RETROACTIF.search(texte)
    assert m is not None
    assert normaliser_date(m.group(1)) == "14/09/2026"


def test_officier_redacteur_non_pris_pour_le_mis_en_cause() -> None:
    """Régression sur le dossier Lyon : "Nous, Gilles GAUTHIER, Capitaine
    de Police..." est la formule d'auto-présentation universelle de
    l'officier rédacteur — le titre suit le nom, séparé par une virgule,
    au lieu de le précéder ("Capitaine GAUTHIER"). L'ancienne exclusion ne
    vérifiait que le mot précédent, jamais le mot suivant : l'officier
    était pris pour le mis en cause."""
    texte = (
        "Nous, Gilles GAUTHIER, Capitaine de Police, Officier de Police Judiciaire.\n"
        "Constatons la présence de la personne dénommée : BENALI Sofiane, né le "
        "12/05/2001 à Lyon 4e.\n"
    )
    assert _premiere_mention_hors_titres(texte) == ("Sofiane", "BENALI")


def test_personne_denommee_reconnue_nom_prenom() -> None:
    """"la personne dénommée : NOM Prénom" est une formule aussi standard
    que le champ encadré "Personne : ..." pour donner l'identité dans
    l'ordre NOM Prénom — observée sur le dossier Lyon, sans le mot
    "Personne" ni les deux-points en tête de ligne qu'exige RE_PERSONNE_CHAMP."""
    texte = "Constatons la présence dans nos locaux de la personne dénommée : BENALI Sofiane, né le 12/05/2001."
    assert _premiere_mention_hors_titres(texte) == ("Sofiane", "BENALI")


def test_deux_colonnes_ne_cree_pas_une_fausse_personne() -> None:
    """Régression exacte sur le dossier Lyon : un document à deux colonnes
    juxtapose sur une même ligne physique la fin d'un bloc d'adresse
    ("...Cabinet du Procureur de la République") et le titre du PV qui
    suit ("PROCÈS-VERBAL DE PLACEMENT...") — recollés par pdfplumber,
    "République" (majuscule-minuscules) suivi d'un mot tout en capitales
    matchait par erreur le motif Prénom-NOM, créant une fausse personne
    "République PROCÈS-VERBAL". _premiere_mention_ou_creation doit
    débarrasser le texte de ses titres avant de chercher une personne."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE personnes (id INTEGER PRIMARY KEY, nom TEXT, role TEXT)")
    texte_page = (
        "PROCÉDURE N° 2026/00482 Cabinet du Procureur de la République\n"
        "PROCÈS-VERBAL DE PLACEMENT EN GARDE À VUE ET NOTIFICATION DES DROITS\n"
        "L'an deux mille vingt-six, le 14 septembre, à 19 heures 45 minutes.\n"
        "Nous, Gilles GAUTHIER, Capitaine de Police.\n"
        "Constatons la présence de la personne dénommée : BENALI Sofiane, né le "
        "12/05/2001 à Lyon 4e.\n"
    )
    personne_id = _premiere_mention_ou_creation(db, texte_page, "mis_en_cause")
    assert personne_id is not None
    nom = db.execute("SELECT nom FROM personnes WHERE id = ?", (personne_id,)).fetchone()["nom"]
    assert nom == "Sofiane BENALI"


def test_placement_et_notification_droits_dans_le_meme_pv() -> None:
    """Régression sur le même dossier : le placement en garde à vue et la
    notification (différée) des droits sont souvent racontés dans UN SEUL
    PV, pas deux pièces distinctes — jusqu'ici seul le placement était
    extrait, le délai de notification (souvent le point le plus
    significatif du dossier) n'était jamais calculé. La notification ne
    répète pas la date ("n'a pu lui être faite qu'à 11h45") : celle du
    placement s'applique, le même jour."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE pieces (id INTEGER, type TEXT, personne_principale_id INTEGER)")
    db.execute("INSERT INTO pieces VALUES (1, 'PV de notification de placement en garde à vue', 1)")
    piece = db.execute("SELECT * FROM pieces").fetchone()

    db.execute("CREATE TABLE pages (numero_global INTEGER, texte TEXT)")
    texte_page = (
        "PROCÈS-VERBAL DE PLACEMENT EN GARDE À VUE\n"
        "Personne : TARDIEU Jean-Marc\n"
        "Date de placement : 15 Octobre 2026 à 07h00\n"
        "Vu les articles 62-2 et suivants du Code de Procédure Pénale.\n"
        "Informons M. TARDIEU de son placement en garde à vue ce jour à 07h00.\n"
        "NOTIFICATION DES DROITS :\n"
        "En raison de la rédaction des actes de perquisition, du transport vers le "
        "commissariat et de l'encombrement des cellules, la notification effective des "
        "droits (droit au silence, droit à un avocat, droit à un médecin) n'a pu lui "
        "être faite qu'à 11h45.\n"
    )
    db.execute("INSERT INTO pages VALUES (1, ?)", (texte_page,))
    pages = db.execute("SELECT * FROM pages").fetchall()

    evenements = _extraire_evenements_piece(db, piece, pages)
    par_nature = {e["nature"]: e for e in evenements}

    assert par_nature["placement_garde_a_vue"]["date"] == "15/10/2026"
    assert par_nature["placement_garde_a_vue"]["heure"] == "07h00"
    assert par_nature["notification_droits"]["date"] == "15/10/2026"
    assert par_nature["notification_droits"]["heure"] == "11h45"


def test_suspect_qui_se_nomme_prime_sur_victime_dite_denommee() -> None:
    """Régression sur le dossier Lyon : un PV d'interpellation mentionne
    souvent, en plus du suspect, une victime ou un tiers en passant
    ("les effets personnels d'une dénommée DUPONT Julie") — alors que le
    suspect s'est identifié bien plus tôt dans le texte, mais via "déclare
    se nommer verbalement", pas "dénommé(e)". Sans unifier les deux
    formulations dans un seul motif cherché par position, "dénommée"
    l'emportait toujours quelle que soit sa position dans le texte, et la
    victime héritait par erreur du rôle "mis_en_cause" par défaut de cette
    pièce."""
    texte = (
        "L'individu déclare se nommer verbalement : BENALI Sofiane, né le 12 mai 2001 "
        "à Lyon 4e, sans domicile stable déclaré, sans emploi.\n"
        "Trouvons en sa possession un sac à main contenant les effets personnels "
        "d'une dénommée DUPONT Julie.\n"
    )
    assert _premiere_mention_hors_titres(texte) == ("Sofiane", "BENALI")


def test_upsert_personne_ne_duplique_pas_sur_role_different() -> None:
    """Régression sur le dossier Lyon : "Julie DUPONT" apparaissait deux
    fois dans l'interface — une fois comme "Client (mis en cause)" (pièce
    d'interpellation mal ciblée, voir le test précédent) et une fois comme
    "Victime" (sa propre audition, correctement identifiée par le modèle).
    _upsert_personne cherchait par (nom, rôle), pas par nom seul : deux
    rôles différents pour la même personne créaient deux lignes distinctes
    plutôt qu'une seule — un avocat qui voit un nom cité deux fois avec
    deux rôles croit à deux personnes réelles différentes."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE personnes (id INTEGER PRIMARY KEY, nom TEXT, role TEXT)")

    id_mis_en_cause = _upsert_personne(db, "Julie DUPONT", "mis_en_cause")
    id_victime = _upsert_personne(db, "Julie DUPONT", "victime")

    assert id_mis_en_cause == id_victime
    assert db.execute("SELECT COUNT(*) FROM personnes").fetchone()[0] == 1


def test_heure_en_chiffres_sans_le_mot_minutes() -> None:
    """"14 heures 00" (minutes à zéro) omet souvent le mot "minutes" —
    contrairement à "17 heures 52 minutes" où il est présent. Sans le
    rendre facultatif, cette écriture pourtant très répandue n'était
    reconnue par aucune des fonctions de normalisation d'heure."""
    assert _normaliser_heure_libre("14 heures 00") == "14h00"
    assert _normaliser_heure_libre("9 heures") == "09h00"
    assert _normaliser_heure_libre("17 heures 52 minutes") == "17h52"


def test_formule_le_date_a_heure_tolere_heure_en_toutes_lettres() -> None:
    """Régression exacte sur le dossier Lyon : un rapport de synthèse
    ouvre sur "Le 15 septembre 2026 à 14 heures 00." (heure en toutes
    lettres, sans le mot "minutes"). Le deuxième palier de
    detecter_ouverture_acte n'acceptait que l'heure compacte ("14h00") —
    faute de correspondance sur la vraie ouverture de l'acte, il retombait
    plus bas dans la même page sur "Le 14 septembre 2026 à 17h35, Mme
    DUPONT Julie a été victime...", une date de fait divers racontée dans
    le corps du texte, sans aucun rapport avec la date du rapport
    lui-même. Résultat en réel : tous les faits de cette pièce héritaient
    de la date/heure du fait divers plutôt que de la date du rapport."""
    texte = (
        "1. FAITS ET PROCÉDURE :\n"
        "Le 15 septembre 2026 à 14 heures 00.\n"
        "Rapport d'enquête rédigé par le Capitaine de Police G. GAUTHIER, OPJ.\n"
        "Le 14 septembre 2026 à 17h35, Mme DUPONT Julie a été victime d'une "
        "agression physique violente cours Gambetta / place Péri à Lyon 7e."
    )
    assert detecter_date_heure_acte(texte) == ("15/09/2026", "14h00")
