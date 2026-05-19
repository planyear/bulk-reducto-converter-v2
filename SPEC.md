# SPEC.md — Bulk Reducto Converter

A reconstruction-grade specification for the `bulk-reducto-converter` repository.
Reading this document in isolation must be sufficient to rebuild the system from
scratch with behavior equivalent to the design defined here.

---

## 1. Executive Overview

### 1.1 Purpose

**Bulk Reducto Converter** is a FastAPI web service with an embedded
single-page frontend that batch-converts a heterogeneous set of local documents
selected by the user into a single downloadable **ZIP archive of Markdown
(`.md`) files**.

The conversion happens in two stages:

1. **Normalize-to-PDF.** Every supported input format is converted to PDF in
   memory: PDFs are passed through; Microsoft Office, OpenDocument and `.xlsm`
   files are routed through CloudConvert (when an API key is configured) or
   fall back to a local headless LibreOffice; images are wrapped into a PDF
   page with `img2pdf`; plain text / CSV / Markdown are rendered with ReportLab.
2. **PDF → Markdown.** The PDF is then handed to one of two interchangeable
   OCR/parsing backends, selected at startup by the `OCR` entry in the `.env`:
   - **Reducto** — third-party SaaS, returns per-page chunks formatted with
     page markers.
   - **Docling** — open-source local library (`docling`), returns markdown
     directly via `DocumentConverter`.

The resulting Markdown files are bundled into a single ZIP and streamed back
to the browser. Job state is purely transient — the request holds the
connection open until processing finishes, then the ZIP is returned.

### 1.2 Primary Use Cases

1. An analyst drops a mixed bag of PDFs, Excel files (incl. macro-enabled
   `.xlsm`), Word docs, images and `.txt`/`.md` files onto the page and gets
   back a ZIP of cleaned Markdown — one `.md` per source file.
2. Operations team toggles the `OCR` setting between `reducto` and `docling`
   in `.env` to choose between cloud-hosted (higher quality, paid) and
   local (free, offline) text extraction without redeploying the application.

### 1.3 Mental Model (End-to-End)

```
Browser
  │
  ▼
GET / ────────────────► serves frontend/index.html (+ /static/* assets)
  │
  ▼
User drags / selects files (multi-file <input> + drop zone)
  │
  ▼
POST /convert  (multipart/form-data, field name "files", many)
  │
  ▼
parse_upload(files): list[(name, mime, bytes)]
  │
  ▼
process_files(items):
  Semaphore(MAX_CONCURRENCY)
  asyncio.gather(*[ _process_one(item) for item in items ])
     │
     ▼
     _process_one(name, mime, src_bytes):
        pdf_bytes = await to_pdf_bytes(name, src_bytes, mime)
            ├ application/pdf            → passthrough
            ├ OFFICE_MIMES (+ xlsm)      → CloudConvert | LibreOffice
            ├ image/*                    → img2pdf
            └ text/{plain,csv,markdown}  → ReportLab
        markdown = await parse_pdf(pdf_bytes, name)
            ├ settings.ocr == "reducto"  → reducto_clean_pdf(pdf_bytes)
            └ settings.ocr == "docling"  → docling_clean_pdf(pdf_bytes, name)
        return FileResult(name, markdown, status="ok")
  │
  ▼
build_zip(results) → bytes (ZIP archive in memory)
  │
  ▼
StreamingResponse(zip_bytes, media_type="application/zip",
                  headers={"Content-Disposition": "attachment; filename=converted_YYYY-MM-DD_HHMMSS.zip"})
  ▲
  │   (HTTP connection stayed open during processing)
  │
Browser: spinner stops; blob is saved automatically via the
download attribute on a temporary anchor.
```

### 1.4 Major Components

| Module                | Responsibility                                                                |
| --------------------- | ----------------------------------------------------------------------------- |
| `app/__init__.py`     | Empty — marks `app` as a package.                                             |
| `app/main.py`         | FastAPI app, route declarations, frontend mount, JSON error envelope.         |
| `app/config.py`       | `Settings` pydantic model loaded from environment variables via `python-dotenv`. Defines the `OCR` switch. |
| `app/processors.py`   | "Normalize-to-PDF" router: PDF passthrough, CloudConvert, LibreOffice, image, text.|
| `app/parsers.py`      | OCR backends. Defines `parse_pdf`, `reducto_clean_pdf`, `docling_clean_pdf`.  |
| `app/jobs.py`         | `_process_one` worker, `process_files` semaphore-bounded fan-out, in-memory `FileResult` model. |
| `app/packaging.py`    | Builds the ZIP archive (`build_zip`) and the auto-generated archive filename. |
| `frontend/index.html` | The single-page UI.                                                            |
| `frontend/styles.css` | Minimal modern styling (system font stack, light/dark friendly).               |
| `frontend/app.js`     | Vanilla JS: drag-and-drop, file list, submit-and-download.                     |

### 1.5 Execution Model

- Single Python process running **uvicorn**, ASGI, async I/O.
- All conversion happens **inline in the request handler**: the HTTP connection
  is held open until every file has been processed and the ZIP has been
  assembled. Frontend shows a spinner until the response arrives.
- Per-file work is fanned out concurrently with `asyncio.gather` under an
  `asyncio.Semaphore` (default cap 5, configurable via `MAX_CONCURRENCY`).
- No database, no message queue, no background worker, no session store. There
  is no persistent job state at all — when the response is sent, every byte
  related to the request is garbage-collected.

### 1.6 External Services

| Service                    | When Used                              | Auth                                            |
| -------------------------- | -------------------------------------- | ----------------------------------------------- |
| Reducto API                | When `OCR=reducto`                      | `Authorization: Bearer ${REDUCTO_API_KEY}`      |
| CloudConvert API (opt.)    | Office / ODF / `.xlsm` → PDF (if key set)| `Authorization: Bearer ${CLOUDCONVERT_API_KEY}` |
| Docling (local library)    | When `OCR=docling`                      | None — runs in-process.                          |
| LibreOffice (local binary) | Office / ODF → PDF fallback             | None — subprocess on host.                       |

### 1.7 Design Philosophy

- **Tiny surface area.** One real endpoint, `POST /convert`. The frontend lives
  in `frontend/` and is served as static files.
- **"Spinner-friendly" HTTP.** Awaiting the full conversion inside the request
  handler is intentional, not an oversight — keeps the frontend trivial and
  removes the need for a polling/status endpoint.
- **Pluggable OCR.** The `OCR` env-var switch is the only thing that decides
  Reducto vs. Docling; both code paths produce equivalent Markdown output.
- **Graceful degradation for Office conversion.** Prefer CloudConvert when
  configured, otherwise local LibreOffice; the Docker image installs
  LibreOffice unconditionally so the fallback always works in the container.
