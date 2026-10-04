"""Schéma SQLite de l'affaire. Un fichier `depouille.db` par affaire.

Chaque table porte un statut par ligne pour permettre la reprise étape par
étape sans tout relancer (voir PLAN.md section 2.2 / 2.3).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    cle TEXT PRIMARY KEY,
    valeur TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_global INTEGER NOT NULL,
    fichier_source TEXT NOT NULL,
    page_fichier INTEGER NOT NULL,
    texte TEXT NOT NULL DEFAULT '',
    ocr_applique INTEGER NOT NULL DEFAULT 0,
    empreinte_sha256 TEXT NOT NULL,
    cote_detectee TEXT,
    statut TEXT NOT NULL DEFAULT 'ingere',
    UNIQUE(numero_global)
);

CREATE TABLE IF NOT EXISTS pieces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    page_debut INTEGER NOT NULL,
    page_fin INTEGER NOT NULL,
    date_apparente TEXT,
    heure_apparente TEXT,
    service_redacteur TEXT,
    personnes_citees_json TEXT NOT NULL DEFAULT '[]',
    cote TEXT,
    confiance REAL NOT NULL DEFAULT 0.0,
    statut_revision TEXT NOT NULL DEFAULT 'a_faire',
    personne_principale_id INTEGER REFERENCES personnes(id),
    methode_personne_principale TEXT,
    titre TEXT
);

CREATE TABLE IF NOT EXISTS personnes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    role TEXT NOT NULL,
    alias_json TEXT NOT NULL DEFAULT '[]',
    est_client INTEGER NOT NULL DEFAULT 0,
    UNIQUE(nom, role)
);

CREATE TABLE IF NOT EXISTS evenements_procedure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    piece_id INTEGER REFERENCES pieces(id),
    date TEXT,
    heure TEXT,
    nature TEXT NOT NULL,
    personne_id INTEGER REFERENCES personnes(id),
    service TEXT,
    page INTEGER NOT NULL,
    citation TEXT NOT NULL,
    methode_verif TEXT,
    statut_verif TEXT NOT NULL DEFAULT 'a_faire'
);

CREATE TABLE IF NOT EXISTS evenements_faits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    piece_id INTEGER REFERENCES pieces(id),
    page INTEGER NOT NULL,
    citation TEXT NOT NULL,
    personne_id_source INTEGER REFERENCES personnes(id),
    date_evenement_affirmee TEXT,
    description TEXT NOT NULL,
    methode_verif TEXT,
    statut_verif TEXT NOT NULL DEFAULT 'a_faire'
);

CREATE TABLE IF NOT EXISTS declarations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personne_id INTEGER REFERENCES personnes(id),
    piece_id INTEGER REFERENCES pieces(id),
    page INTEGER NOT NULL,
    citation TEXT NOT NULL,
    point_factuel TEXT NOT NULL,
    methode_verif TEXT,
    statut_verif TEXT NOT NULL DEFAULT 'a_faire'
);

CREATE TABLE IF NOT EXISTS divergences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personne_id INTEGER REFERENCES personnes(id),
    point_factuel TEXT NOT NULL,
    declaration_id_a INTEGER REFERENCES declarations(id),
    declaration_id_b INTEGER REFERENCES declarations(id)
);

CREATE TABLE IF NOT EXISTS rejets_verification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_origine TEXT NOT NULL,
    id_origine INTEGER NOT NULL,
    page_annoncee INTEGER NOT NULL,
    citation_proposee TEXT NOT NULL,
    raison TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etape TEXT NOT NULL,
    statut TEXT NOT NULL,
    debut TEXT NOT NULL,
    fin TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cout_usd REAL NOT NULL DEFAULT 0.0
);

-- Une seule ligne (id=1) : le résumé en une phrase de l'affaire, généré à
-- partir des faits déjà extraits et vérifiés — jamais en relisant les PDF
-- bruts. Absent en --offline (nécessite un appel au modèle) ou si la
-- génération échoue ; jamais une valeur devinée pour combler l'absence.
-- Emplacement de chaque citation surlignée sur sa page du PDF surligné,
-- en points PDF (origine en haut à gauche) : permet d'encadrer LE passage
-- dont parle un élément quand l'avocat clique dessus.
CREATE TABLE IF NOT EXISTS positions_citations (
    page INTEGER NOT NULL,
    citation TEXT NOT NULL,
    largeur_page REAL NOT NULL,
    hauteur_page REAL NOT NULL,
    zones_json TEXT NOT NULL,
    PRIMARY KEY (page, citation)
);

-- Résumé détaillé : phrases rangées par section, chacune avec ses sources
-- (page + citation exacte, reprises d'éléments déjà vérifiés).
CREATE TABLE IF NOT EXISTS resume_detaille (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_ordre INTEGER NOT NULL,
    section_titre TEXT NOT NULL,
    phrase_ordre INTEGER NOT NULL,
    texte TEXT NOT NULL,
    sources_json TEXT NOT NULL
);

-- Contradictions entre pièces proposées par le modèle et retenues par les
-- contrôles déterministes (celles établies par règles fixes se recalculent
-- à l'affichage). sources_json : [{libelle, page, citation}].
CREATE TABLE IF NOT EXISTS contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ordre INTEGER NOT NULL,
    domaine TEXT NOT NULL,
    titre TEXT NOT NULL,
    description TEXT NOT NULL,
    sources_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_affaire (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    texte TEXT NOT NULL,
    genere_le TEXT NOT NULL
);
"""


# Colonnes ajoutées après la création des premières affaires. `CREATE TABLE
# IF NOT EXISTS` ne touche pas une table qui existe déjà : sans ces
# migrations, la base d'un dossier traité avant l'ajout d'une colonne ferait
# échouer toute requête qui la cite — et l'avocat perdrait l'accès à un
# dossier pourtant complet.
MIGRATIONS = (
    # Personne que l'avocat défend, désignée par lui — n'importe quel rôle
    # (un avocat de partie civile défend la victime).
    ("personnes", "est_client", "INTEGER NOT NULL DEFAULT 0"),
    # Intitulé précis de la pièce pour l'index (« Réquisition ORBIS (ligne
    # 14 52) et réponse »), plus parlant que son seul type.
    ("pieces", "titre", "TEXT"),
)


def appliquer_migrations(conn: sqlite3.Connection) -> None:
    for table, colonne, definition in MIGRATIONS:
        colonnes = {ligne[1] for ligne in conn.execute(f"PRAGMA table_info({table})")}
        if colonnes and colonne not in colonnes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {definition}")
    conn.commit()


def ouvrir_db(chemin: Path) -> sqlite3.Connection:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    appliquer_migrations(conn)
    conn.commit()
    return conn
