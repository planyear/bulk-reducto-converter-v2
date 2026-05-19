FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
      fonts-dejavu \
      ca-certificates \
      curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Docling layout/OCR + EasyOCR detection/recognition models at
# build time. Running this in a single-threaded process avoids the
# tqdm._lock race that hits huggingface_hub's thread_map when multiple
# request threads instantiate DocumentConverter concurrently, and prevents
# a half-populated model cache from poisoning subsequent runs.
ENV HF_HOME=/root/.cache/huggingface
RUN python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"

COPY . .
RUN chmod +x /app/start.sh

ENV PYTHONUNBUFFERED=1

CMD ["/app/start.sh"]