- **No persistence.** A run-to-failure on any file does not stop the batch:
  the bad file ends up with an error entry in a small `errors.txt` inside the
  returned ZIP.
- **Conservative HTTP retries** only on transient Reducto failures
  (429 + 5xx, 3 attempts, exponential backoff starting at 0.75s).

---

## 2. Repository Layout

```
bulk-reducto-converter/
├── .dockerignore           ( 9 entries)
├── .gitignore              (excludes .env, .venv, __pycache__, etc.)
├── Dockerfile
├── requirements.txt        (pinned)
├── start.sh                (POSIX /bin/sh entrypoint)
├── app/
│   ├── __init__.py         (empty — package marker)
│   ├── main.py             (FastAPI app + routes + static mounts)
│   ├── config.py           (Settings, OCR switch)
│   ├── processors.py       (normalize-to-PDF router)
│   ├── parsers.py          (Reducto + Docling backends, dispatcher)
│   ├── jobs.py             (semaphore-bounded worker fan-out)
│   └── packaging.py        (ZIP builder)
└── frontend/
    ├── index.html
    ├── styles.css
    └── app.js
```

The repository contains **no** database files, **no** template engine, **no**
tests directory, **no** `pyproject.toml`, **no** `docker-compose.yml`.

---

## 3. Runtime & Deployment

### 3.1 Container Image (`Dockerfile`)

```dockerfile
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

COPY . .
RUN chmod +x /app/start.sh

ENV PYTHONUNBUFFERED=1

CMD ["/app/start.sh"]
```

Notes:

- Base image is `python:3.11-slim`. Reproductions MUST use Python 3.11.x because
  `docling` and `reportlab` versions are pinned against that minor.
- `libreoffice` is installed unconditionally so the LibreOffice fallback is
  available even when `CLOUDCONVERT_API_KEY` is not configured.
- `fonts-dejavu` is required so LibreOffice headless does not emit blank PDFs
  due to a missing default font.
- `PYTHONUNBUFFERED=1` makes uvicorn logs visible to container log collectors
  in real time.

### 3.2 Entrypoint Script (`start.sh`)

```sh
#!/bin/sh
set -e

cd /app

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
```

Behavior:

1. `set -e` — abort on any non-zero exit.
2. `cd /app` — guarantees the working directory regardless of how the container
   was invoked.
3. `exec python -m uvicorn app.main:app` — replaces the shell so PID 1 is uvicorn.
4. Port comes from `$PORT` (defaulting to 8000); compatible with PaaS hosts
   (e.g. Render, Fly, Heroku) that inject `PORT`.
5. `--proxy-headers --forwarded-allow-ips="*"` — trusts `X-Forwarded-*` from any
   upstream proxy so generated URLs are correct behind a TLS-terminating LB.

The frontend is started together with the backend: there is no separate
frontend dev server. Browsing to `http://localhost:8000/` after the container
boots loads the single-page UI directly.

### 3.3 `.dockerignore`

```
.git
.venv
__pycache__/
*.pyc
*.pyo
*.pyd
.env
.DS_Store
```

`start.sh`, `app/`, `frontend/`, and `requirements.txt` are intentionally **not**
ignored.

### 3.4 `.gitignore` (Highlights)

Must include at minimum:

- Python: `__pycache__/`, `*.pyc`, `*.pyo`, `*.pyd`, `*.so`, `*.egg-info`,
  `dist/`, `build/`.
- Virtual envs: `.venv/`, `venv/`, `env/`.
- OS/IDE: `.DS_Store`, `Thumbs.db`, `.idea/`, `.vscode/`, `*.code-workspace`.
- Logs: `logs/`, `*.log`, `uvicorn*.log`.
- Type checkers: `.mypy_cache/`, `.pyright/`, `.dmypy.json`, `.pyre/`,
  `.pytest_cache/`.
- Secrets: **`.env`**, `*.env`, `.env.*`, `.envrc`.

The critical entry is `.env` — it holds API keys and the `OCR` selection.

### 3.5 Local Development (No Container)

```sh
pip install -r requirements.txt
cp .env.example .env   # then edit values
python -m uvicorn app.main:app --port 8000 --reload
# open http://localhost:8000/
```

`python-dotenv` is called at module import time in `config.py`, so a
project-root `.env` file is auto-loaded.

---

## 4. Dependencies (Pinned Versions)

`requirements.txt` must contain exactly the following pinned versions (header
comments preserved for clarity):

```
# FastAPI core
fastapi==0.115.0
uvicorn[standard]==0.30.5
pydantic==2.9.2
python-multipart==0.0.9
python-dotenv==1.0.1

# HTTP client (Reducto + CloudConvert)
httpx==0.27.2

# Async utilities
anyio==4.4.0

# PDF synthesis
img2pdf==0.5.1
reportlab==4.2.2
Pillow==10.4.0

# OCR / parsing backends
docling==1.16.0
```

Notes:

- `Pillow` is imported by `processors.py` (`from PIL import Image  # noqa: F401`)
  as a defensive import — kept so future image manipulation can be added
  without a dependency change.
- `docling` is always installed even when `OCR=reducto`. This keeps the image
  uniform and makes the `OCR` switch a runtime decision with no build-time
  branching.
- No Google API libraries, no Authlib, no `requests` — those are not part of
  this system.

---

## 5. Configuration & Environment Variables

### 5.1 `Settings` Class (`app/config.py`)

```python
from pydantic import BaseModel
import os
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseModel):
    # OCR backend selection: "reducto" or "docling" (case-insensitive)
    ocr: str = (os.getenv("OCR") or "reducto").strip().lower()

    # CloudConvert (optional — used by Office / ODF / .xlsm → PDF)
    cloudconvert_api_key: str | None = os.getenv("CLOUDCONVERT_API_KEY")

    # Reducto (required when OCR=reducto)
    reducto_api_url: str | None = os.getenv("REDUCTO_API_URL")
    reducto_api_key: str | None = os.getenv("REDUCTO_API_KEY")

    # Docling (optional tuning — defaults are sensible)
    docling_do_ocr: bool = (os.getenv("DOCLING_DO_OCR") or "true").strip().lower() in ("1", "true", "yes", "on")

    # Processing controls
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "5"))

    # Upload guard (combined request body cap)
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))  # 200 MiB total


settings = Settings()
```

Key invariants:

- **`load_dotenv()` runs at import time.** Importing `app.config` reads the
  project-root `.env` file before evaluating any of the field defaults.
- The default values are computed at **class definition time**, not per-instance.
  Changing env vars after the module is first imported has no effect.
- The `OCR` value is normalized to lowercase. Valid values are `"reducto"` and
  `"docling"`. Any other value causes `parse_pdf` to raise a clear error at
  request time.
