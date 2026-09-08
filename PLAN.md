# Plan — Outil de dépouillement de procédure pénale (`depouille`)

Ce document sert de référence de travail. Il est découpé en deux parties :
d'abord les points du cahier des charges qui, en l'état, présentent un risque
réel d'échec silencieux (à trancher avant de coder), puis le plan
d'implémentation détaillé.

---

## 1. Trois points à trancher avant de coder

### 1.1 Résidence des données du modèle de langage — point sensible, pas cosmétique

Le SDK Anthropic tel que packagé par défaut interroge l'API Anthropic, hébergée
aux États-Unis. Il n'existe pas de région UE pour l'API Anthropic directe. Pour
un usage couvert par le secret professionnel de l'avocat, la question de savoir
où transitent les données n'est pas un détail RGPD parmi d'autres — c'est un
point sur lequel un ordre des avocats ou un juge peut te demander des comptes.
Deux options existent si tu veux une résidence UE : Claude via **AWS Bedrock**
(régions `eu-central-1`, `eu-west-3`) ou via **Google Vertex AI**
(`europe-west*`). Les deux exposent les mêmes modèles Claude avec une API
différente du SDK Anthropic natif.

**Ce que je propose** : rendre le fournisseur ET la région explicitement
configurables (Anthropic direct / Bedrock / Vertex), avec Bedrock UE comme
option recommandée dans la documentation, sans l'imposer par défaut — je ne
connais pas ta politique de conformité actuelle. La ligne affichée au démarrage
(« telle donnée va sortir vers tel endpoint ») devient alors le seul endroit où
ce choix a besoin d'être visible. `--offline` reste le filet de sécurité
absolu.

→ Je code contre une interface `LLMProvider` abstraite dès le départ pour ne
pas coupler le pipeline au SDK Anthropic ; le connecteur Anthropic direct est
livré en premier, Bedrock pourra être ajouté sans toucher au pipeline. Dis-moi
si tu veux que je livre le connecteur Bedrock dès cette version ou si Anthropic
direct suffit pour l'instant.

### 1.2 Vérification des citations sur pages OCRisées : l'exactitude littérale stricte va rejeter trop de vrais faits

Le cahier des charges dit : "une seconde passe programmatique recherche chaque
citation dans le texte de la page annoncée. Toute citation introuvable est
rejetée." Appliqué tel quel avec une recherche de sous-chaîne strictement
exacte, ce mécanisme va aussi rejeter des citations *correctes* dès que le
modèle réécrit une espace insécable, une apostrophe courbe, une casse, ou un
retour à la ligne OCR en recopiant le texte — ce qui arrive systématiquement,
pas occasionnellement. Sur un dossier à 30-40 % de pages scannées, une
vérification trop stricte peut faire chuter ton taux de citations validées à
un niveau qui rend le livrable inutilisable, alors même que les faits sont
réels et correctement sourcés.

**Ce que je propose** (sans jamais transiger sur le principe — pas de citation
inventée qui passe) :
1. Normalisation avant comparaison des deux côtés : espaces multiples réduits,
   guillemets/apostrophes typographiques uniformisés, casse insensible.
2. Recherche exacte en sous-chaîne sur texte normalisé.
3. À défaut, recherche floue (`rapidfuzz`, seuil ≥ 97 %) *uniquement* sur les
   pages marquées OCR — jamais sur du texte natif, où l'exactitude stricte
   reste la règle. Une citation validée par la voie floue est marquée comme
   telle dans le rapport de contrôle, jamais silencieusement confondue avec
   une citation exacte.
4. Tout ce qui ne passe ni l'un ni l'autre est rejeté et versé dans
   `99_controle.md`, comme prévu.

Cela ajoute `rapidfuzz` à la pile technique (pure Python, pas de dépendance
réseau). Dis-moi si ce compromis te convient — c'est le seul endroit du
système où j'introduis une tolérance, et elle reste strictement bornée aux
pages OCR.

### 1.3 « Six livrables en moins de vingt minutes sur 400 pages » — objectif, pas garantie contractuelle

Sur un dossier réel où une part significative des pages est scannée (PV
manuscrits, fax, photocopies), le poste de temps dominant est l'OCR
(`ocrmypdf` + Tesseract), pas l'appel au modèle. À titre indicatif, Tesseract
en local tourne autour de quelques secondes par page selon la résolution — sur
150-200 pages scannées, l'OCR seul peut représenter l'essentiel du budget de
20 minutes, avant même la classification et l'analyse. J'architecture le
pipeline pour paralléliser l'OCR (multiprocessing) et ne jamais retraiter une
page déjà vue (empreinte de contenu), ce qui devrait tenir l'objectif dans la
majorité des cas — mais je ne peux pas le garantir sans un vrai dossier scanné
pour calibrer. Je le traite comme un objectif de conception, pas comme un
critère d'acceptation binaire tant qu'on n'a pas testé sur du réel.

