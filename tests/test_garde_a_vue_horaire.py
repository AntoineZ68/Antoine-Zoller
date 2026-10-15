"""Actes de garde à vue horodatés sans date (« A 07h30, nous notifions… »),
tels qu'écrits dans un dossier de test de 30 pages : avant, aucun acte
n'était reconnu — ni durée, ni délai, ni signalement. Textes repris du
dossier, lus par l'OCR."""

from __future__ import annotations

import pytest
from rich.console import Console

from depouille.chrono import lancer_chrono
from depouille.config import Config
from depouille.conformite import detecter_signalements
from depouille.contradictions import contradictions_par_regles
from depouille.db import ouvrir_db
from depouille.gardes_a_vue import gardes_a_vue

PIECES = [
    ("PV de saisine", "D.02 - PROCÈS VERBAL DE SAISINE\nL'an deux mille vingt-six, Nous, Officier de Police "
     "Judiciaire, rapportons les faits suivants : Ce jour à\n06h00, nous recevons un appel téléphonique anonyme."),
    ("PV d'interpellation", "D.07 - PROCÈS VERBAL D'INTERPELLATION\nA 05h52, nous pénétrons en force dans "
     "l'appartement situé au 3ème étage. Le nommé Kamel BENALI est immédiatement maîtrisé."),
    ("PV de notification de placement en garde à vue", "D.08 - PLACEMENT EN GARDE À VUE\nA 07h30, de retour au "
     "service, nous notifions formellement au nommé Kamel BENALI son placement en garde à vue. A 07h35, nous lui "
     "notifions ses droits (médecin, avocat, famille, silence)."),
    ("Pièce de procédure – autre", "D.09 - AVIS AVOCAT ET MAGISTRAT\nA 07h42, nous contactons la permanence du "
     "cabinet de Maître X par téléphone pour l'informer de la mesure de garde à vue. A 07h50, nous avisons Monsieur "
     "le Procureur de la République du placement en garde à vue."),
    ("PV d'audition", "D.21 - PROCÈS VERBAL D'AUDITION DE M. BENALI\nA 08h50, nous extrayons le gardé à vue de sa "
     "cellule. Constatons que l'avocat n'est toujours pas présent au service à cette heure."),
]


@pytest.fixture
def db(tmp_path):
    db = ouvrir_db(tmp_path / "t.db")
    db.execute("INSERT INTO personnes (id, nom, role) VALUES (1, 'Kamel BENALI', 'mis_en_cause')")
    for numero, (type_, texte) in enumerate(PIECES, start=1):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) VALUES (?, 'd.pdf', ?, ?, ?)",
            (numero, numero, texte, str(numero)),
        )
        db.execute(
            "INSERT INTO pieces (id, type, page_debut, page_fin, cote, personne_principale_id) VALUES (?, ?, ?, ?, ?, ?)",
            (numero, type_, numero, numero, f"D{numero:02d}", None if numero == 1 else 1),
        )
    db.commit()
    lancer_chrono(db, Config(offline=True), force=False, console=Console(quiet=True))
    return db


def test_actes_horodates_reconnus(db) -> None:
    actes = {(r["nature"], r["heure"]) for r in db.execute("SELECT nature, heure FROM evenements_procedure WHERE statut_verif = 'verifie'")}
    assert actes >= {
        ("interpellation", "05h52"), ("placement_garde_a_vue", "07h30"), ("notification_droits", "07h35"),
        ("avis_avocat", "07h42"), ("avis_parquet", "07h50"), ("debut_audition", "08h50"),
    }


def test_garde_a_vue_sur_une_meme_journee_quand_rien_n_est_date(db) -> None:
    [gav] = gardes_a_vue(db)
    assert gav["heures_sans_date"]
    assert gav["delai_interpellation_placement_minutes"] == 98
    assert gav["delai_notification_minutes"] == 5
    assert gav["delai_avis_parquet_minutes"] == 118, "depuis l'interpellation, début de la privation de liberté"
    assert gav["delai_avis_avocat_audition_minutes"] == 68


def test_audition_moins_de_deux_heures_apres_l_avis_a_l_avocat_signalee(db) -> None:
    [signalement] = [s for s in detecter_signalements(db) if "moins de 2 h" in s.titre]
    assert signalement.titre.startswith("Audition débutée 1 h 08 après l'avis à l'avocat")
    assert "63-4-2" in signalement.description and "même journée" in signalement.description
    assert signalement.page_reference == 5


def test_acte_avant_la_saisine_releve_par_les_regles(db) -> None:
    [c] = [c for c in contradictions_par_regles(db) if "avant la saisine" in c.titre]
    assert c.titre == "Interpellation à 05h52, avant la saisine à 06h00"
    assert [s["page"] for s in c.sources] == [1, 2]


def test_avis_au_procureur_heure_en_lettres() -> None:
    from depouille.chrono import ACTES_HORODATES
    from depouille.regex_patterns import _normaliser_heure_libre

    texte = "Avis est donné à Madame la Procureure de la République à six heures quarante-cinq."
    m = next(m for nature, motif in ACTES_HORODATES if nature == "avis_parquet" and (m := motif.search(texte)))
    assert _normaliser_heure_libre(m.group("heure")) == "06h45"


