"""Un dossier pénal réel n'est pas un dossier d'essai : il fait soixante
pages ou plus, contient des auditions de garde à vue étalées sur huit ou
neuf pages, des pièces scannées et des documents manuscrits à peine
lisibles. Ce fichier vérifie ce que ce volume casse, et qui ne se voyait
pas sur de petits dossiers : la perte silencieuse de texte par troncature,
et l'absence de tout avertissement sur les pages mal reconnues."""

from __future__ import annotations

import json
import sqlite3

import pytest
from rich.console import Console

import depouille.chrono as chrono_module
import depouille.classify as classify_module
import depouille.declarations as declarations_module
from depouille.chrono import _extraire_faits_llm
from depouille.classify import identifier_personne_principale
from depouille.config import Config
from depouille.db import ouvrir_db
from depouille.llm.base import ReponseLLM, lots_de_pages, tranches_de_texte
from depouille.qualite_texte import grouper_en_plages, pages_peu_lisibles, score_illisibilite


# --- Découpage : plus aucune troncature silencieuse ---


def _pages(tailles: list[int]) -> list[dict]:
    return [{"numero_global": i + 1, "texte": "x" * t} for i, t in enumerate(tailles)]


def test_lots_de_pages_ne_perd_aucune_page() -> None:
    pages = _pages([3000] * 9)
    lots = lots_de_pages(pages, taille_max=8000)

    assert len(lots) > 1, "une pièce de 27 000 caractères tient en plusieurs appels"
    vues = [p["numero_global"] for lot in lots for p in lot]
    assert vues == list(range(1, 10)), "toutes les pages sont analysées, dans l'ordre"


def test_lots_de_pages_ne_coupe_jamais_au_milieu_dune_page() -> None:
    """Les citations renvoyées par le modèle sont ancrées à un numéro de
    page : une page coupée en deux produirait des citations à cheval, donc
    invérifiables."""
    pages = _pages([12000, 500])
    lots = lots_de_pages(pages, taille_max=8000)

    assert [len(lot) for lot in lots] == [1, 1]
    assert len(lots[0][0]["texte"]) == 12000, "la page trop longue n'est pas amputée ici"


def test_tranches_de_texte_coupe_sur_des_lignes() -> None:
    texte = "".join(f"ligne numero {i} avec du contenu\n" for i in range(400))
    tranches = tranches_de_texte(texte, taille_max=1000)

    assert len(tranches) > 1
    assert "".join(tranches) == texte, "rien n'est perdu ni dupliqué"
    assert all(t.endswith("\n") for t in tranches[:-1]), "aucune coupe en plein mot"


# --- L'extraction des faits couvre la pièce entière ---


class ProviderCompteur:
    """Renvoie un fait par page reçue : ce qui permet de vérifier qu'aucune
    page n'a été écartée avant l'appel."""

    def __init__(self) -> None:
        self.pages_vues: list[int] = []

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        pages = [int(l.removeprefix("[page ").removesuffix("]")) for l in prompt.splitlines() if l.startswith("[page ")]
        self.pages_vues.extend(pages)
        faits = [{"page": p, "citation": "CITATION", "description": "d", "personne_source": ""} for p in pages]
        return ReponseLLM(texte=json.dumps(faits), tokens_in=1, tokens_out=1)


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return ouvrir_db(tmp_path / "test.db")


def test_audition_longue_analysee_en_entier(monkeypatch, db) -> None:
    """Une audition de garde à vue de neuf pages était tronquée à la taille
    d'un seul appel : la fin de l'interrogatoire — souvent l'endroit des
    aveux, des rétractations ou des contradictions — n'était jamais
    analysée, et rien ne le signalait."""
    for numero in range(1, 10):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
            "VALUES (?, 'dossier.pdf', ?, ?, 'x')",
            (numero, numero, f"page {numero} " + "mot " * 900),
        )
    db.execute(
        "INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, \"PV d'audition\", 1, 9)"
    )
    db.commit()

    provider = ProviderCompteur()
    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: provider)

    pieces = db.execute("SELECT * FROM pieces").fetchall()
    nb = _extraire_faits_llm(
        db, Config(offline=False), pieces, Console(quiet=True), {"tokens_in": 0, "tokens_out": 0}
    )

    assert provider.pages_vues == list(range(1, 10)), "aucune page n'est écartée avant l'appel"
    assert nb == 9
    pages_en_base = [r[0] for r in db.execute("SELECT page FROM evenements_faits ORDER BY page")]
    assert pages_en_base == list(range(1, 10))


