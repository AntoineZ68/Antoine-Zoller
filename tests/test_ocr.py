"""Reconnaissance des pages numérisées : ce qui casse sur les gros dossiers
réels (cote numérique sur chaque page, milliers de pages, petits serveurs)
et ne se voyait pas sur le jeu d'essai."""

from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from depouille import ingest
from depouille.ingest import _extraire_textes_natifs, _ocr_si_necessaire

POLICE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PHRASE = "REPONSE : Je ne sais pas, j'avais prete mon telephone ce jour-la."


def _page_scannee(doc: pymupdf.Document, texte: str = PHRASE) -> pymupdf.Page:
    """Une page faite d'une seule image (un scan), sans aucun texte natif."""
    image = Image.new("L", (1240, 1754), 255)  # A4 à 150 dpi
    ImageDraw.Draw(image).text((90, 200), texte, fill=0, font=ImageFont.truetype(POLICE, 26))
    tampon = BytesIO()
    image.save(tampon, format="JPEG", quality=90)
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=tampon.getvalue())
    return page


def _calque_factice(texte: str = "texte reconnu") -> bytes:
    """Ce que renvoie Tesseract : un PDF d'une page, texte invisible, sans image."""
    with pymupdf.open() as doc:
        doc.new_page(width=595, height=842).insert_text((72, 100), texte, render_mode=3)
        return doc.tobytes()


def _dossier_vierge(chemin: Path, nb_pages: int) -> Path:
    with pymupdf.open() as doc:
        for _ in range(nb_pages):
            doc.new_page(width=595, height=842)
        doc.save(chemin)
    return chemin


def _ocr_reel_disponible() -> bool:
    import shutil

    return shutil.which("tesseract") is not None


reel = pytest.mark.skipif(not _ocr_reel_disponible(), reason="Tesseract absent")


@reel
def test_page_scannee_portant_une_cote_numerique_est_reconnue(tmp_path) -> None:
    """Le logiciel du greffe appose la cote (« D12 ») en texte numérique sur
    chaque page scannée. L'ancien moteur sautait toute page contenant déjà
    du texte : ces pages n'étaient JAMAIS lues — c'est-à-dire, sur un
    dossier coté numériquement, aucune."""
    source = tmp_path / "dossier.pdf"
    with pymupdf.open() as doc:
        _page_scannee(doc).insert_text((520, 30), "D12", fontsize=10)
        doc.save(source)

    natifs = _extraire_textes_natifs(source)
    assert len("".join(natifs[0].split())) < ingest.SEUIL_OCR_CARACTERES

    sortie = _ocr_si_necessaire(source, natifs, tmp_path / "work", Console(quiet=True))
    texte = " ".join(_extraire_textes_natifs(sortie)[0].split())
    assert "telephone" in texte and "D12" in texte


@reel
def test_calque_aligne_sur_le_texte_du_scan(tmp_path) -> None:
    """Le surlignage et le cadre du classeur situent une citation par la
    position des mots du calque : décalé, il encadrerait la mauvaise ligne."""
    source = tmp_path / "dossier.pdf"
    with pymupdf.open() as doc:
        _page_scannee(doc)
        doc.save(source)
    sortie = _ocr_si_necessaire(source, _extraire_textes_natifs(source), tmp_path / "work", Console(quiet=True))
    with pymupdf.open(sortie) as doc:
        zones = doc[0].search_for("telephone")
    # Texte dessiné à y = 200 px sur 1754 px de haut, soit ≈ 96 pt sur 842.
    assert zones and 90 < zones[0].y0 < 110


@reel
def test_page_tournee_reste_alignee(tmp_path) -> None:
    """Une page paysage scannée est souvent remise droite par une simple
    rotation d'affichage : le calque doit suivre cette rotation."""
    source = tmp_path / "dossier.pdf"
    with pymupdf.open() as doc:
        _page_scannee(doc)
        doc.save(tmp_path / "droit.pdf")
        doc[0].set_rotation(90)
        doc.save(source)
    zones = {}
    for chemin in (tmp_path / "droit.pdf", source):
        sortie = _ocr_si_necessaire(chemin, [""], tmp_path / f"work_{chemin.stem}", Console(quiet=True))
        with pymupdf.open(sortie) as doc:
            trouvees = doc[0].search_for("telephone")
        assert trouvees, chemin.name
        zones[chemin.stem] = tuple(trouvees[0])
    assert zones["dossier"] == pytest.approx(zones["droit"], abs=3)