- `MAX_UPLOAD_BYTES` is the combined byte limit for the multipart request
  (defaults to 200 MiB).

### 5.2 `.env.example`

A `.env.example` file MUST exist at the repository root so a fresh clone is
self-describing:

```dotenv
# ===== OCR backend selection =====
# Allowed values: reducto | docling
OCR=reducto

# ===== Reducto (required when OCR=reducto) =====
REDUCTO_API_URL=https://api.reducto.ai
REDUCTO_API_KEY=

# ===== CloudConvert (optional — used to convert Office/ODF/.xlsm to PDF) =====
# If unset, the app falls back to local LibreOffice (installed in the Docker image).
CLOUDCONVERT_API_KEY=

# ===== Docling tuning (optional, only relevant when OCR=docling) =====
# Enable OCR pass inside Docling for scanned PDFs.
DOCLING_DO_OCR=true

# ===== Processing controls =====
MAX_CONCURRENCY=5
MAX_UPLOAD_BYTES=209715200
```

### 5.3 Complete Environment Variable Reference

| Variable                | Required        | Default                | Purpose                                                     |
| ----------------------- | --------------- | ---------------------- | ----------------------------------------------------------- |
| `OCR`                   | NO              | `reducto`              | Selects parsing backend. `"reducto"` or `"docling"` (case-insensitive). |
| `REDUCTO_API_URL`       | YES if `OCR=reducto` | —                | Base URL for the Reducto API (e.g. `https://api.reducto.ai`). |
| `REDUCTO_API_KEY`       | YES if `OCR=reducto` | —                | Bearer token sent on all Reducto requests.                   |
| `CLOUDCONVERT_API_KEY`  | NO              | —                      | When set, used for Office/ODF/`.xlsm` → PDF; otherwise LibreOffice is used. |
| `DOCLING_DO_OCR`        | NO              | `true`                 | Enables Docling's OCR pass for scanned PDFs.                |
| `MAX_CONCURRENCY`       | NO              | `5`                    | `asyncio.Semaphore` size for per-file workers.              |
| `MAX_UPLOAD_BYTES`      | NO              | `209715200` (200 MiB)  | Hard cap on total multipart upload size.                    |
| `PORT`                  | NO              | `8000`                 | uvicorn bind port (read by `start.sh`).                     |

---

## 6. HTTP API & Routes

### 6.1 Application Bootstrap (`app/main.py`)

```python
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .jobs import process_files
from .packaging import build_zip, make_archive_name


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bulk-reducto")


app = FastAPI(title="Bulk Reducto Converter")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
```

Key facts:

- `FastAPI(title="Bulk Reducto Converter")` — default Swagger UI at `/docs` is
  left in place for ad-hoc API exploration; it is unauthenticated. Operators
  who want it disabled can pass `docs_url=None, redoc_url=None`.
- The root logger is reconfigured at INFO level, format
  `"%(asctime)s %(levelname)s %(name)s: %(message)s"`, writing to STDOUT.
- The whole `frontend/` directory is mounted at `/static/` so `app.js`,
  `styles.css` and any future assets are served as-is. `index.html` is
  returned by a dedicated `GET /` handler so the page lives at the root URL.

### 6.2 Route Inventory

| Method | Path        | Purpose                                                              |
| ------ | ----------- | -------------------------------------------------------------------- |
| GET    | `/`         | Serves `frontend/index.html`.                                        |
| GET    | `/static/*` | Static assets (mounted via `StaticFiles`).                           |
| GET    | `/health`   | Liveness probe — returns `{"status": "ok", "ocr": <settings.ocr>}`.  |
| POST   | `/convert`  | Multipart upload of one or more files. Returns a ZIP archive of `.md` files.|

There is no other endpoint. There is no auth layer. Operators that need access
control are expected to put the service behind a reverse proxy.

### 6.3 Route Handlers

#### `GET /`

```python
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html", media_type="text/html")
```

Returns the single HTML page. Browser caching is left to FastAPI defaults
(no explicit `Cache-Control`).

#### `GET /health`

```python
@app.get("/health")
async def health():
    return {"status": "ok", "ocr": settings.ocr}
```

Used by container orchestrators and by the frontend on initial load to display
the active OCR backend in the page header.

#### `POST /convert`

```python
@app.post("/convert")
async def convert(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # 1) Read uploads into memory with a combined size cap
    items: list[tuple[str, str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes} bytes)",
            )
        items.append((f.filename or "unnamed", f.content_type or "application/octet-stream", data))

    logger.info("CONVERT received n=%s total_bytes=%s ocr=%s", len(items), total, settings.ocr)

    # 2) Process every file concurrently (bounded)
    try:
        results = await process_files(items)
    except Exception as e:
        logger.exception("Conversion run failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # 3) Pack results into a ZIP
    zip_bytes = build_zip(results)
    archive_name = make_archive_name()

    logger.info(
        "CONVERT done n=%s ok=%s errors=%s archive=%s size=%s",
        len(results),
        sum(1 for r in results if r.status == "ok"),
        sum(1 for r in results if r.status == "error"),
        archive_name,
        len(zip_bytes),
    )

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{archive_name}"'},
    )
```

Contract details:

- Request body is **`multipart/form-data`** with a repeated form field named
  `files` (matching the frontend `<input name="files" multiple>`).
- The combined size of all uploads must not exceed `MAX_UPLOAD_BYTES`,
  otherwise the request fails fast with HTTP 413.
- The handler **awaits** the entire batch before returning. There is no
  separate status endpoint.
- A whole-batch exception is surfaced as HTTP 500 with `detail=str(e)`.
  Per-file exceptions are caught inside `_process_one` and ride along inside
  the returned ZIP as error stubs (see § 9.3).

### 6.4 JSON Error Envelope

All non-2xx responses are returned by FastAPI's default `HTTPException`
handler, i.e. `{"detail": "<message>"}`. No custom error wrapper is added.

---

## 7. Frontend

The frontend is intentionally minimal: vanilla HTML/CSS/JS, no build step, no
bundler, no framework. It must look modern out of the box on a clean install.

### 7.1 `frontend/index.html`

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bulk Reducto Converter</title>
  <link rel="stylesheet" href="/static/styles.css" />
