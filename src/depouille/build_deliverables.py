"""Étape 6 — Assemblage des livrables.

Chaque tableau de sortie ne lit que des lignes au statut_verif = 'verifie' :
une citation rejetée par la passe de vérification n'apparaît dans aucun
livrable. La fiche de personnalité est construite directement à partir du
texte des pages (extraction ligne à ligne, revérifiée avant intégration) —
jamais résumée ni reformulée.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Font
from rich.console import Console

from .chrono import calculer_durees
from .classify import _est_titre
from .config import Config
from .conformite import detecter_signalements
from .regex_patterns import decouper_en_phrases, texte_sans_entete
from .surlignage import construire_pdf_surligne
from .verification import verifier_citation

RE_NE_LE = re.compile(r"né(?:e)?\s+le\s+(\d{2}/\d{2}/\d{4})\s+à\s+([A-ZÀ-Ÿ][\wà-ÿ'\-]+)")
RE_DEMEURANT = re.compile(r"demeurant\s+([^,.\n]+)")

PIECES_PERSONNALITE = ("Enquête de personnalité", "Casier judiciaire")


def _cle_tri_date_heure(date_str: str | None, heure_str: str | None) -> tuple[int, str, str]:
    if not date_str:
        return (1, "9999", "99h99")
    j, m, a = date_str.split("/")
    return (0, f"{a}{m}{j}", heure_str or "")


def _ajouter_table_docx(doc: Document, entetes: list[str], lignes: list[list[str]]) -> None:
    table = doc.add_table(rows=1, cols=len(entetes))
    table.style = "Light Grid Accent 1"
    for cellule, entete in zip(table.rows[0].cells, entetes):
        cellule.text = entete
        cellule.paragraphs[0].runs[0].font.bold = True
    for ligne in lignes:
        cellules = table.add_row().cells
        for cellule, valeur in zip(cellules, ligne):
            cellule.text = str(valeur)


def _construire_chronologie_procedure(db: sqlite3.Connection, chemin: Path) -> None:
    doc = Document()
    doc.add_heading("Chronologie de procédure", level=1)

    doc.add_heading("Durées calculées", level=2)
    durees = calculer_durees(db)
    _ajouter_table_docx(
        doc,
        ["Indicateur", "Valeur"],
        [[cle.replace("_", " "), valeur] for cle, valeur in durees.items()],
    )

    doc.add_heading("Événements", level=2)
    lignes = db.execute(
        """SELECT ep.*, p.nom AS personne_nom FROM evenements_procedure ep
           LEFT JOIN personnes p ON p.id = ep.personne_id
           WHERE ep.statut_verif = 'verifie'"""
    ).fetchall()
    lignes = sorted(lignes, key=lambda r: _cle_tri_date_heure(r["date"], r["heure"]))
    _ajouter_table_docx(
        doc,
        ["Date", "Heure", "Nature", "Personne", "Page", "Citation"],
        [
            [
                r["date"] or "NON TROUVÉ",
                r["heure"] or "NON TROUVÉ",
                r["nature"].replace("_", " "),
                r["personne_nom"] or "NON TROUVÉ",
                r["page"],
                r["citation"],
            ]
            for r in lignes
        ],
    )
    doc.save(chemin)


def _construire_chronologie_faits(db: sqlite3.Connection, chemin: Path) -> None:
    doc = Document()
    doc.add_heading("Chronologie des faits", level=1)
    doc.add_paragraph(
        "Ce que la procédure dit qu'il s'est passé, avec la source de chaque affirmation. "
        "Les versions divergentes ne sont jamais fusionnées : elles apparaissent séparément, "
        "chacune avec sa propre source."
    )

    lignes = db.execute(
        """SELECT ef.*, p.nom AS personne_nom FROM evenements_faits ef
           LEFT JOIN personnes p ON p.id = ef.personne_id_source
           WHERE ef.statut_verif = 'verifie'
           ORDER BY ef.date_evenement_affirmee, ef.page"""
    ).fetchall()

    if not lignes:
        doc.add_paragraph(
            "Aucun fait narratif généré : soit le mode --offline était actif (la chronologie "
            "des faits nécessite un appel au modèle), soit aucune pièce narrative n'a produit "
            "de citation vérifiable.",
            style="Intense Quote",
        )
    else:
        for r in lignes:
            p = doc.add_paragraph()
            p.add_run(f"Page {r['page']} — {r['personne_nom'] or 'NON TROUVÉ'} : ").bold = True
            p.add_run(f"« {r['citation']} »")
            if r["description"]:
                doc.add_paragraph(r["description"], style="Intense Quote")

    doc.save(chemin)


def _construire_declarations(db: sqlite3.Connection, chemin: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    personnes = db.execute(
        """SELECT DISTINCT p.id, p.nom FROM personnes p
           JOIN declarations d ON d.personne_id = p.id
           WHERE d.statut_verif = 'verifie' ORDER BY p.nom"""
    ).fetchall()

    for personne in personnes:
        ws = wb.create_sheet(personne["nom"][:31])
        ws.append(["Point factuel", "Page", "Citation"])
        for cellule in ws[1]:
            cellule.font = Font(bold=True)
        for d in db.execute(
            "SELECT * FROM declarations WHERE personne_id = ? AND statut_verif = 'verifie' ORDER BY point_factuel, page",
            (personne["id"],),
        ):
            ws.append([d["point_factuel"], d["page"], d["citation"]])

    ws_div = wb.create_sheet("Divergences")
    ws_div.append(["Personne", "Point factuel", "Page A", "Citation A", "Page B", "Citation B"])
    for cellule in ws_div[1]:
        cellule.font = Font(bold=True)
    for row in db.execute(
        """SELECT p.nom, dv.point_factuel,
                  da.page AS page_a, da.citation AS citation_a,
                  db_.page AS page_b, db_.citation AS citation_b
           FROM divergences dv
           JOIN declarations da ON da.id = dv.declaration_id_a
           JOIN declarations db_ ON db_.id = dv.declaration_id_b
           LEFT JOIN personnes p ON p.id = dv.personne_id"""
    ):
        ws_div.append(
            [row["nom"] or "NON TROUVÉ", row["point_factuel"], row["page_a"], row["citation_a"], row["page_b"], row["citation_b"]]
        )

    if not wb.sheetnames:
        wb.create_sheet("Déclarations")
    wb.save(chemin)


def _phrases_utiles(texte: str) -> list[str]:
    return decouper_en_phrases(texte_sans_entete(texte, _est_titre))


def _elements_personnalite(db: sqlite3.Connection, seuil_flou_ocr: int) -> dict[str, list[dict]]:
    personnes_mec = db.execute("SELECT * FROM personnes WHERE role = 'mis_en_cause'").fetchall()
    resultat: dict[str, list[dict]] = {}

    for personne in personnes_mec:
        elements: list[dict] = []
        prenom, nom = personne["nom"].split(" ", 1)

        for page in db.execute("SELECT * FROM pages ORDER BY numero_global"):
            if prenom not in page["texte"] or nom not in page["texte"]:
                continue
            for phrase in _phrases_utiles(page["texte"]):
                if prenom not in phrase and nom not in phrase:
                    continue
                m_ne = RE_NE_LE.search(phrase)
                if m_ne:
                    elements.append({"libelle": f"Né(e) le {m_ne.group(1)} à {m_ne.group(2)}", "page": page["numero_global"], "citation": phrase})
                m_dem = RE_DEMEURANT.search(phrase)
                if m_dem:
                    elements.append({"libelle": f"Demeurant {m_dem.group(1).strip()}", "page": page["numero_global"], "citation": phrase})

        for piece in db.execute("SELECT * FROM pieces WHERE type IN (?, ?)", PIECES_PERSONNALITE):
            for page in db.execute(
                "SELECT * FROM pages WHERE numero_global BETWEEN ? AND ? ORDER BY numero_global",
                (piece["page_debut"], piece["page_fin"]),
            ):
                for phrase in _phrases_utiles(page["texte"]):
                    elements.append({"libelle": piece["type"], "page": page["numero_global"], "citation": phrase})

        elements_verifies = []
        for e in elements:
            page = db.execute("SELECT texte, ocr_applique FROM pages WHERE numero_global = ?", (e["page"],)).fetchone()
            valide, _ = verifier_citation(page["texte"], e["citation"], bool(page["ocr_applique"]), seuil_flou_ocr)
            if valide:
                elements_verifies.append(e)

        resultat[personne["nom"]] = elements_verifies

    return resultat


def _construire_personnalite(db: sqlite3.Connection, chemin: Path, seuil_flou_ocr: int) -> None:
    doc = Document()
    doc.add_heading("Fiche de personnalité", level=1)
    doc.add_paragraph(
        "Consolidation sourcée des éléments figurant au dossier sur la personne mise en "
        "cause. Aucun élément n'est ajouté ni déduit ; ce qui n'apparaît pas explicitement "
        "dans le dossier est marqué NON TROUVÉ."
    )

    elements_par_personne = _elements_personnalite(db, seuil_flou_ocr)

    if not elements_par_personne:
        doc.add_paragraph("NON TROUVÉ — aucune personne mise en cause identifiée dans le dossier.")

    for nom, elements in elements_par_personne.items():
        doc.add_heading(nom, level=2)
        if not elements:
            doc.add_paragraph("NON TROUVÉ")
            continue
        for e in elements:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{e['libelle']} ").bold = True
            p.add_run(f"(p. {e['page']} : « {e['citation']} »)")

    doc.save(chemin)


def _construire_signalements(db: sqlite3.Connection, chemin: Path) -> None:
    doc = Document()
    doc.add_heading("Signalements de conformité procédurale", level=1)
    doc.add_paragraph(
        "Signalements strictement structurels (pièce ou événement attendu non retrouvé "
        "dans le dossier, ou seuil numérique non controversé de garde à vue dépassé). "
        "L'outil ne qualifie jamais juridiquement un acte et ne parle jamais de "
        "\"nullité\" ou d'\"irrégularité\" : chaque ligne dit seulement ce qui a été "
        "trouvé ou pas trouvé, avec sa source. L'appréciation juridique reste entièrement "
        "à l'avocat.",
        style="Intense Quote",
    )

    signalements = detecter_signalements(db)
    if not signalements:
        doc.add_paragraph("Aucun signalement structurel détecté sur les éléments identifiés dans le dossier.")
    else:
        for s in signalements:
            doc.add_heading(s.titre, level=2)
            doc.add_paragraph(s.description)
            if s.page_reference and s.citation_reference:
                p = doc.add_paragraph()
                p.add_run(f"Source (p. {s.page_reference}) : ").bold = True
                p.add_run(f"« {s.citation_reference} »")

    doc.save(chemin)


def _construire_controle(db: sqlite3.Connection, chemin: Path, stats_surlignage: dict[str, int]) -> None:
    nb_signalements = len(detecter_signalements(db))
    nb_pages = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    nb_ocr = db.execute("SELECT COUNT(*) FROM pages WHERE ocr_applique = 1").fetchone()[0]
    pieces_non_id = db.execute("SELECT * FROM pieces WHERE type = 'Non identifié'").fetchall()
    rejets = db.execute("SELECT * FROM rejets_verification").fetchall()

    nb_dates_non_trouvees = db.execute("SELECT COUNT(*) FROM pieces WHERE date_apparente IS NULL").fetchone()[0]
    nb_services_non_trouves = db.execute("SELECT COUNT(*) FROM pieces WHERE service_redacteur IS NULL").fetchone()[0]
    nb_cotes_non_trouvees = db.execute("SELECT COUNT(*) FROM pages WHERE cote_detectee IS NULL").fetchone()[0]

    nb_floue_ocr = 0
    for table in ("evenements_procedure", "evenements_faits", "declarations"):
        nb_floue_ocr += db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE methode_verif = 'floue_ocr'"
        ).fetchone()[0]

    runs = db.execute(
        "SELECT * FROM run_log WHERE id IN (SELECT MAX(id) FROM run_log GROUP BY etape) ORDER BY id"
    ).fetchall()
    duree_totale_s = 0.0
    cout_total = 0.0
    tokens_in_total = 0
    tokens_out_total = 0
    lignes_etapes = []
    for r in runs:
        debut = datetime.fromisoformat(r["debut"])
        fin = datetime.fromisoformat(r["fin"]) if r["fin"] else debut
        duree_s = (fin - debut).total_seconds()
        duree_totale_s += duree_s
        cout_total += r["cout_usd"]
        tokens_in_total += r["tokens_in"]
        tokens_out_total += r["tokens_out"]
        lignes_etapes.append(f"| {r['etape']} | {duree_s:.1f} s | {r['tokens_in']} | {r['tokens_out']} | {r['cout_usd']:.4f} $ |")

    lignes = []
    lignes.append("# Rapport de contrôle\n")
    lignes.append(f"Généré le {datetime.now().isoformat(timespec='seconds')}.\n")
    lignes.append("## Vue d'ensemble\n")
    lignes.append(f"- Pages traitées : {nb_pages}")
    lignes.append(f"- Pages passées en OCR : {nb_ocr}")
    lignes.append(f"- Durée totale de traitement (somme des étapes) : {duree_totale_s:.1f} s")
    lignes.append(f"- Tokens modèle consommés : {tokens_in_total} entrée / {tokens_out_total} sortie")
    lignes.append(f"- Coût estimé des appels modèle : {cout_total:.4f} $")
    lignes.append(f"- Signalements de conformité procédurale : {nb_signalements} (voir 06_signalements_procedure.docx)")
    if "erreur" in stats_surlignage:
        lignes.append(f"- Dossier surligné : ÉCHEC ({stats_surlignage['erreur']})\n")
    else:
        lignes.append(
            f"- Dossier surligné (00_dossier_surligne.pdf) : {stats_surlignage['surlignes']} passage(s) "
            f"surligné(s), {stats_surlignage['introuvables']} introuvable(s) dans la mise en page PDF "
            "(légende : jaune = chronologie de procédure, bleu = déclarations, vert = chronologie des faits)\n"
        )

    lignes.append("## Durée et coût par étape\n")
    lignes.append("| Étape | Durée | Tokens entrée | Tokens sortie | Coût |")
    lignes.append("|---|---|---|---|---|")
    lignes.extend(lignes_etapes)
    lignes.append("")

    lignes.append("## Pièces non identifiées (à relire manuellement)\n")
    if pieces_non_id:
        lignes.append("| Pages | Confiance |")
        lignes.append("|---|---|")
        for p in pieces_non_id:
            lignes.append(f"| {p['page_debut']}-{p['page_fin']} | {p['confiance']:.2f} |")
    else:
        lignes.append("Aucune.")
    lignes.append("")

    lignes.append("## Citations rejetées à la vérification\n")
    if rejets:
        lignes.append("| Table | Page annoncée | Citation proposée | Raison |")
        lignes.append("|---|---|---|---|")
        for r in rejets:
            citation_courte = r["citation_proposee"][:80].replace("|", "/")
            lignes.append(f"| {r['table_origine']} | {r['page_annoncee']} | {citation_courte} | {r['raison']} |")
    else:
        lignes.append("Aucune.")
    lignes.append("")

    lignes.append("## Champs sortis en NON TROUVÉ\n")
    lignes.append(f"- Date de pièce non trouvée : {nb_dates_non_trouvees}")
    lignes.append(f"- Service rédacteur non trouvé : {nb_services_non_trouves}")
    lignes.append(f"- Cote de page non trouvée : {nb_cotes_non_trouvees}")
    lignes.append("")

    lignes.append("## Citations validées par correspondance floue (pages OCR uniquement)\n")
    lignes.append(
        f"{nb_floue_ocr} citation(s) validée(s) par tolérance floue plutôt que par exactitude "
        "littérale stricte — à relire en priorité même si elles ne sont pas rejetées."
    )
    lignes.append("")

    chemin.write_text("\n".join(lignes), encoding="utf-8")


def construire_livrables(db: sqlite3.Connection, affaire_dir: Path, config: Config, console: Console) -> None:
    dossier_out = affaire_dir / "out"
    dossier_out.mkdir(parents=True, exist_ok=True)

    if db.execute("SELECT COUNT(*) FROM pieces").fetchone()[0] == 0:
        console.print("  [build] aucune pièce en base — lance d'abord `depouille classify`.")
        return

    try:
        resultat_surlignage = construire_pdf_surligne(db, affaire_dir, dossier_out / "00_dossier_surligne.pdf")
        console.print(
            f"  [build] dossier surligné : {resultat_surlignage['surlignes']} passage(s) surligné(s), "
            f"{resultat_surlignage['introuvables']} introuvable(s) dans la mise en page PDF"
        )
    except (FileNotFoundError, OSError) as exc:
        console.print(
            f"  [build] échec du surlignage ({exc}) — fichier(s) source introuvable(s). "
            "Les autres livrables sont générés normalement."
        )
        resultat_surlignage = {"surlignes": 0, "introuvables": 0, "erreur": str(exc)}

    _construire_chronologie_procedure(db, dossier_out / "02_chronologie_procedure.docx")
    _construire_chronologie_faits(db, dossier_out / "03_chronologie_faits.docx")
    _construire_declarations(db, dossier_out / "04_declarations.xlsx")
    _construire_personnalite(db, dossier_out / "05_personnalite.docx", config.seuil_flou_ocr)
    _construire_signalements(db, dossier_out / "06_signalements_procedure.docx")
    _construire_controle(db, dossier_out / "99_controle.md", resultat_surlignage)

    console.print(f"  [build] livrables générés dans {dossier_out}")