def test_un_lot_en_echec_ne_fait_pas_perdre_les_autres(monkeypatch, db) -> None:
    """Un appel qui échoue (réponse illisible, coupure réseau) ne doit coûter
    que son propre lot, pas le reste de la pièce."""
    for numero in range(1, 7):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, empreinte_sha256) "
            "VALUES (?, 'dossier.pdf', ?, ?, 'x')",
            (numero, numero, f"page {numero} " + "mot " * 900),
        )
    db.execute("INSERT INTO pieces (id, type, page_debut, page_fin) VALUES (1, \"PV d'audition\", 1, 6)")
    db.commit()

    class ProviderCapricieux(ProviderCompteur):
        def __init__(self) -> None:
            super().__init__()
            self.appels = 0

        def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
            self.appels += 1
            if self.appels == 1:
                raise RuntimeError("réponse illisible")
            return super().appeler(systeme, prompt, modele)

    provider = ProviderCapricieux()
    monkeypatch.setattr(chrono_module, "obtenir_provider", lambda config: provider)

    pieces = db.execute("SELECT * FROM pieces").fetchall()
    nb = _extraire_faits_llm(
        db, Config(offline=False), pieces, Console(quiet=True), {"tokens_in": 0, "tokens_out": 0}
    )

    assert provider.appels > 1
    assert nb > 0, "les lots suivants sont analysés malgré l'échec du premier"


# --- Identification : l'identité peut être déclarée ailleurs qu'en tête ---


class ProviderIdentiteTardive:
    """Ne reconnaît la personne que si la tranche soumise contient son nom —
    comme le ferait un vrai modèle."""

    def __init__(self) -> None:
        self.appels = 0

    def appeler(self, systeme: str, prompt: str, modele: str) -> ReponseLLM:
        self.appels += 1
        if "Yanis BELKACEM" in prompt:
            return ReponseLLM(texte='{"nom": "Yanis BELKACEM", "role": "mis_en_cause"}', tokens_in=1, tokens_out=1)
        return ReponseLLM(texte='{"nom": null, "role": null}', tokens_in=1, tokens_out=1)


def test_identite_declaree_en_fin_de_piece_reste_trouvee(monkeypatch, db) -> None:
    """Sur une reprise d'audition, l'identité est parfois rappelée en fin de
    procès-verbal. Tronquer la pièce au premier appel la rendait
    introuvable, et la pièce ressortait sans aucune personne."""
    remplissage = "\n".join(f"Q. : question numero {i} ? R. : sans interet." for i in range(400))
    texte = f"{remplissage}\nLecture faite, l'intéressé Yanis BELKACEM persiste et signe.\n"

    provider = ProviderIdentiteTardive()
    monkeypatch.setattr(classify_module, "obtenir_provider", lambda config: provider)

    personne_id, methode = identifier_personne_principale(
        db, Config(offline=False), "PV d'audition", texte, Console(quiet=True), {"tokens_in": 0, "tokens_out": 0}
    )

    assert methode == "llm"
    assert provider.appels > 1, "la pièce est parcourue au-delà de sa première tranche"
    assert db.execute("SELECT nom FROM personnes WHERE id = ?", (personne_id,)).fetchone()["nom"] == "Yanis BELKACEM"


# --- Pages illisibles : l'avocat doit être prévenu ---


TEXTE_PROPRE = (
    "Constatons sur les lieux la présence de traces de sang sur le trottoir. "
    "Un sac à main de marque Lancaster est retrouvé à proximité immédiate. "
    "Les constatations sont effectuées le 14 septembre 2026 à 18 heures 30."
)
# Sortie réelle d'un OCR sur une page manuscrite : « Je soussigné Docteur
# Marchand certifie avoir examiné ce jour Madame Dupont Julie née en 1996 ».
TEXTE_ILLISIBLE = (
    "Je SusSiha DŒt dr Mrchand certifie #WOir eXarire "
    "ce jar Magna Dupmt JUlie Fe en 195 prScentant "
    "heratom j ugal ardt æ denar #ij on d rabiasion di Ou fuche"
)


def test_texte_propre_nest_pas_signale() -> None:
    assert score_illisibilite(TEXTE_PROPRE) < 0.1


def test_texte_de_page_manuscrite_est_signale() -> None:
    assert score_illisibilite(TEXTE_ILLISIBLE) >= 0.35


def test_entete_administratif_ne_declenche_pas_de_fausse_alerte() -> None:
    """« N° PARQUET », « N° 2026/00482 » figurent sur presque chaque page
    d'un dossier : les compter comme du bruit d'OCR condamnerait le dossier
    entier."""
    entete = (
        "COMMISSARIAT DE POLICE DE LYON 3/6 - N° PARQUET : 26/000481 - N° PROCÉDURE : 2026/00482 "
        "COTE : D. 4 - Page 12 sur 47 - Service : BAC de nuit - Téléphone : 04 72 00 00 00 "
        "Affaire suivie par l'OPJ de permanence, référence interne 2026-BAC-0481-D4."
    )
    assert score_illisibilite(entete) < 0.35