</head>
<body>
  <main class="shell">
    <header class="hero">
      <h1>Bulk Reducto Converter</h1>
      <p class="sub">Drop files in, download a ZIP of Markdown out.</p>
      <p class="badge" id="ocr-badge">OCR: …</p>
    </header>

    <section id="dropzone" class="dropzone" tabindex="0" aria-label="File drop zone">
      <input id="picker" name="files" type="file" multiple hidden />
      <p class="dz-text">
        <strong>Drag &amp; drop</strong> files here, or
        <button type="button" id="browse">browse</button>.
      </p>
      <p class="dz-hint">PDF, Word, Excel (incl. .xlsm), PowerPoint, images, .txt / .csv / .md</p>
    </section>

    <ul id="filelist" class="filelist" aria-live="polite"></ul>

    <div class="actions">
      <button id="convert" type="button" disabled>Convert &amp; download ZIP</button>
      <button id="clear" type="button" class="ghost" disabled>Clear</button>
    </div>

    <div id="overlay" class="overlay" hidden>
      <div class="spinner" aria-hidden="true"></div>
      <p class="overlay-text">Converting your files…</p>
    </div>

    <div id="error" class="error" hidden role="alert"></div>
  </main>

  <script src="/static/app.js" defer></script>
</body>
</html>
```

### 7.2 `frontend/styles.css`

Required visual properties (a reconstructor may match these values exactly or
preserve their behavioral intent):

- System font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif`.
- Page background: a subtle linear gradient (light theme), with a
  `prefers-color-scheme: dark` rule that flips backgrounds to near-black and
  text to near-white.
- Card-style centered shell, max-width `720px`, rounded `16px` corners.
- Dropzone with a dashed `2px` border that switches to solid on `.dragover`.
- Primary button: filled, accent color (e.g. `#2563eb`), 8px radius,
  `:disabled` reduces opacity to `0.5` and disables pointer events.
- Spinner: pure CSS `border-top: 3px solid currentColor` rotating animation,
  shown inside `#overlay`.
- `.error`: red background tint, padded, rounded.

Any cohesive minimal modern stylesheet that honors these properties is
acceptable.

### 7.3 `frontend/app.js`

Behavioral specification (the exact JS may vary as long as every bullet holds):

1. On `DOMContentLoaded`, fetch `/health` and write the JSON `ocr` field into
   `#ocr-badge` as `OCR: reducto` or `OCR: docling`.
2. Maintain a single in-memory `File[]` array. The frontend never uploads
   speculatively.
3. **Drag-and-drop:**
   - `dragenter` / `dragover` on `#dropzone` → `preventDefault` and add
     `.dragover` class.
   - `dragleave` → remove `.dragover`.
   - `drop` → call `addFiles(event.dataTransfer.files)`.
4. **Browse:** clicking `#browse` triggers `#picker.click()`. On the picker's
   `change` event, call `addFiles(picker.files)` and reset the input.
5. `addFiles(list)` deduplicates by `(name, size, lastModified)`, appends to
   the in-memory array, and re-renders `#filelist`. Each `<li>` shows the
   filename, human-readable size, and a × button that removes the entry.
6. The `#convert` and `#clear` buttons are disabled iff the array is empty.
7. **Submit:**
   - Show `#overlay`.
   - Build a `FormData`, append each file under the field name `files`.
   - `fetch("/convert", {method: "POST", body: fd})`.
   - On `response.ok`:
     - Read `response.blob()`.
     - Read filename from `Content-Disposition` header; fall back to
       `converted.zip`.
     - Create a temporary `<a>` with `href = URL.createObjectURL(blob)` and
       `download = filename`; click it; revoke the URL.
   - On non-OK: read `response.json()` and render `detail` into `#error`.
   - Always hide `#overlay` afterwards.
8. After a successful download, the file list is **not** cleared automatically
   (so the user can re-submit with adjustments). The "Clear" button empties
   the array and re-renders.
9. Network and parse errors are caught and rendered into `#error`. The overlay
   never gets stuck visible.

The JS is plain ES2020, no transpilation needed. Modules are not required;
a single `app.js` is sufficient.

---

## 8. Worker Pipeline

### 8.1 `FileResult` (`app/jobs.py`)

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class FileResult:
    source_name: str
    output_name: str           # the name placed inside the ZIP
    status: str                # "ok" | "error"
    content: Optional[bytes]   # UTF-8 encoded markdown when status == "ok"
    message: Optional[str]     # error message when status == "error"
```

A `dataclass` is used (not Pydantic) because nothing is serialized to JSON over
the wire — the only consumer is `build_zip`.

### 8.2 Output File Naming

The output filename for a source file `foo.docx` is computed by:

```python
import re

def make_output_name(src_name: str, ext: str = ".md") -> str:
    stem = re.sub(r"\.[^.]+$", "", src_name)              # strip last extension
    stem = re.sub(r"[\\/<>:*?\"|]+", "_", stem).strip()   # sanitize unsafe chars
    return f"{stem}{ext}"
```

Properties:

- Strips the **last** dot-extension only.
- Sanitizes the set `\\ / < > : * ? " |` to underscore (NTFS-incompatible chars).
- Default extension is `".md"`.
- If two source files would produce the same output name, `build_zip`
  disambiguates by appending `_2`, `_3`, … (see § 11).

### 8.3 Single-File Worker (`_process_one`)

```python
import logging
from .processors import to_pdf_bytes, PDF_MIME
from .parsers import parse_pdf

logger = logging.getLogger("bulk-reducto.jobs")


async def _process_one(name: str, mime: str, src_bytes: bytes) -> FileResult:
    out_name = make_output_name(name)
    logger.info("START name=%s mime=%s size=%s", name, mime, len(src_bytes))

    try:
        pdf_bytes = await to_pdf_bytes(name=name, src_bytes=src_bytes, mime=mime)
        if mime == PDF_MIME:
            logger.info("PDF passthrough name=%s size=%s", name, len(pdf_bytes))
        else:
            logger.info("CONVERTED -> PDF name=%s pdf_size=%s", name, len(pdf_bytes))

        markdown = await parse_pdf(pdf_bytes, name)
        logger.info("PARSED name=%s md_len=%s", name, len(markdown))

        return FileResult(
            source_name=name,
            output_name=out_name,
            status="ok",
            content=markdown.encode("utf-8"),
            message=None,
        )
    except Exception as e:
        logger.exception("ERROR processing name=%s", name)
        return FileResult(
            source_name=name,
            output_name=out_name,
            status="error",
            content=None,
            message=str(e),
        )
```

Sequence:

1. Compute `out_name` (used regardless of success/failure so the error stub
   has a sensible name).
2. **Normalize-to-PDF** via `to_pdf_bytes` (§ 9).
3. **Parse to Markdown** via `parse_pdf` (§ 10).
4. Return an `ok` result containing UTF-8 markdown, or an `error` result with
   the exception message. Per-file failures **do not** abort the batch.

### 8.4 Batch Fan-Out (`process_files`)

```python
import asyncio
from typing import Iterable
from .config import settings


async def process_files(items: Iterable[tuple[str, str, bytes]]) -> list[FileResult]:
    items = list(items)
    sem = asyncio.Semaphore(int(settings.max_concurrency or 1))

    async def bounded(name: str, mime: str, data: bytes) -> FileResult:
        async with sem:
            return await _process_one(name, mime, data)

    return list(await asyncio.gather(*[bounded(n, m, d) for (n, m, d) in items]))
```