def test_images_du_dossier_jamais_reencodees(monkeypatch, tmp_path) -> None:
    """Le PDF montré à l'avocat doit rester la pièce reçue : l'ancien moteur
    (conversion PDF/A) réencodait les images. Le calque se pose par-dessus."""
    monkeypatch.setattr(ingest, "_tesseract_texte_seul", lambda png: _calque_factice())
    source = tmp_path / "dossier.pdf"
    with pymupdf.open() as doc:
        _page_scannee(doc)
        doc.save(source)
    sortie = _ocr_si_necessaire(source, [""], tmp_path / "work", Console(quiet=True))

    def octets_image(chemin: Path) -> bytes:
        with pymupdf.open(chemin) as doc:
            return doc.extract_image(doc[0].get_images()[0][0])["image"]

    assert octets_image(sortie) == octets_image(source)
    assert "texte reconnu" in _extraire_textes_natifs(sortie)[0]


def test_ocr_signale_son_avancement(monkeypatch, tmp_path) -> None:
    """Sur un petit serveur, un gros dossier se compte en dizaines de
    minutes : sans progression, rien ne distingue un traitement lent d'une
    panne."""
    monkeypatch.setattr(ingest, "_tesseract_texte_seul", lambda png: _calque_factice())
    source = _dossier_vierge(tmp_path / "dossier.pdf", 14)
    # 12 pages sans texte sur 14 (les pages 3 et 9 portent déjà du texte).
    textes = ["" if n not in (3, 9) else "x" * 200 for n in range(1, 15)]

    rappels = []
    chemin = _ocr_si_necessaire(
        source, textes, tmp_path / "work", Console(quiet=True),
        progression=lambda faites, total, nom: rappels.append((faites, total)),
    )

    assert rappels == [(0, 12), (5, 12), (10, 12), (12, 12)]
    assert chemin.name == "ocr_dossier.pdf", "le surlignage relit ce fichier sous ce nom"
    assert sorted(p.name for p in (tmp_path / "work").iterdir()) == ["ocr_dossier.pdf"]
    relus = _extraire_textes_natifs(chemin)
    assert all("texte reconnu" in relus[n - 1] for n in range(1, 15) if n not in (3, 9))
    assert "texte reconnu" not in relus[2], "une page native n'est pas repassée à l'OCR"


def test_progression_espacee_sur_les_tres_gros_dossiers() -> None:
    """2 000 pages : pas 2 000 écritures en base pour suivre l'avancement."""
    assert ingest._intervalle_rappels(12) == 5
    assert ingest._intervalle_rappels(2000) == 10


def test_page_en_echec_laissee_sans_texte_sans_tout_faire_echouer(monkeypatch, tmp_path) -> None:
    """Une page que Tesseract n'arrive pas à lire dans le délai ne doit pas
    faire perdre les 1 999 autres."""
    appels = {"n": 0}

    def tesseract(png):
        appels["n"] += 1
        if appels["n"] == 2:
            raise subprocess.TimeoutExpired("tesseract", ingest.DELAI_TESSERACT_PAR_PAGE_S)
        return _calque_factice()

    monkeypatch.setattr(ingest, "_tesseract_texte_seul", tesseract)
    source = _dossier_vierge(tmp_path / "dossier.pdf", 3)
    monkeypatch.setattr(ingest, "_travailleurs_ocr", lambda: 1)  # ordre des appels déterministe
    sortie = _ocr_si_necessaire(source, ["", "", ""], tmp_path / "work", Console(quiet=True))
    relus = _extraire_textes_natifs(sortie)
    assert ["texte reconnu" in t for t in relus] == [True, False, True]


def test_tesseract_absent_erreur_explicite(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ingest.shutil, "which", lambda nom: None)
    source = _dossier_vierge(tmp_path / "dossier.pdf", 1)
    with pytest.raises(RuntimeError, match="Tesseract"):
        _ocr_si_necessaire(source, [""], tmp_path / "work", Console(quiet=True))


