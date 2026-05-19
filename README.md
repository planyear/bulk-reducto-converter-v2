# Bulk Reducto Converter

A FastAPI service with an embedded single-page UI that batch-converts a mixed bag of documents into a single ZIP archive of Markdown files.

## Features

- **Drag-and-drop SPA** at `/` — no build step, plain HTML/CSS/JS.
- **One real endpoint**: `POST /convert` accepts a multipart upload of many files and streams back a ZIP of `.md` files.
- **Heterogeneous inputs supported**:
  - PDF (passthrough)
  - Microsoft Office (`.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`)
  - Macro-enabled Excel (`.xlsm`)
  - OpenDocument (`.odt`, `.ods`, `.odp`)
  - Images (`image/*` — JPEG, PNG, BMP, GIF, WebP, …)
  - Plain text, CSV, Markdown (`text/plain`, `text/csv`, `text/markdown`)
- **Pluggable OCR backend** chosen at startup via the `OCR` env var:
  - `reducto` — Reducto SaaS (per-page chunks, `## Page N` headings).
  - `docling` — local `docling` library, no network.
- **Office → PDF strategy**: CloudConvert when `CLOUDCONVERT_API_KEY` is set, otherwise headless LibreOffice (installed in the Docker image).
- **Bounded concurrency** via `asyncio.Semaphore(MAX_CONCURRENCY)`.
- **Per-file isolation**: a bad file becomes a line in `errors.txt` inside the returned ZIP; the rest of the batch still succeeds.
- **Conservative retries** on Reducto transient failures (429 / 5xx, 3 attempts, exponential backoff from 0.75s).
- **Health probe** at `/health` returning `{"status":"ok","ocr":"<backend>"}`.
- **Server-side upload cap** via `MAX_UPLOAD_BYTES` (default 200 MiB).
- **No persistence** — no DB, no job queue, no session store; everything is in-memory per request.

## Run locally

Python 3.11 is required (the pinned `docling` and `reportlab` versions assume it).

```sh
pip install -r requirements.txt
cp .env.example .env       # then edit values (set REDUCTO_API_KEY if using OCR=reducto)
python -m uvicorn app.main:app --port 8000 --reload
```

Open <http://localhost:8000/> in a browser.

Quick check:

```sh
curl http://localhost:8000/health
# -> {"status":"ok","ocr":"reducto"}
```

> If you keep `OCR=reducto`, the LibreOffice fallback for Office files is only available inside the Docker image. For Office file support outside Docker, install LibreOffice locally (`soffice` on `PATH`) or set `CLOUDCONVERT_API_KEY`.

## Run with Docker

```sh
docker build -t brc .
docker run --rm -p 8000:8000 \
  -e OCR=reducto \
  -e REDUCTO_API_URL=https://api.reducto.ai \
  -e REDUCTO_API_KEY=... \
  -e CLOUDCONVERT_API_KEY=... \
  brc
```

To switch backends, restart with `-e OCR=docling` (Reducto vars become optional).

## Deploy on Render

This repo ships a `render.yaml` Blueprint.

1. Push the repo to GitHub.
2. In the Render dashboard, click **New → Blueprint** and point it at the repo.
3. Render reads `render.yaml`, builds the Dockerfile, and provisions a web service.
4. Fill in the secret env vars marked `sync: false` in the Render UI:
   - `REDUCTO_API_KEY`
   - `CLOUDCONVERT_API_KEY` (optional)
5. Render injects `PORT`; `start.sh` already honors it.
6. The health check at `/health` is wired up automatically.

To switch the deployed backend, edit `OCR` (`reducto` or `docling`) in the Render env settings and redeploy.

## Environment variables

| Variable                | Required             | Default               | Purpose                                                |
| ----------------------- | -------------------- | --------------------- | ------------------------------------------------------ |
| `OCR`                   | no                   | `reducto`             | `reducto` or `docling` (case-insensitive).             |
| `REDUCTO_API_URL`       | yes if `OCR=reducto` | —                     | Reducto base URL, e.g. `https://api.reducto.ai`.       |
| `REDUCTO_API_KEY`       | yes if `OCR=reducto` | —                     | Bearer token for Reducto.                              |
| `CLOUDCONVERT_API_KEY`  | no                   | —                     | When set, used for Office/ODF/`.xlsm` → PDF.           |
| `DOCLING_DO_OCR`        | no                   | `true`                | Enable Docling's OCR pass for scanned PDFs.            |
| `MAX_CONCURRENCY`       | no                   | `5`                   | `asyncio.Semaphore` size for per-file workers.         |
| `MAX_UPLOAD_BYTES`      | no                   | `209715200` (200 MiB) | Hard cap on combined upload size.                      |
| `PORT`                  | no                   | `8000`                | uvicorn bind port (read by `start.sh`).                |
