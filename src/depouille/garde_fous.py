"""Garde-fous déterministes appliqués à tout texte libre produit par le
modèle (résumé, réponse à une question, intitulé, description d'un fait).

La consigne donnée au modèle ne suffit pas : un modèle peut l'ignorer. Ces
filtres, eux, ne dépendent pas de sa bonne volonté — un texte qui contient
une qualification juridique, un jugement, ou un élément absent de ses
sources n'atteint jamais l'écran."""

from __future__ import annotations

import re
import unicodedata

RE_QUALIFICATION = re.compile(
    r"nullit|irr[ée]gul|invocable|qualit[ée] (?:pour|à) agir|\bgrief\b|vice de proc|ill[ée]gal|annulable|entach",
    re.IGNORECASE,
)

# Jugement sur la sincérité ou la culpabilité de quiconque. « menti(?!on) » :
# « Mention de la poursuite des investigations » n'est pas un mensonge.
RE_JUGEMENT = re.compile(
    r"\bment\b|\bmenti(?!on)|mensong|faux t[ée]moignage|coupable|culpabilit|innocen|"
    r"\bpreuve|\bprouve|\bavoue|incrimin|disculp",
    re.IGNORECASE,
)


def contient_qualification(texte: str) -> bool:
    return bool(RE_QUALIFICATION.search(texte))


def contient_jugement(texte: str) -> bool:
    return bool(RE_JUGEMENT.search(texte))


# --- Éléments identifiants : noms, lieux, nombres -------------------------
#
# Ce qu'un texte rédigé par le modèle peut inventer de dangereux, ce sont
# les éléments qui identifient : un nom, un lieu, un opérateur, une date, une
# heure, une durée, un montant — et la nature des faits ou le stade de la
# procédure. Chacun doit figurer dans le texte de référence : la pièce pour
# un intitulé, les sources citées pour une phrase de résumé.
#
# Observé en réel (Mistral Large, dossier de contrôle) : un résumé faisait
# dire au client qu'il était arrivé « vers 21h00 » (il a dit 22 h puis
# 23 h 30), qu'il avait discuté « une dizaine de minutes » (une vingtaine),
# qu'il connaissait la victime « depuis un an » (deux ans), et parlait de
# « violences » et d'un « réquisitoire » absents du dossier. L'ancien
# contrôle laissait tout passer : « 21 » et « 00 » se trouvaient ailleurs
# dans la référence (« 21h10 », « 09h00 »), et rien ne lisait les nombres
# écrits en lettres.

RE_MOT = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")
# Mots qui n'identifient rien ni personne : abréviations de procédure et
# civilités. « PV de pose de la balise » reste exact si la pièce écrit
# « PROCÈS-VERBAL » ; « Mme SERMET » reste exact si la source écrit « SERMET
# Odile ».
MOTS_GENERIQUES = frozenset({
    "pv", "gav", "cpp", "opj", "apj", "jld", "ji", "tj", "cp", "rg",
    "m", "mme", "mlle", "me", "dr", "maitre", "monsieur", "madame", "docteur",
})
# Ce qui précède un début de phrase : un mot à majuscule initiale y est un
# mot ordinaire (« Il », « Lors »), pas un nom propre.
FINS_DE_PHRASE = ".!?…:;«\"(\n"

NOMBRES_EN_LETTRES = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20,
    "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "cent": 100,
    "cents": 100, "mille": 1000,
}
# « un », « une » sont aussi des articles, « neuf » un adjectif : ils ne
# comptent comme nombres que suivis d'une unité (« un an », « une heure »).
NOMBRES_AMBIGUS = frozenset({"un", "une", "neuf"})
# « et » ne lie deux mots-nombres qu'après une dizaine : « vingt et un »,
# « soixante et onze » — jamais « entre deux et trois heures » (5).
DIZAINES_ET = frozenset({"vingt", "trente", "quarante", "cinquante", "soixante"})
UNITES = frozenset({
    "an", "ans", "annee", "annees", "mois", "semaine", "semaines", "jour", "jours",
    "nuit", "nuits", "heure", "heures", "minute", "minutes", "seconde", "secondes",
    "fois", "euro", "euros", "metre", "metres", "kilometre", "kilometres", "km",
    "personne", "personnes", "individu", "individus", "homme", "hommes", "femme",
    "femmes", "enfant", "enfants", "coup", "coups", "balle", "balles", "vehicule",
    "vehicules", "telephone", "telephones",
})
APPROXIMATIONS = frozenset({
    "dizaine", "douzaine", "quinzaine", "vingtaine", "trentaine", "quarantaine",
    "cinquantaine", "soixantaine", "centaine", "millier",
})