def test_references_et_horaires_ne_sont_pas_du_bruit() -> None:
    """Un dossier pénal est plein de cotes et d'horaires collés à des
    lettres. Les compter comme des mots abîmés signalerait tout le dossier."""
    texte = (
        "Le 14 septembre 2026 à 17h45, cote D.1, procédure 2026/00482, N°2026-481. "
        "Interpellation à 18h02, placement en garde à vue à 18h30, notification "
        "des droits à 18h35. Le scellé numéro 4 porte l'IMEI 356938035643809. "
        "L'audition reprend à 21h15 en présence du conseil, 2ème audition."
    )
    assert score_illisibilite(texte) < 0.35


def test_scan_correct_nest_pas_confondu_avec_un_manuscrit() -> None:
    """Un scan propre passé à l'OCR perd des apostrophes et coupe quelques
    mots — sans que le texte cesse d'être exploitable. Mesuré sur un dossier
    de 60 pages entièrement numérisées à 300 dpi : score médian 0.05, très
    en deçà du seuil."""
    texte = (
        "QUESTION : Pouvez-vous nous indiquer votre emploi du temps entre 17 et 18 heures ? "
        "REPONSE: J etais chez mon frere, nous avons regarde un match jusqu a 22 heures. "
        "Constatons que le scelle numero 4 contient un telephone de marque Apple dont le "
        "numero IMEI releve est 356938035643809, correspondant a celui declare vole."
    )
    assert score_illisibilite(texte) < 0.35


def test_page_trop_courte_nest_jamais_signalee() -> None:
    """Une page de garde ou un intercalaire ne contient que quelques mots :
    la mesure n'y a pas de sens statistique."""
    assert score_illisibilite("PIÈCES ANNEXES") == 0.0


def test_pages_peu_lisibles_signale_la_page_et_pas_les_autres(db) -> None:
    for numero, texte in ((1, TEXTE_PROPRE), (2, TEXTE_ILLISIBLE), (3, TEXTE_PROPRE)):
        db.execute(
            "INSERT INTO pages (numero_global, fichier_source, page_fichier, texte, ocr_applique, empreinte_sha256) "
            "VALUES (?, 'dossier.pdf', ?, ?, 1, 'x')",
            (numero, numero, texte),
        )
    db.commit()

    signalees = pages_peu_lisibles(db)
    assert [n for n, _, _ in signalees] == [2]


def test_plages_de_pages_lisibles_pour_un_humain() -> None:
    assert grouper_en_plages([12, 13, 14, 17, 18, 30]) == [(12, 14), (17, 18), (30, 30)]


# --- Mémoire : un run tué par le noyau ne rend rien du tout ---


def test_parallelisme_ocr_limite_par_la_memoire_du_conteneur(monkeypatch) -> None:
    """Sur un conteneur à 512 Mo (offre d'hébergement courante), reconnaître
    plusieurs pages à la fois dépasse la limite et fait tuer le processus :
    l'avocat n'obtient alors aucun livrable, pas un livrable dégradé."""
    from depouille import ingest

    monkeypatch.setattr(ingest, "_memoire_disponible_mo", lambda: 512)
    monkeypatch.setattr(ingest.os, "cpu_count", lambda: 8)
    assert ingest._travailleurs_ocr() == 1


def test_parallelisme_ocr_profite_dune_machine_large(monkeypatch) -> None:
    from depouille import ingest

    monkeypatch.setattr(ingest, "_memoire_disponible_mo", lambda: 16000)
    monkeypatch.setattr(ingest.os, "cpu_count", lambda: 4)
    assert ingest._travailleurs_ocr() == 4, "plafonné par les cœurs, pas par la mémoire"


def test_limite_du_conteneur_prime_sur_la_ram_de_la_machine(tmp_path) -> None:
    """Sur un hébergeur, la machine hôte est grande et le conteneur petit :
    lire la mémoire physique donnerait une réponse fausse et dangereuse."""
    from depouille import ingest

    fichier = tmp_path / "memory.max"
    fichier.write_text(f"{512 * 1024 * 1024}\n")
    assert ingest._memoire_disponible_mo((str(fichier),)) == 512


def test_cgroup_sans_plafond_retombe_sur_la_memoire_physique(tmp_path) -> None:
    """Un cgroup non plafonné porte la valeur maximale d'un entier 64 bits,
    ou le mot « max » : la prendre pour une limite réelle ferait croire à une
    mémoire quasi infinie et relancerait la parallélisation au maximum."""
    from depouille import ingest

    physique = ingest._memoire_disponible_mo(())
    for valeur in ("9223372036854771712", "max"):
        fichier = tmp_path / f"memory_{valeur[:4]}.max"
        fichier.write_text(valeur + "\n")
        assert ingest._memoire_disponible_mo((str(fichier),)) == physique