---

## 2. Architecture

### 2.1 Arborescence du projet

```
depouille/
  pyproject.toml
  README.md
  .gitignore                    # inclut *.config.toml, */work/, */out/*.db, etc.
  config.example.toml           # template committé, jamais le vrai config
  src/depouille/
    cli.py                      # app typer, une commande par étape + `run`
    config.py                   # chargement config, garde --offline
    db.py                       # schéma SQLite + migrations légères
    ingest.py
    classify.py
    index_builder.py
    chrono.py
    declarations.py
    build_deliverables.py
    verification.py             # passe de contrôle déterministe (1.2)
    llm/
      base.py                   # interface LLMProvider
      anthropic_provider.py
      offline_provider.py       # no-op, lève si appelé
    regex_patterns.py            # dates, heures, cotes, n° de procédure
    control_report.py
  tests/
    fixtures/
      generate_fixture.py        # génère le faux dossier PDF
    test_ingest.py
    test_verification.py
    test_chrono_durations.py
    test_offline_no_network.py
    ...
  PLAN.md
```

### 2.2 Schéma SQLite (un fichier par affaire, `<affaire>/depouille.db`)

- `pages(id, numero_global, fichier_source, page_fichier, texte, ocr_applique, empreinte_sha256, cote_detectee, statut)`
- `pieces(id, type, page_debut, page_fin, date_apparente, service_redacteur, personnes_citees_json, cote, confiance, statut_revision)`
- `personnes(id, nom, role, alias_json)` — rôles : mis_en_cause / victime / témoin / expert / enquêteur
- `evenements_procedure(id, piece_id, date, heure, nature, personne_id, service, page, citation, methode_verif, statut_verif)`
- `evenements_faits(id, piece_id, page, citation, personne_id_source, description, statut_verif)`
- `declarations(id, personne_id, piece_id, page, citation, point_factuel, methode_verif, statut_verif)`
- `divergences(id, personne_id, point_factuel, declaration_id_a, declaration_id_b)`
- `rejets_verification(id, table_origine, id_origine, page_annoncee, citation_proposee, raison)`
- `run_log(id, etape, statut, debut, fin, tokens_in, tokens_out, cout_usd)`

Chaque étape lit son entrée depuis les tables produites par l'étape
précédente et écrit un `statut` par ligne. Reprise = ne retraiter que les
lignes en statut `a_faire` ou `erreur`, sauf `--force`.

### 2.3 Pipeline (commandes CLI, conformes à la demande)

```
depouille ingest   <dossier_pdf...> --affaire <nom>
depouille classify <affaire>
depouille index    <affaire>
depouille chrono   <affaire>
depouille decl     <affaire>
depouille build    <affaire>
depouille run      <dossier_pdf...> --affaire <nom>   # enchaîne tout, reprenable
```

Options globales : `--offline`, `--config <chemin>`, `--force` (retraiter),
`--affaire-dir <chemin>` (par défaut `./<affaire>/`).

Bannière obligatoire à chaque lancement, avant toute action :
`[réseau] --offline actif : aucun appel sortant.` ou
`[réseau] appels LLM activés → <provider>/<région>/<modèle>`.

### 2.4 Étapes — détail d'implémentation

**Ingestion** : `pypdf` pour split/pages, `pdfplumber` pour texte natif. Seuil
« page scannée » : < 50 caractères extraits → passage à `ocrmypdf` (langue
`fra`), le texte OCR remplace le texte natif pour cette page, `ocr_applique =
1`. Détection de cote par regex multi-motifs (en-tête/pied de page,
tolérance sur variantes de format) ; à défaut `NON TROUVÉ`, jamais de
déduction.

**Classification** : détection de frontière de pièce par heuristique
déterministe d'abord (rupture de numérotation, motif d'en-tête récurrent,
changement de cote), puis appel LLM par pièce (pas par page — maîtrise du
coût) pour typer + extraire service/personnes/date apparente + niveau de
confiance auto-déclaré par le modèle, recoupé avec la présence effective de
mots-clés attendus pour ce type. Sous le seuil (configurable, défaut 0.7) →
`Non identifié`, signalé pour relecture.

**Index** : deux vues SQL triées (page, date) matérialisées pour l'export.

**Chrono** : extraction d'horodatages par regex sur le texte des pièces
pertinentes (GAV, notifications, prolongations, auditions, perquisitions),
un événement = une ligne avec citation obligatoire. Durées calculées en
Python pur (`datetime` diff), affichées brutes, sans commentaire. Chronologie
des faits : appel LLM par pièce narrative (PV d'audition, constatations,
synthèse) pour extraire les affirmations factuelles avec leur auteur ; jamais
de fusion de versions.

