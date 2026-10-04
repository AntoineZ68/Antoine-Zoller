# depouille

Outil interne de dépouillement de procédure pénale. Pas un SaaS : un
programme en ligne de commande qui tourne sur ta machine, sur tes dossiers.
Voir `PLAN.md` pour l'architecture complète et les choix de conception.

## Installation (une seule fois)

### 1. Logiciels nécessaires sur la machine

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-fra ghostscript qpdf
```

(sur Mac : `brew install tesseract tesseract-lang ghostscript qpdf`)

### 2. Le programme lui-même

Depuis le dossier du projet :

```bash
pip install -e .
```

Ça installe la commande `depouille` et toutes ses dépendances Python.

## Utilisation

### Sans aucune IA (100% local, aucune donnée ne sort de la machine)

```bash
depouille run /chemin/vers/dossier.pdf --affaire mon_affaire --offline
```

Ça traite tout le dossier de bout en bout (découpage, OCR si besoin,
classification par mots-clés, chronologie de procédure, déclarations) et
produit les livrables dans `mon_affaire/out/`. La chronologie des faits
(narrative) ne sera pas produite en mode `--offline` : elle nécessite un
appel à un modèle de langage.

### Avec appel à un modèle de langage (Anthropic)

1. Copie `config.example.toml` en `config.toml` (ce fichier ne sera jamais
   commité dans git — il est dans `.gitignore`).
2. Renseigne ta clé API Anthropic dans `config.toml`, ou exporte-la :
   ```bash
   export ANTHROPIC_API_KEY="ta-clé"
   ```
3. Lance sans `--offline` :
   ```bash
   depouille run /chemin/vers/dossier.pdf --affaire mon_affaire
   ```

Le programme affiche toujours, en première ligne, si des données vont
sortir de la machine et vers quel fournisseur.

### Étape par étape (reprenable)

Si tu préfères inspecter chaque étape avant de passer à la suivante :

```bash
depouille ingest   dossier.pdf --affaire mon_affaire
depouille classify --affaire mon_affaire
depouille index    --affaire mon_affaire
depouille chrono   --affaire mon_affaire
depouille decl     --affaire mon_affaire
depouille build    --affaire mon_affaire
```

Ajoute `--force` à une étape pour la refaire depuis zéro (sinon une étape
déjà faite est simplement ignorée, pour permettre de reprendre après une
interruption sans tout relancer).

## Livrables

Dans `mon_affaire/out/` :

- `01_index.xlsx` — sommaire des pièces (ordre des pages + ordre chronologique)
- `02_chronologie_procedure.docx` — actes, heures, durées calculées
- `03_chronologie_faits.docx` — récit sourcé
- `04_declarations.xlsx` — un onglet par personne + divergences
- `05_personnalite.docx` — fiche de personnalité sourcée
- `99_controle.md` — **à lire en premier** : ce qui a été rejeté, ce qui
  manque, ce qu'il faut relire à la main

## Jeu d'essai

Pour générer un faux dossier de procédure entièrement fictif et voir le
programme tourner sans avoir besoin d'un vrai dossier :

```bash
python tests/fixtures/generate_fixture.py tests/fixtures/output
depouille run tests/fixtures/output/dossier_fictif.pdf --affaire test --offline
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Banc d'essai IA

Fait tourner le vrai pipeline, avec les vrais appels au modèle et les
réglages du service en ligne, sur un dossier fictif dont on connaît le
contenu (`tests/fixtures/generate_controle.py`), puis contrôle la sortie :

```bash
export ANTHROPIC_API_KEY=...      # variable d'environnement, jamais dans un fichier du dépôt
python scripts/banc_essai_ia.py   # rapport dans /tmp/banc_essai_ia/rapport.md
python scripts/banc_essai_ia.py --pdf mon_dossier.pdf   # + un PDF à vous, jamais commité
python scripts/banc_essai_ia.py --hors-ligne            # sans clé : vérifie le banc lui-même
```

Bloquant (code de sortie 1) : un appel au modèle en échec, un texte du
modèle qui qualifie ou juge, une citation affichée absente de sa page, une
réponse à une question qui qualifie (une question piège est posée).
Mesuré : contradictions attendues, résumés, faits vérifiés, coût et durée.

## Confidentialité

- Tout le traitement PDF/OCR est local.
- `config.toml` (clé API) et les dossiers d'affaires sont dans `.gitignore` :
  ils ne sont jamais commités.
- `--offline` désactive tout appel réseau.