Notes:

- Order of the returned list **matches the input order** (because `asyncio.gather`
  preserves it). This is a deliberate guarantee so the ZIP's file order is
  predictable.
- `int(... or 1)` protects against accidental `0` / `None`.
- One semaphore guards both heavy local CPU work (LibreOffice/Docling) and
  network-bound work (Reducto/CloudConvert). A single bound is good enough.

---

## 9. Normalize-to-PDF Router (`app/processors.py`)

### 9.1 Constants

```python
PDF_MIME = "application/pdf"
XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"

OFFICE_MIMES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    XLSM_MIME,
}

TEXT_LIKE = {
    "text/plain",
    "text/csv",
    "text/markdown",
}
```

`OFFICE_MIMES` covers: `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.odt`,
`.ods`, `.odp`, and `.xlsm`.

### 9.2 Dispatch

```python
import asyncio
import io
import os
import json
import pathlib
import tempfile
import base64

import httpx
from PIL import Image  # noqa: F401
import img2pdf
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from .config import settings


async def to_pdf_bytes(*, name: str, src_bytes: bytes, mime: str) -> bytes:
    """Normalize any supported input to PDF bytes."""
    if mime == PDF_MIME:
        return src_bytes

    if mime in OFFICE_MIMES:
        if settings.cloudconvert_api_key:
            return await _cloudconvert_office_to_pdf(src_bytes, name, mime)
        if not _has_libreoffice():
            raise RuntimeError(
                "LibreOffice is not available on this host and CloudConvert is not configured"
            )
        return await _convert_with_libreoffice(src_bytes, name)

    if mime.startswith("image/"):
        return _image_to_pdf(src_bytes)

    if mime in TEXT_LIKE:
        return _text_to_pdf(src_bytes)

    raise RuntimeError(f"Unsupported content type for conversion: {mime}")
```

Order matters — `application/pdf` is checked first so PDFs are not accidentally
matched by another rule.

### 9.3 LibreOffice Probe & Conversion

```python
def _has_libreoffice() -> bool:
    for binname in ("soffice", "libreoffice"):
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if (pathlib.Path(p) / binname).exists():
                return True
    return False


async def _convert_with_libreoffice(input_bytes: bytes, input_name: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        in_path = pathlib.Path(td) / input_name
        in_path.write_bytes(input_bytes)

        proc = await asyncio.create_subprocess_exec(
            "soffice", "--headless", "--convert-to", "pdf", "--outdir", td, str(in_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"LibreOffice convert failed: {out.decode(errors='ignore')}")

        expected = in_path.with_suffix(".pdf")
        if expected.exists():
            return expected.read_bytes()

        pdfs = list(pathlib.Path(td).glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("Converted PDF not found")
        return pdfs[0].read_bytes()
```

- Probes `soffice` first (standard on Debian/Ubuntu), then `libreoffice` (shell
  wrapper). Walks `$PATH` manually.
- `stderr` is folded into `stdout` so the failure log line contains both.
- Reads the result file named `{stem}.pdf`; if missing (LibreOffice occasionally
  munges filenames containing special characters), falls back to the first
  `*.pdf` found in the temp directory.

### 9.4 Image and Text → PDF

```python
def _image_to_pdf(image_bytes: bytes) -> bytes:
    return img2pdf.convert([image_bytes])


def _text_to_pdf(text_bytes: bytes) -> bytes:
    text = text_bytes.decode("utf-8", errors="ignore")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    x = 72
    y = height - 72
    for line in text.splitlines():
        c.drawString(x, y, line[:110])
        y -= 14
        if y < 72:
            c.showPage()
            y = height - 72
    c.save()
    return buf.getvalue()
```

- Image: `img2pdf` embeds the original raster bytes into a PDF page without
  recompression or rasterization. Supported inputs include JPEG, PNG, BMP, GIF,
  WebP per img2pdf's capability surface.
- Text: ReportLab canvas, LETTER pagesize, 72pt margins (1 inch), 14pt line
  spacing, lines truncated to 110 chars, new page when `y < 72`. Default
  Helvetica 12pt.

### 9.5 CloudConvert Pipeline

```python
async def _cloudconvert_office_to_pdf(
    input_bytes: bytes,
    input_name: str,
    mime: str,
) -> bytes:
    if not settings.cloudconvert_api_key:
        raise RuntimeError("CloudConvert API key is not configured")

    api_key = settings.cloudconvert_api_key
    b64_file = base64.b64encode(input_bytes).decode("ascii")

    job_def = {
        "tasks": {
            "import-1": {
                "operation": "import/base64",
                "file": b64_file,
                "filename": input_name,
                "content_type": mime,
            },
            "convert-1": {
                "operation": "convert",
                "input": "import-1",
                "output_format": "pdf",
            },
            "export-1": {
                "operation": "export/url",
                "input": "convert-1",
            },
        }
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            "https://api.cloudconvert.com/v2/jobs",
            json=job_def,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        job_id = data["id"]

        while data.get("status") not in ("finished", "error"):
            await asyncio.sleep(2)
            poll = await client.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=headers,
                params={"include": "tasks"},
            )
            poll.raise_for_status()
            data = poll.json()["data"]

        if data.get("status") != "finished":
            raise RuntimeError(f"CloudConvert job failed: {data}")

        tasks = data.get("tasks", [])
        export_tasks = [t for t in tasks if t.get("operation") == "export/url"]
        if not export_tasks:
            raise RuntimeError(f"No export/url task in CloudConvert job: {data}")

        files = export_tasks[0].get("result", {}).get("files", [])
        if not files:
            raise RuntimeError(f"No files in CloudConvert export result: {export_tasks[0]}")

        file_url = files[0]["url"]

        pdf_resp = await client.get(file_url)
        pdf_resp.raise_for_status()
        return pdf_resp.content
```

Behavior:

- **Single HTTP client per call**, 300s timeout.
- The file is sent inline as base64; no separate upload step.
- Polling loop sleeps 2 seconds between status checks; the 300s client timeout
  is the only upper bound (per individual request).
- Errors surface as `RuntimeError` containing the offending API payload to
  ease debugging.

---

## 10. OCR / Parsing Backends (`app/parsers.py`)

The parser layer hides the choice of backend behind a single async function:

```python
async def parse_pdf(pdf_bytes: bytes, source_name: str) -> str: ...
```

It returns a **Markdown string**. UTF-8 encoding happens inside `_process_one`.

### 10.1 Dispatcher

