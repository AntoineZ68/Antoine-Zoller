# Image d'hébergement du serveur web (web/backend). Installe aussi Tesseract
# et Ghostscript : sans eux, ocrmypdf plante dès qu'une page scannée du
# dossier n'a pas de texte natif — un hébergeur Python standard ne les
# fournit pas d'office.
FROM python:3.11-slim

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