def test_delai_tesseract_releve_pour_les_petits_serveurs(monkeypatch) -> None:
    """Au-delà du délai, la page est abandonnée. 180 s (valeur courante des
    outils) est calibré pour un poste de bureau, pas une fraction de cœur."""
    vus = {}

    def faux_run(commande, **options):
        vus.update(options, commande=commande)
        return subprocess.CompletedProcess(commande, 0, stdout=b"%PDF-1.5 ...")

    monkeypatch.setattr(ingest.subprocess, "run", faux_run)
    ingest._tesseract_texte_seul(b"png")
    assert vus["timeout"] > 180
    assert vus["env"]["OMP_THREAD_LIMIT"] == "1", "le parallélisme se fait entre pages"
    assert "fra" in vus["commande"]


def test_coeurs_lus_dans_le_quota_du_conteneur(tmp_path) -> None:
    """os.cpu_count() compte les cœurs de la machine hôte (16 ou plus), pas
    le quota du conteneur : 16 Tesseract sur un cœur ne vont pas plus vite
    mais consomment 16 fois la mémoire."""
    v2 = tmp_path / "cpu.max"
    for contenu, attendu in (("50000 100000", 1), ("200000 100000", 2), ("150000 100000", 2)):
        v2.write_text(contenu + "\n")
        assert ingest._coeurs_disponibles(((str(v2), None),)) == attendu

    quota, periode = tmp_path / "quota", tmp_path / "periode"
    quota.write_text("400000\n")
    periode.write_text("100000\n")
    assert ingest._coeurs_disponibles(((str(quota), str(periode)),)) == 4


def test_conteneur_sans_quota_retombe_sur_les_coeurs_de_la_machine(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ingest.os, "cpu_count", lambda: 6)
    v2 = tmp_path / "cpu.max"
    v2.write_text("max 100000\n")
    quota, periode = tmp_path / "quota", tmp_path / "periode"
    quota.write_text("-1\n")
    periode.write_text("100000\n")
    assert ingest._coeurs_disponibles(((str(v2), None), (str(quota), str(periode)))) == 6
    assert ingest._coeurs_disponibles(((str(tmp_path / "absent"), None),)) == 6


def test_page_scannee_avec_mention_longue_du_greffe_est_reconnue(tmp_path) -> None:
    """« Copie certifiée conforme — Tribunal judiciaire de Lyon — page
    12/2000 » dépasse 50 caractères : au seul seuil de longueur, la page
    passait pour du texte natif et n'était jamais lue."""
    source = tmp_path / "dossier.pdf"
    mention = "Copie certifiée conforme — Tribunal judiciaire de Lyon — page 12/2000"
    with pymupdf.open() as doc:
        _page_scannee(doc).insert_text((40, 830), mention, fontsize=8)
        with pymupdf.open() as natif:
            natif.new_page().insert_text((72, 100), "texte tape " * 40, fontsize=9)
            doc.insert_pdf(natif)
        doc.save(source)
    natifs = _extraire_textes_natifs(source)
    assert len("".join(natifs[0].split())) > ingest.SEUIL_OCR_CARACTERES
    assert ingest._pages_a_reconnaitre(source, natifs) == [1], "la page tapée n'est pas une page scannée"


def test_pdf_deja_reconnu_en_amont_pas_relu(tmp_path) -> None:
    """Beaucoup de dossiers arrivent déjà « cherchables » (calque invisible
    posé par le scanner) : en poser un second les entremêlerait."""
    source = tmp_path / "dossier.pdf"
    with pymupdf.open() as doc:
        _page_scannee(doc).insert_text((90, 100), "REPONSE : Je ne sais pas, j'avais prete mon telephone ce jour-la.", render_mode=3)
        doc.save(source)
    natifs = _extraire_textes_natifs(source)
    assert ingest.SEUIL_OCR_CARACTERES <= len("".join(natifs[0].split())) < ingest.SEUIL_OCR_PAGE_IMAGE
    assert ingest._pages_a_reconnaitre(source, natifs) == []