```python
import asyncio
import io
import json
import logging
from typing import Optional, List, Dict, Any

import httpx

from .config import settings

logger = logging.getLogger("bulk-reducto.parsers")

PDF_MIME = "application/pdf"


async def parse_pdf(pdf_bytes: bytes, source_name: str) -> str:
    backend = settings.ocr
    if backend == "reducto":
        return await reducto_clean_pdf(pdf_bytes)
    if backend == "docling":
        return await docling_clean_pdf(pdf_bytes, source_name)
    raise RuntimeError(f"Unknown OCR backend in settings.ocr: {backend!r}")
```

Notes:

- The backend value is read fresh on each call so a developer who edits `.env`
  and restarts a single uvicorn `--reload` cycle picks up the change.
- Any value other than `"reducto"` / `"docling"` raises a clear, user-visible
  error during the request.

### 10.2 Reducto Backend

#### Helpers

```python
def _reducto_base_url() -> str:
    if not settings.reducto_api_url:
        raise RuntimeError("REDUCTO_API_URL is not set")
    return settings.reducto_api_url.rstrip("/")


def _reducto_auth_headers() -> Dict[str, str]:
    if not settings.reducto_api_key:
        raise RuntimeError("REDUCTO_API_KEY is not set")
    return {"Authorization": f"Bearer {settings.reducto_api_key}"}


async def _reducto_post_with_retries(url, *, json=None, files=None, headers=None, attempts: int = 3):
    backoff = 0.75
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(attempts):
            resp = await client.post(url, json=json, files=files, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp


async def _reducto_get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()
```

Retry policy: 3 attempts; retries on `429, 500, 502, 503, 504`; backoff
`0.75s → 1.5s` (only between attempts 1→2 and 2→3); POST timeout 60s; signed-URL
GET timeout 120s.

#### Main function

```python
async def reducto_clean_pdf(pdf_bytes: bytes) -> str:
    base = _reducto_base_url()
    headers = _reducto_auth_headers()

    # 1) Upload
    upload_url = f"{base}/upload"
    files = {"file": ("doc.pdf", io.BytesIO(pdf_bytes), PDF_MIME)}
    up = await _reducto_post_with_retries(upload_url, files=files, headers=headers)
    up_data = up.json()

    document_url = up_data.get("url") or up_data.get("document_url") or up_data.get("file_id")
    if not document_url:
        raise RuntimeError(f"Reducto /upload missing url/file_id field: {up_data}")

    # 2) Parse
    parse_url = f"{base}/parse"
    body = {
        "document_url": document_url,
        "options": {"chunking": {"chunk_mode": "page"}},
        "experimental_options": {"rotate_pages": True},
        "advanced_options": {"add_page_markers": True},
    }
    pr = await _reducto_post_with_retries(
        parse_url,
        json=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    parsed = pr.json()

    # 3) Prefer inline chunks; otherwise follow signed URL
    result = parsed.get("result") if isinstance(parsed, dict) else {}
    chunks: Optional[List[Dict[str, Any]]] = None

    if isinstance(result, dict):
        chunks = result.get("chunks")
        if not chunks:
            signed_url = result.get("url")
            if signed_url:
                big = await _reducto_get_json(signed_url)
                chunks = big.get("chunks")

    # 4) Build Markdown
    if not chunks:
        if isinstance(parsed, dict) and "text" in parsed:
            return parsed["text"]
        return "```json\n" + json.dumps(parsed, ensure_ascii=False, indent=2) + "\n```"

    pages: List[str] = []
    for idx, ch in enumerate(chunks, start=1):
        if not ch:
            continue
        content = (ch.get("content") or "").strip()
        page_no = None
        blocks = ch.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            bbox = (blocks[0] or {}).get("bbox") or {}
            page_no = bbox.get("page")
        if page_no is None:
            page_no = idx
        if content:
            pages.append(f"## Page {page_no}\n\n{content}\n")

    cleaned = "\n".join(pages).strip()
    return cleaned or "_(Empty document)_"
```

Reducto contract:

- `POST /upload` — multipart form upload of a PDF. Response JSON contains at
  least one of `url`, `document_url`, or `file_id` (probed in that order).
- `POST /parse` — JSON body with:
  - `document_url`: the value from `/upload`.
  - `options.chunking.chunk_mode`: `"page"` → emit one chunk per page.
  - `experimental_options.rotate_pages`: `true` → auto-rotate pages if needed.
  - `advanced_options.add_page_markers`: `true` → keep page boundaries.
- `/parse` response is either inline JSON with `result.chunks` or JSON with
  `result.url` pointing to a signed URL whose JSON body holds `chunks`.

**Output Markdown shape (Reducto):**

```markdown
## Page 1

<content of page 1>

## Page 2

<content of page 2>
…
```

Empty result → `_(Empty document)_`. Missing-chunks fallback → either
`parsed["text"]` as-is, or a JSON-fenced dump for forensic debugging.

### 10.3 Docling Backend

Docling runs in-process; there is no network call. The conversion is
synchronous (CPU-bound), so it is wrapped in `asyncio.to_thread` to avoid
blocking the event loop.

```python
def _docling_convert_sync(pdf_bytes: bytes, source_name: str) -> str:
    # Imported lazily so that startup does not pay the import cost when OCR=reducto.
    from docling.document_converter import DocumentConverter

    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        suffix = "" if source_name.lower().endswith(".pdf") else ".pdf"
        path = pathlib.Path(td) / (source_name + suffix)
        path.write_bytes(pdf_bytes)

        converter = DocumentConverter()
        result = converter.convert(str(path))
        return result.document.export_to_markdown()


async def docling_clean_pdf(pdf_bytes: bytes, source_name: str) -> str:
    md = await asyncio.to_thread(_docling_convert_sync, pdf_bytes, source_name)
    md = (md or "").strip()
    return md or "_(Empty document)_"
```

Properties:

- **Lazy import** of `docling.document_converter.DocumentConverter`. The
  library is heavy; importing it eagerly slows uvicorn boot when only Reducto
  is used. The first Docling request pays the import cost once, then it is
  cached by Python's module system.
- Writes the PDF to a temp file because Docling's `DocumentConverter.convert`
  accepts a path (or URL). The filename's `.pdf` suffix is preserved so
  Docling routes the input through its PDF pipeline.
- `DOCLING_DO_OCR` env var is exposed via `settings.docling_do_ocr`; a
  reconstruction may wire it into `PdfPipelineOptions(do_ocr=settings.docling_do_ocr)`
  before instantiating `DocumentConverter`. The minimal default form above
  (no options) uses Docling's library defaults, which include OCR for scanned
  pages.
- The function always returns Markdown. Empty output → `_(Empty document)_`.

