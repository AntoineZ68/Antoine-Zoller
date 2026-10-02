"""Désignation, par l'avocat, de la personne qu'il défend.

L'outil ne peut pas le deviner : dans un dossier à plusieurs mis en cause,
chacun est le client de quelqu'un, et un avocat de partie civile défend la
victime. C'est donc l'avocat qui le dit, et tout ce qui s'adresse à « votre
client » en découle."""

from __future__ import annotations

import sqlite3


class PersonneInconnue(ValueError):
    pass


def designer_client(db: sqlite3.Connection, nom: str | None) -> None:
    """Désigne `nom` comme client (une seule personne à la fois), ou retire
    toute désignation si `nom` est None. Le résumé, qui écrit « votre
    client », est supprimé : il doit être régénéré pour ne jamais désigner
    la mauvaise personne."""
    if nom is not None and not db.execute("SELECT 1 FROM personnes WHERE nom = ?", (nom,)).fetchone():
        raise PersonneInconnue(f"Aucune personne nommée {nom!r} dans ce dossier.")
    db.execute("UPDATE personnes SET est_client = 0")
    if nom is not None:
        db.execute("UPDATE personnes SET est_client = 1 WHERE nom = ?", (nom,))
    db.execute("DELETE FROM resume_affaire")
    db.commit()