# Nature des faits et stade de la procédure : un résumé qui écrit
# « violences » ou « réquisitoire » quand aucune source ne le dit change le
# dossier que l'avocat croit lire. Motifs sur texte normalisé (sans accents,
# minuscules).
TERMES_A_SOURCER = tuple(re.compile(m) for m in (
    r"violen\w*", r"\bvols?\b", r"\bviols?\b", r"meurtr\w*", r"assassin\w*", r"homicid\w*",
    r"\bcoups?\b", r"blessur\w*", r"agress\w*", r"menac\w*", r"escroq\w*",
    r"abus de confiance", r"trafic\w*", r"stupefiant\w*", r"recel\w*", r"extorsion\w*",
    r"sequestr\w*", r"harcel\w*", r"degradation\w*", r"incendi\w*", r"outrage\w*",
    r"rebellion\w*", r"\barmes?\b", r"blanchi\w*", r"malfaiteurs", r"cambriol\w*",
    r"enlevement\w*", r"racket\w*",
    r"requisitoire\w*", r"information judiciaire", r"mise? en examen", r"juge d.instruction",
    r"\bordonnance\w*", r"controle judiciaire", r"detention provisoire", r"comparution\w*",
    r"\brenvo[iy]\w*", r"\bjugement\w*", r"condamn\w*", r"tribunal\w*", r"cour d.assises",
    r"\baudience\w*", r"classement sans suite", r"deferr?e?ment\w*", r"\bplaintes?\b",
    r"qualification\w*", r"\baveux?\b", r"temoin assiste", r"partie civile", r"mandat d",
    r"\becrou\w*", r"incarcer\w*",
))


def _normaliser(texte: str) -> str:
    forme = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in forme if not unicodedata.combining(c)).lower()


def _valeur_en_lettres(mots: list[str]) -> int:
    """« vingt-quatre » → 24, « quatre-vingt-dix-huit » → 98,
    « deux cents » → 200, « trente et un » → 31."""
    total, courant = 0, 0
    for mot in mots:
        if mot == "et":
            continue
        valeur = NOMBRES_EN_LETTRES[mot]
        if valeur == 100:
            courant = max(courant, 1) * 100
        elif valeur == 1000:
            total += max(courant, 1) * 1000
            courant = 0
        elif valeur == 20 and courant % 100 == 4:
            courant += 76  # quatre-vingt
        else:
            courant += valeur
    return total + courant


def _jetons_chiffres(texte_normalise: str, strict: bool) -> list[tuple[str, int | None]]:
    """Découpe en jetons (mot, valeur) : la valeur d'un nombre écrit en
    chiffres ou en lettres, None pour un mot ordinaire. Une suite de mots-
    nombres ne forme qu'un jeton. `strict` : « un », « une », « neuf » seuls
    ne comptent comme nombres que suivis d'une unité."""
    bruts = re.findall(r"\d+|[a-z]+", texte_normalise)
    jetons: list[tuple[str, int | None]] = []
    i = 0
    while i < len(bruts):
        mot = bruts[i]
        if mot.isdigit():
            jetons.append((mot, int(mot)))
            i += 1
            continue
        if mot in NOMBRES_EN_LETTRES:
            j = i
            suite: list[str] = []
            while j < len(bruts) and (
                bruts[j] in NOMBRES_EN_LETTRES
                or (bruts[j] == "et" and suite and suite[-1] in DIZAINES_ET
                    and j + 1 < len(bruts) and bruts[j + 1] in ("un", "une", "onze"))
            ):
                suite.append(bruts[j])
                j += 1
            suivant = bruts[j] if j < len(bruts) else ""
            ambigu = all(m in NOMBRES_AMBIGUS or m == "et" for m in suite)
            if strict and ambigu and suivant not in UNITES and not (suivant == "h"):
                jetons.extend((m, None) for m in suite)
            else:
                jetons.append((" ".join(suite), _valeur_en_lettres(suite)))
            i = j
            continue
        jetons.append((mot, None))
        i += 1
    return jetons


def _horaires(jetons: list[tuple[str, int | None]]) -> list[tuple[str, tuple[int, int]]]:
    """Heures et durées en heures : « 21h10 », « 22 heures 30 »,
    « vingt-deux heures et demie », « 24 heures » → (texte, (h, min))."""
    horaires = []
    for k, (mot, valeur) in enumerate(jetons):
        if valeur is None or k + 1 >= len(jetons) or jetons[k + 1][0] not in ("h", "heure", "heures"):
            continue
        minutes = 0
        if k + 2 < len(jetons):
            suivant, valeur_suivante = jetons[k + 2]
            if valeur_suivante is not None and valeur_suivante < 60:
                minutes = valeur_suivante
            elif suivant == "et" and k + 3 < len(jetons) and jetons[k + 3][0] in ("demie", "quart"):
                minutes = 30 if jetons[k + 3][0] == "demie" else 15
        libelle = f"{mot}h{minutes:02d}" if mot.isdigit() else f"{mot} heures" + (f" {minutes}" if minutes else "")
        horaires.append((libelle, (valeur, minutes)))
    return horaires