**Output Markdown shape (Docling):** whatever
`result.document.export_to_markdown()` produces — typically Docling-flavored
Markdown with headings, lists, tables and image references. The shape differs
from Reducto's per-page headings; this is acceptable because the two backends
are deliberately not byte-identical, only equivalently useful.

---

## 11. ZIP Packaging (`app/packaging.py`)

### 11.1 Archive Filename

```python
from datetime import datetime

def make_archive_name() -> str:
    return f"converted_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"
```

Uses **local server time**. In Docker containers this is typically UTC.

### 11.2 ZIP Builder

```python
import io
import zipfile
from typing import Iterable
from .jobs import FileResult


def build_zip(results: Iterable[FileResult]) -> bytes:
    results = list(results)
    buf = io.BytesIO()
    errors_lines: list[str] = []
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            if r.status == "ok" and r.content is not None:
                name = _unique(r.output_name, seen_names)
                zf.writestr(name, r.content)
            else:
                errors_lines.append(f"{r.source_name}\t{r.message or 'unknown error'}")

        if errors_lines:
            zf.writestr("errors.txt", ("\n".join(errors_lines) + "\n").encode("utf-8"))

    return buf.getvalue()


def _unique(name: str, seen: dict[str, int]) -> str:
    if name not in seen:
        seen[name] = 1
        return name
    seen[name] += 1
    stem, _, ext = name.rpartition(".")
    if stem == "":
        return f"{name}_{seen[name]}"
    return f"{stem}_{seen[name]}.{ext}"
```

Properties:

- Compression: `ZIP_DEFLATED` (standard, broadly compatible).
- Each successful `FileResult.content` becomes a single ZIP entry whose name is
  `FileResult.output_name`, optionally disambiguated with `_2`, `_3`, …
- Failed files are aggregated into a single `errors.txt` inside the ZIP, one
  TSV-like row per failure: `<source_name>\t<message>`.
- If every file succeeds, no `errors.txt` is added.
- If every file fails, the ZIP still contains exactly `errors.txt` (no `.md`
  files).

---

## 12. Concurrency, Error Handling, Logging

### 12.1 Concurrency

- Single uvicorn worker by default (no `--workers N` passed in `start.sh`).
- The single Python event loop processes all requests; per-batch concurrency
  inside a request is bounded by `asyncio.Semaphore(MAX_CONCURRENCY)`.
- Docling work is CPU-bound and dispatched via `asyncio.to_thread`; it does
  not starve other event-loop tasks.
- Reducto/CloudConvert work is network-bound (`httpx.AsyncClient`).

### 12.2 Error Surfaces

| Error Origin                          | Caught Where           | Visible As                                                       |
| ------------------------------------- | ---------------------- | ---------------------------------------------------------------- |
| No files in request                   | `convert` handler      | HTTP 400 `"No files uploaded"`.                                  |
| Combined upload exceeds the cap       | `convert` handler      | HTTP 413 `"Upload exceeds MAX_UPLOAD_BYTES (<n> bytes)"`.        |
| Per-file conversion failure           | `_process_one` try/except | `FileResult(status="error")` → appears as a line in `errors.txt` inside the ZIP. |
| Whole-batch crash in `process_files`  | `convert` handler try/except | HTTP 500 `detail=str(e)`.                                        |
| Unknown `OCR` value                   | `parse_pdf`            | Per-file error: `Unknown OCR backend in settings.ocr: '<value>'`.|
| Missing `REDUCTO_API_URL` / `_KEY` when `OCR=reducto` | `_reducto_*` helpers | Per-file error with explicit "is not set" message.   |

### 12.3 Logging

- Root logger configured in `main.py`:
  - Level: INFO
  - Format: `%(asctime)s %(levelname)s %(name)s: %(message)s`
  - Handler: `StreamHandler(sys.stdout)` (not stderr).
- Named loggers:
  - `bulk-reducto` (in `main.py`) — request-level info.
  - `bulk-reducto.jobs` (in `jobs.py`) — per-file stages.
  - `bulk-reducto.parsers` (in `parsers.py`) — backend-specific diagnostics.
- Per-file log stages: `START`, `PDF passthrough` or `CONVERTED -> PDF`,
  `PARSED`, `ERROR processing`.
- Per-batch lines: `CONVERT received n=… total_bytes=… ocr=…`,
  `CONVERT done n=… ok=… errors=… archive=… size=…`.
- Errors use `logger.exception(...)` so the full traceback is printed.

---

## 13. Glossary

| Term                              | Meaning                                                                          |
| --------------------------------- | -------------------------------------------------------------------------------- |
| **Bulk Reducto Converter**        | Project name. Refers to "bulk conversion of documents to Markdown via a swappable OCR backend". |
| **Reducto**                       | Third-party SaaS that turns PDFs into structured per-page text via the `/upload` + `/parse` endpoints. |
| **Docling**                       | Open-source PDF/document parser library (`docling`) that runs in-process and emits Markdown. |
| **OCR (env var)**                 | The runtime switch in `.env` selecting `reducto` or `docling` as the parsing backend. |
| **normalize-to-PDF**              | The first conversion stage: any supported input MIME → PDF bytes.                 |
| **OFFICE_MIMES**                  | Hard-coded set of MIME types routed through CloudConvert / LibreOffice.           |
| **TEXT_LIKE**                     | Hard-coded set of MIME types routed through the ReportLab text-to-PDF path.       |
| **FileResult**                    | In-memory dataclass holding per-file outcome (success bytes or error message).    |
| **archive name**                  | The downloaded ZIP's filename: `converted_<YYYY-MM-DD>_<HHMMSS>.zip`.             |
| **errors.txt**                    | Per-batch error log packaged inside the returned ZIP whenever ≥1 file failed.     |

---

## 14. Invariants & Implicit Assumptions

The reconstructor MUST preserve all of these:

1. **No persistence.** No DB, no disk-backed job store. Every byte related to
   a request is discarded once the response is sent.
2. **`POST /convert` blocks until the entire batch finishes.** There is no
   separate status endpoint. Clients must tolerate long-running HTTP calls.
3. **`FileResult` order matches input order** (because `asyncio.gather`
   preserves it). Tests can rely on this.
4. **`status="error"` does not abort the batch.** Errors are aggregated into
   `errors.txt` inside the returned ZIP.
5. **`OCR` is read once at process start.** Changing the value of `OCR` in
   `.env` requires a restart (the `Settings` instance is module-level).
6. **Lazy import of Docling.** `from docling.document_converter import DocumentConverter`
   must happen inside `_docling_convert_sync`, not at module top level, so
   startup is fast when `OCR=reducto`.
7. **CloudConvert is preferred over LibreOffice when the API key is set.**
   The Docker image still installs LibreOffice unconditionally.
8. **Reducto upload extracts the document handle in the order**: `url`,
   `document_url`, `file_id`. Do not change the order.
