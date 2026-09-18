# Image d'hébergement du serveur web (web/backend). Installe aussi Tesseract
# et Ghostscript : sans eux, ocrmypdf plante dès qu'une page scannée du
# dossier n'a pas de texte natif — un hébergeur Python standard ne les
# fournit pas d'office.
FROM python:3.11-slim

# Sans ça, la sortie standard est mise en mémoire tampon par blocs dès
# qu'elle n'est pas connectée à un terminal (systématique dans un conteneur) :
# tous les console.print() du pipeline (ingest/classify/chrono/decl/build),
# lancé en tâche de fond pour chaque dossier, restaient invisibles dans les
# logs Render — la seule fenêtre sur les vraies erreurs de traitement en
# production (clé LLM invalide, réponse mal formée...). Seuls les logs HTTP
# d'uvicorn passaient, car ils empruntent un mécanisme de journalisation
# différent, avec vidage explicite.
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    ghostscript \
    qpdf \
    unpaper \
    pngquant \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY web/backend/requirements.txt web/backend/requirements.txt

RUN pip install --no-cache-dir -r web/backend/requirements.txt \
    && pip install --no-cache-dir -e .

COPY web/backend/app web/backend/app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir web/backend --host 0.0.0.0 --port ${PORT:-8000}"]