@pytest.mark.parametrize("texte, attendu", [
    ("A 07h30, de retour au service, nous notifions", (None, "07h30")),
    ("rapportons les faits suivants : Ce jour à\n06h00, nous recevons", (None, "06h00")),
    ("Il dit être parti. A 23h10 il revient.", (None, None)),
    ("A 23h10, il revient.", (None, None)),
])
def test_ouverture_par_l_heure_seule(texte, attendu) -> None:
    from depouille.regex_patterns import detecter_date_heure_acte

    assert detecter_date_heure_acte(texte) == attendu


def test_charabia_des_bandes_noires_ne_cache_pas_l_intitule() -> None:
    """« RE », « il », « En 27 | » en tête de page : l'intitulé passait sous
    les lignes examinées, la pièce était fondue dans la précédente."""
    from depouille.classify import _detecter_pieces_par_page

    pages = [
        {"numero_global": 1, "fichier_source": "d.pdf", "texte": "D.08 - PLACEMENT EN GARDE À VUE\nA 07h30, nous notifions."},
        {"numero_global": 2, "fichier_source": "d.pdf", "texte": "RE\nil\nD.09\nD.09 - AVIS AVOCAT ET MAGISTRAT\nA 07h42, nous."},
    ]
    assert len(_detecter_pieces_par_page(pages)) == 2


def test_reponse_au_milieu_d_une_ligne_et_recit_de_l_enqueteur() -> None:
    from depouille.declarations import RE_VOIX_ENQUETEUR, _extraire_qr_deterministe

    texte = (
        "A 08h50, nous extrayons le gardé à vue de sa cellule.\n"
        "Question : Reconnaissez-vous votre implication dans le\n"
        "trafic de stupéfiants ? Réponse : Je n'ai rien à dire\n\nsans mon avocat."
    )
    [point] = _extraire_qr_deterministe([{"numero_global": 21, "texte": texte}])
    assert point["citation"] == "Je n'ai rien à dire sans mon avocat."
    assert RE_VOIX_ENQUETEUR.match("Décidons néanmoins de débuter l'audition sur le fond.")
    assert RE_VOIX_ENQUETEUR.match("A 08h50, nous extrayons le gardé à vue de sa cellule.")
    assert not RE_VOIX_ENQUETEUR.match("Je n'ai rien à dire sans mon avocat.")


def test_juge_d_instruction_n_est_pas_illisible() -> None:
    from depouille.qualite_texte import SEUIL_ILLISIBILITE, score_illisibilite

    texte = "Conformément aux instructions du magistrat mandant, procédons aux actes de procédure habituels. " * 6
    assert score_illisibilite(texte) < SEUIL_ILLISIBILITE


@pytest.mark.parametrize("lu, attendu", [
    ("À 69h00, notifions", "À 09h00, notifions"),
    ("Le 16 novembre à 68h30.", "Le 16 novembre à 08h30."),
    ("À 14F30, extrayons", "À 14h30, extrayons"),
    ("contacté à €9h10", "contacté à 09h10"),
    ("à 19h00 et 08h30", "à 19h00 et 08h30"),
])
def test_heures_mal_lues_par_l_ocr_corrigees(lu, attendu) -> None:
    """Observé sur un dossier de gendarmerie scanné : le résumé écrivait
    « placé en garde à vue à 69h00 »."""
    from depouille.ingest import corriger_heures_ocr

    assert corriger_heures_ocr(lu) == attendu


def test_tournures_de_gendarmerie(tmp_path) -> None:
    from depouille.chrono import ACTES_HORODATES

    texte = (
        "Droits notifiés à 09h05. Mentionnons que SOS Médecins, contacté à 09h10, ne s'est toujours pas présenté. "
        "Pénétrons dans les lieux et débutons les opérations de perquisition à 04h20."
    )
    trouves = {n: m.group("heure") for n, motif in ACTES_HORODATES if (m := motif.search(texte))}
    assert trouves == {"notification_droits": "09h05", "demande_examen_medical": "09h10", "debut_perquisition": "04h20"}


def test_perquisition_avant_six_heures_signalee(tmp_path) -> None:
    from depouille.conformite import detecter_signalements

    db = ouvrir_db(tmp_path / "t.db")
    db.execute(
        "INSERT INTO evenements_procedure (nature, date, heure, page, citation, statut_verif) "
        "VALUES ('debut_perquisition', '15/11/2026', '04h20', 5, 'débutons les opérations de perquisition à 04h20', 'verifie'), "
        "('debut_perquisition', '16/11/2026', '10h00', 6, 'x', 'verifie')"
    )
    [s] = [s for s in detecter_signalements(db) if "Perquisition" in s.titre]
    assert s.titre == "Perquisition débutée à 04h20 — hors de la plage 6 h – 21 h" and "article 59" in s.description


def test_tache_de_scan_devant_l_intitule() -> None:
    """« _ PROCÈS-VERBAL D'INTERPELLATION » : la pièce était fondue dans la précédente."""
    from depouille.classify import _est_titre

    assert _est_titre("_ PROCÈS-VERBAL D'INTERPELLATION")
    assert not _est_titre("_ texte ordinaire de la page")