9. **Logging goes to STDOUT, not STDERR.**
10. **Filename uniqueness inside the ZIP is enforced by `_unique`.** Two
    sources with the same sanitized stem produce `foo.md`, `foo_2.md`, …
11. **`make_archive_name` uses local-TZ `datetime.now()`,** not UTC.
12. **Reducto Markdown uses `## Page N` headings**, not text rulers — this is
    intentional so the output is real Markdown.
13. **`MAX_UPLOAD_BYTES` is enforced server-side** as a defensive guard; the
    frontend does not pre-check size.
14. **`from PIL import Image  # noqa: F401`** is intentional — keep the
    `noqa: F401` comment so linters don't strip it.

---

## 15. End-to-End Reconstruction Recipe

Build the system in this order:

1. **Create the package skeleton.**
   ```
   mkdir bulk-reducto-converter
   cd bulk-reducto-converter
   mkdir app frontend
   touch app/__init__.py
   ```
2. **Write `requirements.txt`** with the exact pinned versions from § 4.
3. **Write `.gitignore`** ensuring `.env`, `__pycache__/`, `.venv/` are excluded.
4. **Write `.dockerignore`** per § 3.3.
5. **Write `.env.example`** per § 5.2.
6. **Write `app/config.py`** per § 5.1, including the top-level `load_dotenv()`
   call and the module-level `settings = Settings()` singleton.
7. **Write `app/processors.py`** per § 9:
   - Constants block first (`PDF_MIME`, `XLSM_MIME`, `OFFICE_MIMES`, `TEXT_LIKE`).
   - `_has_libreoffice`, `_convert_with_libreoffice`, `_image_to_pdf`,
     `_text_to_pdf`, `_cloudconvert_office_to_pdf`, `to_pdf_bytes`.
8. **Write `app/parsers.py`** per § 10:
   - `parse_pdf` dispatcher, `_reducto_*` helpers + `reducto_clean_pdf`,
     `_docling_convert_sync` + `docling_clean_pdf`.
9. **Write `app/jobs.py`** per § 8:
   - `FileResult` dataclass, `make_output_name`, `_process_one`, `process_files`.
   - Logger name: `"bulk-reducto.jobs"`.
10. **Write `app/packaging.py`** per § 11.
11. **Write `app/main.py`** per § 6:
    - `FastAPI(title="Bulk Reducto Converter")`.
    - Static mount at `/static`.
    - Three explicit routes: `GET /`, `GET /health`, `POST /convert`, plus the
      implicit `GET /static/*` from `StaticFiles`.
12. **Write `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`**
    per § 7.
13. **Write `Dockerfile`** per § 3.1 (Python 3.11-slim + LibreOffice install).
14. **Write `start.sh`** per § 3.2 (POSIX `/bin/sh`, uvicorn exec).
    Mark executable.

Provision and run:

```sh
docker build -t brc .
docker run --rm -p 8000:8000 \
  -e OCR=reducto \
  -e REDUCTO_API_URL=https://api.reducto.ai \
  -e REDUCTO_API_KEY=... \
  -e CLOUDCONVERT_API_KEY=... \
  brc
# open http://localhost:8000/
```

To switch to Docling at runtime, restart with `-e OCR=docling` (Reducto vars
become optional).

---

## 16. Verification Procedure

A reconstructed system passes acceptance if all of the following hold:

1. **Boot:** `docker run …` reaches `Uvicorn running on http://0.0.0.0:8000`
   without errors.
2. **Frontend served:** `curl -s http://localhost:8000/` returns HTML
   containing `<title>Bulk Reducto Converter</title>`. `curl -s
   http://localhost:8000/static/app.js` returns the JS.
3. **Health probe:** `curl -s http://localhost:8000/health` returns
   `{"status":"ok","ocr":"reducto"}` (or `"docling"` per env).
4. **Empty request rejected:** `curl -s -o /dev/null -w "%{http_code}" -X POST
   http://localhost:8000/convert` returns `422` (FastAPI's missing-field) or
   the equivalent.
5. **Mixed-format batch (Reducto):** With `OCR=reducto`, upload these via the
   browser UI:
   - `sample.pdf` (should be passthrough)
   - `sample.xlsm` (CloudConvert or LibreOffice → PDF → Reducto)
   - `sample.png` (img2pdf → PDF → Reducto)
   - `sample.txt` (ReportLab → PDF → Reducto)

   Expected: a single ZIP is downloaded named `converted_<date>_<time>.zip`,
   containing four `.md` files with `## Page N` headings, no `errors.txt`.
6. **Same batch (Docling):** Restart with `OCR=docling` and re-upload the same
   batch. Expected: a ZIP of four `.md` files in Docling's Markdown shape
   (headings, lists, tables), no Reducto API calls in logs.
7. **Per-file isolation:** Add a corrupt/unsupported file alongside valid ones.
   Expected: ZIP contains `.md` files for the valid ones and a single
   `errors.txt` line for the bad one; HTTP status is 200.
8. **Concurrency:** With `MAX_CONCURRENCY=2`, observe at most two concurrent
   `PARSED name=…` log lines in flight.
9. **Reducto retries:** Simulate a 503 from Reducto; logs show a single
   retried POST followed by a successful one (no propagated error).
10. **Frontend UX:**
    - Drag-and-drop highlights the dropzone.
    - File list shows each item with a remove button.
    - Submit disables the button and shows the overlay.
    - On success, the ZIP downloads automatically; the overlay disappears.
    - On error, `#error` shows the message and the overlay disappears.

---

## 17. Known Quirks & Uncertainties

- **No automated tests** in the repository; behavior is defined by this spec.
- **Reducto vs. Docling output is intentionally not byte-identical.** They are
  two different parsers; equivalence is by usefulness, not by exact text.
- **`make_archive_name` timestamp is local-TZ**, not UTC. In production Docker
  containers this is typically UTC, but it should be explicit.
- **No idempotency.** Re-submitting the same batch produces a fresh ZIP with a
  new timestamped name; nothing is deduplicated server-side.
- **No rate limiting** beyond `MAX_CONCURRENCY` and `MAX_UPLOAD_BYTES`.
  Operators that need quotas are expected to add them at the proxy.
- **No auth layer.** The service is intended to run on a private network or
  behind a reverse-proxy that enforces access. Adding auth (e.g. a single
  shared bearer token via FastAPI dependency) is outside this spec.
- **`MAX_UPLOAD_BYTES` is enforced after reading.** Uploads up to that size
  are loaded into memory; reconstructors that need disk spooling should use
  FastAPI's streaming `SpooledTemporaryFile` instead.
- **HTTP connection lifetime.** Very large batches may exceed proxy idle
  timeouts. Operators typically tune proxy timeouts or split batches.

End of specification.
