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
    service_redacteur TEXT,
    personnes_citees_json TEXT NOT NULL DEFAULT '[]',
    cote TEXT,
    confiance REAL NOT NULL DEFAULT 0.0,
    statut_revision TEXT NOT NULL DEFAULT 'a_faire',
    personne_principale_id INTEGER REFERENCES personnes(id),
    methode_personne_principale TEXT
);

CREATE TABLE IF NOT EXISTS personnes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    role TEXT NOT NULL,
    alias_json TEXT NOT NULL DEFAULT '[]',
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
"""


def ouvrir_db(chemin: Path) -> sqlite3.Connection:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
