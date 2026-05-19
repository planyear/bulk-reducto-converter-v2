FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake Docling models into the image at build time. The build fails loudly if
# the download breaks — we never want to ship an image that has to fetch ~258 MB
# of weights inside the first user request.
RUN python -c "from docling.utils.model_downloader import download_models; download_models()" \
    && echo "Docling model cache:" \
    && du -sh /root/.cache/docling \
    && ls /root/.cache/docling/models

COPY . .

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "start.sh"]