# Durées hors heures (traitées par _horaires) : « un an » n'est pas « deux
# ans », même si le chiffre 1 figure ailleurs dans la référence.
UNITES_DUREE = {
    "an": "an", "ans": "an", "annee": "an", "annees": "an", "mois": "mois",
    "semaine": "semaine", "semaines": "semaine", "jour": "jour", "jours": "jour",
    "nuit": "nuit", "nuits": "nuit", "minute": "minute", "minutes": "minute",
    "seconde": "seconde", "secondes": "seconde",
}


def _durees(jetons: list[tuple[str, int | None]]) -> list[tuple[str, tuple[int, str]]]:
    durees = []
    for k, (mot, valeur) in enumerate(jetons):
        if valeur is not None and k + 1 < len(jetons) and jetons[k + 1][0] in UNITES_DUREE:
            durees.append((f"{mot} {jetons[k + 1][0]}", (valeur, UNITES_DUREE[jetons[k + 1][0]])))
    return durees


def horaires(texte: str) -> set[tuple[int, int]]:
    """Heures citées dans un texte, quelle que soit leur écriture :
    « 07h50 », « 7 h 50 », « sept heures cinquante » → {(7, 50)}."""
    return {h for _, h in _horaires(_jetons_chiffres(_normaliser(texte), strict=True))}


def _mots_propres(texte: str) -> list[str]:
    """Mots qui désignent probablement un nom propre : tout en capitales, ou
    à majuscule initiale hors début de phrase."""
    mots = []
    for m in RE_MOT.finditer(texte):
        mot = m.group(0)
        lettres = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", mot)
        if len(lettres) < 2:
            continue
        avant = texte[: m.start()].rstrip()
        debut_de_phrase = not avant or avant[-1] in FINS_DE_PHRASE
        if lettres.isupper() or (lettres[0].isupper() and not debut_de_phrase):
            mots.append(mot)
    return mots


def elements_absents(texte: str, reference: str) -> list[str]:
    """Liste les éléments de `texte` introuvables dans `reference` : noms et
    lieux, nombres (en chiffres ou en lettres), heures, approximations
    (« une vingtaine »), nature des faits et stade de la procédure. Vide :
    le texte n'invente aucun élément identifiant."""
    ref = _normaliser(reference)
    txt = _normaliser(texte)
    mots_ref = set(re.findall(r"[a-z0-9]+", ref))
    jetons_ref = _jetons_chiffres(ref, strict=False)
    jetons_txt = _jetons_chiffres(txt, strict=True)
    nombres_ref = {v for _, v in jetons_ref if v is not None}
    horaires_ref = {h for _, h in _horaires(jetons_ref)}

    absents: list[str] = []
    for mot, valeur in jetons_txt:
        if valeur is not None and valeur not in nombres_ref:
            absents.append(mot)
    absents += [libelle for libelle, h in _horaires(jetons_txt) if h not in horaires_ref]
    durees_ref = {d for _, d in _durees(jetons_ref)}
    absents += [libelle for libelle, d in _durees(jetons_txt) if d not in durees_ref]
    approximations_ref = {m.rstrip("s") for m in mots_ref if m.rstrip("s") in APPROXIMATIONS}
    for mot, _ in jetons_txt:
        if mot.rstrip("s") in APPROXIMATIONS and mot.rstrip("s") not in approximations_ref:
            absents.append(mot)
    for motif in TERMES_A_SOURCER:
        trouve = motif.search(txt)
        if trouve and not motif.search(ref):
            absents.append(trouve.group(0))
    for mot in _mots_propres(texte):
        for partie in re.split(r"['’-]", _normaliser(mot)):
            if len(partie) >= 2 and partie not in mots_ref and partie not in MOTS_GENERIQUES:
                absents.append(mot)
                break
    return list(dict.fromkeys(absents))


# Une période dont les deux bornes sont identiques (« entre mars 2031 et
# mars 2031 ») : vrai en apparence, vide de sens, et le signe d'une phrase
# fabriquée à partir d'un gabarit.
RE_PERIODE_VIDE = re.compile(r"\bentre (?:le |les |l')?(.{3,40}?) et (?:le |les |l')?\1\b", re.IGNORECASE)


def problemes_redaction(texte: str, reference: str) -> list[str]:
    """Tout ce qui interdit d'afficher un texte rédigé par le modèle, en clair
    (pour le journal, ou pour le signaler au modèle lors d'un second essai).
    Vide : le texte peut être affiché."""
    problemes = []
    if contient_qualification(texte):
        problemes.append("qualification juridique")
    if contient_jugement(texte):
        problemes.append("jugement sur la sincérité ou la culpabilité")
    if "[" in texte or "]" in texte:
        problemes.append("élément à compléter entre crochets")
    if RE_PERIODE_VIDE.search(texte):
        problemes.append("période dont les deux bornes sont identiques")
    absents = elements_absents(texte, reference)
    if absents:
        problemes.append("éléments absents des sources : " + ", ".join(absents[:10]))
    return problemes