**Déclarations** : par personne × par audition, extraction LLM des points
déclarés avec citation. Détection de divergence : comparaison des points
factuels normalisés (même personne, même sujet identifié) entre auditions
différentes ; dès que deux citations diffèrent sur un point jugé identique,
les deux sont mises côte à côte sans qualification.

**Fiche de personnalité** : agrégation sourcée depuis PV d'audition, enquête
de personnalité, casier judiciaire — même règle page+citation obligatoire.

**Build** : génération `01_index.xlsx` (2 onglets), `02_chronologie_procedure.docx`,
`03_chronologie_faits.docx`, `04_declarations.xlsx` (1 onglet/personne +
divergences), `05_personnalite.docx`, `99_controle.md`.

**Contrôle** (`99_controle.md`) : pages traitées, pages OCR, pièces non
identifiées (+ pages), citations rejetées (+ raison + méthode de vérif
tentée), champs `NON TROUVÉ`, durée totale et par étape, coût des appels
modèle (tokens × tarif configuré), et — ajout par rapport au cahier des
charges — le décompte des citations validées par voie floue (1.2), pour que
tu saches où relire en priorité même parmi ce qui est « validé ».

### 2.5 Jeu d'essai (`tests/fixtures/generate_fixture.py`)

Génère par script (pas de fichiers statiques) : ~10 pièces couvrant au moins
GAV (placement, notification, prolongation, fin), 2 PV d'audition libre/garde
à vue par 2 mis en cause + 1 victime, 1 certificat médical, 1 PV de
perquisition/saisie, 1 enquête de personnalité, 1 extrait de casier —
2 personnes, 3 auditions dont 2 divergentes sur un point factuel précis et
identifiable (ex. l'heure d'un fait, ou la présence d'un tiers), horaires de
GAV cohérents entre eux (permet de vérifier le calcul de durée), et 2-3 pages
rendues en image (texte rasterisé, pas de couche texte) pour forcer le
déclenchement OCR. Noms/lieux/dates manifestement fictifs (ex. « Commune de
Vaucressin », « OPJ Julien Bardot », dates en 2031).

### 2.6 Tests

- `test_verification.py` : aucun fait sans page+citation ne traverse le
  pipeline ; une citation altérée au-delà du seuil flou est bien rejetée et
  absente des livrables ; une citation légèrement reformatée (espace,
  apostrophe) sur page OCR est acceptée et marquée comme telle.
- `test_offline_no_network.py` : interception socket (ex. monkeypatch
  `socket.socket`) pendant un run `--offline` complet → zéro tentative de
  connexion.
- `test_chrono_durations.py` : sur le jeu d'essai, la durée de GAV calculée
  correspond exactement aux horaires injectés par le générateur.
- `test_no_silent_inference.py` : un champ absent du texte source ressort
  bien en `NON TROUVÉ`, jamais interpolé.

---

## 3. Ordre de construction (commits petits, un par étape fonctionnelle)

1. Squelette projet (`pyproject.toml`, `cli.py` vide, `config.py`, bannière
   réseau, `.gitignore`, schéma SQLite vide).
2. Générateur de jeu d'essai → premier commit avec un vrai PDF de test généré
   et inspectable.
3. `ingest` — tourné sur le jeu d'essai, sortie montrée (dump des pages en
   base, statut OCR par page).
4. `classify` — sortie montrée (table des pièces + confiance).
5. `index` — export `01_index.xlsx` montré.
6. `chrono` — export `02_...docx` et `03_...docx` montrés, durées vérifiées.
7. `decl` — export `04_...xlsx` montré, divergence du jeu de test bien
   détectée.
8. `verification.py` intégré à toutes les étapes précédentes (passe de
   contrôle rétroactive) + `99_controle.md`.
9. `build` (assemblage final) + `run` (orchestrateur reprenable).
10. Suite de tests complète + passage sur jeu d'essai de bout en bout,
    chronométré.

---

## 4. Ce qui reste hors périmètre (rappel, confirmé)

Interface web, comptes utilisateurs, détection de nullités, recherche de
jurisprudence, génération de conclusions, résumé automatique, chiffrement des
livrables, multi-affaires simultanées, intégration PLEX automatisée. Je ne
construirai rien de tout ça sans que tu le redemandes explicitement.

---

## Validation requise avant que je commence à coder

Merci de trancher les 3 points de la section 1 (ou de me dire "vas-y avec tes
recommandations par défaut"), puis je démarre à l'étape 1 de la section 3.
