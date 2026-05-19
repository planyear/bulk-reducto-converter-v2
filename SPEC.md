# Bulk Document → Markdown Converter — SPEC

This document is a complete, self-contained build specification. An implementer (human or AI) given only this file should be able to recreate the entire working service.

## 1. Goal

A stateless HTTP service that accepts a batch of mixed-type documents, converts each to a single `.md` file, and returns a zip of all results. Must run comfortably on Render Standard (2 GB RAM, 1 CPU) with no GPU and the smallest possible API-key surface.

The system has exactly one user-facing capability: drag-and-drop (or POST) one or more files, get back a zip of `.md` files.

## 2. Functional requirements (must all be satisfied)

1. **One endpoint** `POST /convert` accepts a `multipart/form-data` body with one or more files in the field name `files`. It returns a single `application/zip` response.
2. **One web page** at `GET /` serves a drag-and-drop UI that calls `POST /convert` and triggers a browser download of the response.
3. **One health endpoint** `GET /health` returns `{"status":"ok","ocr":"<docling|reducto>"}`.
4. **OCR backend is selectable** by the `OCR` environment variable: `docling` (default) or `reducto`. No code change required to switch.
5. **No Reducto API key is required** when `OCR=docling` *unless* the user wants automatic fallback for scanned PDFs.
6. **Office files** (`.docx`, `.xlsx`, `.xlsm`) are converted with pure-Python libraries (`python-docx`, `openpyxl`). No external API, no LibreOffice, no CloudConvert.
7. **Markdown files** pass through unchanged (apart from UTF-8 normalization).
8. **Plaintext / CSV** are read directly and emitted as markdown without going through any OCR step.
9. **Per-file failures are isolated** — one bad file does not fail the whole batch. Failures are appended to `errors.txt` inside the output zip.
10. **The service fits Render Standard** (2 GB RAM / 1 CPU) under the acceptance test in §14.

## 3. Supported input types (the authoritative routing table)

The implementation MUST contain a single dispatch table keyed by the lowercased file extension. Anything not in this table is recorded as an error and skipped.

| Category  | Extensions                                          | Handler module                   | Output rule                                                                                  |
| --------- | --------------------------------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------- |
| Markdown  | `.md`, `.markdown`                                  | `app/handlers/passthrough.py`    | UTF-8 read, write unchanged                                                                  |
| Plaintext | `.txt`                                              | `app/handlers/plaintext.py`      | UTF-8 read with `errors="replace"`, write unchanged                                          |
| CSV       | `.csv`                                              | `app/handlers/csv_.py`           | one pipe-delimited markdown row per CSV row                                                  |
| Word      | `.docx`                                             | `app/handlers/docx_.py`          | paragraphs as text blocks; tables as pipe-delimited rows; double-newline between blocks      |
| Excel     | `.xlsx`, `.xlsm`                                    | `app/handlers/xlsx_.py`          | one `## Sheet: <name>` heading per worksheet; rows as pipe-delimited markdown rows           |
| PDF       | `.pdf`                                              | `app/handlers/ocr.py`            | OCR backend (§5)                                                                             |
| Images    | `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`   | `app/handlers/ocr.py`            | OCR backend (§5)                                                                             |

**Explicitly NOT supported** (no lightweight pure-Python path exists):

- `.doc`, `.xls`, `.ppt` — legacy binary OLE formats
- `.pptx`
- `.odt`, `.ods`, `.odp`
- `.rtf`, `.html`, `.xml`, archives, audio, video

These produce a per-file error: `unsupported file type: <ext>`. The rest of the batch still succeeds.

## 4. Handler output formats (exact — implement byte-for-byte)

All handlers return a single `str` containing UTF-8 Markdown. The orchestrator writes that string to `<safe-name>.md`.

### 4.1 `passthrough` — `.md`, `.markdown`

```python
def convert(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
```

### 4.2 `plaintext` — `.txt`

```python
def convert(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
```

### 4.3 `csv_` — `.csv`

Output: one Markdown table-style row per CSV row, no header underline. Pipes inside cells are escaped as `\|`.

```python
import csv
from pathlib import Path

def convert(path: Path) -> str:
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            cells = [(c or "").replace("|", "\\|") for c in row]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
```

### 4.4 `docx_` — `.docx`

Output: each non-empty paragraph as its own block; each table as one pipe-delimited row per table row. Blocks joined by `\n\n`.

```python
from pathlib import Path
from docx import Document

def convert(path: Path) -> str:
    doc = Document(str(path))
    blocks: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)
    for table in doc.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)
```

### 4.5 `xlsx_` — `.xlsx`, `.xlsm`

Output: one `## Sheet: <title>` heading per worksheet, followed by one pipe-delimited row per non-empty data row. Use `read_only=True, data_only=True` to keep memory low and read formula results rather than formulas.

```python
from pathlib import Path
from openpyxl import load_workbook

def convert(path: Path) -> str:
    wb = load_workbook(str(path), data_only=True, read_only=True)
    sections: list[str] = []
    for ws in wb.worksheets:
        sections.append(f"## Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if all(v is None or str(v).strip() == "" for v in row):
                continue
            cells = ["" if v is None else str(v).replace("|", "\\|") for v in row]
            sections.append("| " + " | ".join(cells) + " |")
        sections.append("")  # blank line between sheets
    wb.close()
    return "\n".join(sections).rstrip() + "\n"
```

### 4.6 `ocr` — PDFs and images

Delegates to `app/parsers.parse_pdf_like(path)` (see §5).

## 5. OCR backends

Both backends implement the same one-function interface:

```python
def parse_pdf_like(path: Path) -> str: ...
```

The dispatcher in `app/parsers.py` reads `settings.OCR` once at import time and selects which implementation to use.

### 5.1 Docling backend (default)

Run Docling in **text-extraction-only** mode: `do_ocr=False`. This avoids loading the OCR models (which would push RSS past the 2 GB ceiling). Keep one `DocumentConverter` instance for the whole process lifetime — instantiating per file leaks several hundred MB.

```python
from pathlib import Path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

_pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
_converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pdf_options)},
)

def parse_docling(path: Path) -> str:
    result = _converter.convert(path)
    return result.document.export_to_markdown()
```

The single `_converter` MUST be created during FastAPI's `lifespan` startup so the first real request does not pay the model-construction cost. Models themselves must already exist on disk — they are baked into the Docker image at build time (see §11.1) — so `download_models()` at startup is just an idempotent verification.

**Fallback rule.** If `parse_docling(path)` returns an empty/whitespace-only string (scanned PDF, no text layer), the dispatcher attempts Reducto on that single file if `REDUCTO_API_KEY` is set. If the key is absent, raise:

```
ValueError("no extractable text; set REDUCTO_API_KEY to enable OCR fallback")
```

so the orchestrator records it in `errors.txt`.

### 5.2 Reducto backend

Reducto is a hosted OCR API. Auth is `Authorization: Bearer <REDUCTO_API_KEY>`. The flow is:

1. **Upload** the file. `POST {REDUCTO_API_URL}/upload` as `multipart/form-data` with a `file` part. Response JSON contains a presigned URL or document handle (call it `document_url`).
2. **Parse** the uploaded file. `POST {REDUCTO_API_URL}/parse` with JSON body:
   ```json
   {
     "document_url": "<from step 1>",
     "options": {
       "ocr_mode": "standard",
       "extraction_mode": "ocr",
       "chunking": { "chunk_mode": "page" }
     }
   }
   ```
3. The response contains `result.chunks: [{ "content": "<markdown>" , ... }]`. Concatenate `chunk.content` for all chunks, separated by `\n\n`.

Pseudocode (use `httpx`, sync inside `asyncio.to_thread` from the orchestrator):

```python
import httpx
from pathlib import Path
from app.config import settings

def parse_reducto(path: Path) -> str:
    if not settings.REDUCTO_API_KEY:
        raise RuntimeError("REDUCTO_API_KEY is required when OCR=reducto")
    headers = {"Authorization": f"Bearer {settings.REDUCTO_API_KEY}"}
    base = settings.REDUCTO_API_URL.rstrip("/")
    with httpx.Client(timeout=120.0) as c:
        with path.open("rb") as f:
            up = c.post(f"{base}/upload", headers=headers, files={"file": (path.name, f)})
        up.raise_for_status()
        document_url = up.json().get("document_url") or up.json().get("url")
        body = {
            "document_url": document_url,
            "options": {
                "ocr_mode": "standard",
                "extraction_mode": "ocr",
                "chunking": {"chunk_mode": "page"},
            },
        }
        for attempt in range(3):
            r = c.post(f"{base}/parse", headers=headers, json=body)
            if r.status_code < 500 and r.status_code != 429:
                break
            import time; time.sleep(0.75 * (2 ** attempt))
        r.raise_for_status()
        chunks = r.json().get("result", {}).get("chunks", [])
    return "\n\n".join((c.get("content") or "").strip() for c in chunks).strip()
```

If Reducto's actual schema differs (field names like `chunks` vs `pages`, `document_url` vs `url`), the implementer adjusts to match the published Reducto docs but keeps the function signature `(path: Path) -> str` and the same retry/backoff pattern.

## 6. Orchestration (`app/jobs.py`)

The single request handler does this exact sequence. Sequential processing is non-negotiable — concurrency > 1 will OOM on Render Standard.

```python
import asyncio, shutil, tempfile, re
from pathlib import Path
from fastapi import UploadFile, HTTPException
from app.config import settings
from app.routing import HANDLERS
from app.packaging import build_zip

CHUNK = 1 << 20  # 1 MiB
SAFE = re.compile(r"[^A-Za-z0-9._-]+")

def _safe_stem(name: str) -> str:
    stem = Path(name).stem or "file"
    return SAFE.sub("_", stem)[:120] or "file"

def _unique(stem: str, used: set[str]) -> str:
    if stem not in used:
        used.add(stem); return stem
    n = 2
    while f"{stem}-{n}" in used:
        n += 1
    used.add(f"{stem}-{n}"); return f"{stem}-{n}"

async def process_batch(uploads: list[UploadFile]) -> tuple[Path, Path]:
    if len(uploads) > settings.MAX_FILES_PER_JOB:
        raise HTTPException(413, f"too many files (max {settings.MAX_FILES_PER_JOB})")
    tmp = Path(tempfile.mkdtemp(prefix="bulkconv-"))
    out = tmp / "out"; out.mkdir()
    errors: list[tuple[str, str]] = []
    try:
        # Stream uploads to disk
        staged: list[tuple[str, Path]] = []
        total = 0
        for u in uploads:
            dest = tmp / f"in_{len(staged):03d}_{_safe_stem(u.filename)}{Path(u.filename).suffix.lower()}"
            with dest.open("wb") as f:
                while chunk := await u.read(CHUNK):
                    total += len(chunk)
                    if total > settings.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "batch exceeds MAX_UPLOAD_BYTES")
                    f.write(chunk)
            staged.append((u.filename, dest))
        # Convert sequentially with a per-file timeout
        used: set[str] = set()
        for original, path in staged:
            ext = path.suffix.lower()
            handler = HANDLERS.get(ext)
            if handler is None:
                errors.append((original, f"unsupported file type: {ext or '(none)'}"))
                continue
            try:
                md = await asyncio.wait_for(asyncio.to_thread(handler, path), timeout=90)
                if not md or not md.strip():
                    raise ValueError("converter produced empty output")
                final = _unique(_safe_stem(original), used)
                (out / f"{final}.md").write_text(md, encoding="utf-8")
            except Exception as e:
                errors.append((original, f"{type(e).__name__}: {e}"))
        zip_path = build_zip(out, errors, tmp)
        return zip_path, tmp
    except:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
```

The FastAPI endpoint returns the zip with a `BackgroundTask` that calls `shutil.rmtree(tmp)`:

```python
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from datetime import datetime, timezone

@app.post("/convert")
async def convert(files: list[UploadFile] = File(...)):
    zip_path, tmp = await process_batch(files)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"converted-{stamp}.zip",
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )
```

## 7. Packaging (`app/packaging.py`)

```python
import zipfile
from pathlib import Path

def build_zip(out_dir: Path, errors: list[tuple[str, str]], tmp: Path) -> Path:
    zip_path = tmp / "result.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(out_dir.iterdir()):
            if p.is_file():
                zf.write(p, arcname=p.name)
        if errors:
            body = "\n".join(f"{name}\t{reason}" for name, reason in errors) + "\n"
            zf.writestr("errors.txt", body)
    return zip_path
```

## 8. Configuration (`app/config.py`)

Use `pydantic-settings`. Reads from environment and `.env`.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    OCR: str = "docling"                                # "docling" | "reducto"
    REDUCTO_API_KEY: str = ""
    REDUCTO_API_URL: str = "https://platform.reducto.ai"
    MAX_UPLOAD_BYTES: int = 209_715_200                 # 200 MiB
    MAX_FILES_PER_JOB: int = 50
    PER_FILE_TIMEOUT_S: int = 300                       # per-file conversion budget
    PORT: int = 8000

settings = Settings()
```

`.env.example` (committed to repo):

```
OCR=docling
REDUCTO_API_KEY=
REDUCTO_API_URL=https://platform.reducto.ai
MAX_UPLOAD_BYTES=209715200
MAX_FILES_PER_JOB=50
PER_FILE_TIMEOUT_S=300
PORT=8000
```

## 9. Routing table (`app/routing.py`)

```python
from app.handlers import passthrough, plaintext, csv_, docx_, xlsx_, ocr

HANDLERS = {
    ".md":       passthrough.convert,
    ".markdown": passthrough.convert,
    ".txt":      plaintext.convert,
    ".csv":      csv_.convert,
    ".docx":     docx_.convert,
    ".xlsx":     xlsx_.convert,
    ".xlsm":     xlsx_.convert,
    ".pdf":      ocr.convert,
    ".png":      ocr.convert,
    ".jpg":      ocr.convert,
    ".jpeg":     ocr.convert,
    ".webp":     ocr.convert,
    ".tiff":     ocr.convert,
    ".tif":      ocr.convert,
}
```

## 10. Application entry (`app/main.py`)

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.jobs import process_batch
from app.parsers import warmup_ocr

@asynccontextmanager
async def lifespan(app: FastAPI):
    warmup_ocr()    # loads Docling models (no-op when OCR=reducto)
    yield

app = FastAPI(lifespan=lifespan, title="bulk-doc-converter")

@app.get("/health")
def health():
    return {"status": "ok", "ocr": settings.OCR}

@app.get("/")
def index():
    return FileResponse(Path("frontend/index.html"))

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# /convert endpoint as defined in §6
```

`warmup_ocr()` in `app/parsers.py`:

- When `OCR=docling`: call `docling.utils.model_downloader.download_models()` (idempotent — no-op if models are already cached on disk) and instantiate the shared `DocumentConverter`. Logs success/failure visibly. Failures don't crash the app — the first PDF request will retry — but they're surfaced loudly in stdout for Render's log viewer.
- When `OCR=reducto`: log the choice and return immediately. No local warmup needed.

## 11. Frontend (`frontend/index.html`)

A single HTML file. No build step, no framework. Required behavior:

1. A drop zone covering most of the page; also a "Choose files" button that opens a file picker.
2. A list element that shows each queued file's name and status (`pending`, `uploading`, `done`, `failed`). `done`/`failed` are set only after the request completes — there is no per-file SSE.
3. A single "Convert" button that POSTs all queued files to `/convert` as `multipart/form-data` field `files`.
4. On success: trigger a browser download of the response (read response as Blob, create object URL, click a hidden `<a>` with `download="converted-<timestamp>.zip"`).
5. On HTTP error: surface the JSON `error` message in a red banner; mark all queued files as `failed`.
6. Show the configured backend (`/health` → `ocr`) in the header so the user knows whether Reducto is active.

Style: plain CSS in a `<style>` tag, no external CDN dependencies.

## 12. Memory & performance budget (Render Standard)

| Component                          | Peak RSS      | Notes                              |
| ---------------------------------- | ------------- | ---------------------------------- |
| Python + FastAPI + uvicorn idle    | ~120 MB       |                                    |
| Docling text-only model loaded     | ~600–800 MB   | one-time at startup                |
| Per-PDF parse (Docling, no OCR)    | +150–300 MB   | freed between files                |
| Per-DOCX / XLSX                    | +30–80 MB     |                                    |
| Zip streaming buffer               | ~64 KB        |                                    |
| **Headroom**                       | ~700 MB+      | for spikes and GC lag              |

Hard rules — violating any of these will OOM the service:

- **Concurrency = 1, process-wide.** A module-level `asyncio.Semaphore(1)` in `app/jobs.py` wraps every conversion call. Within a request, files run sequentially. Across requests, two simultaneous batches are serialized so two clients can never trigger two Docling parses in parallel.
- **Stream uploads to disk** in 1 MiB chunks. Never accumulate the whole batch in a `list[bytes]`.
- **Stream the zip output** to a temp file; return via `FileResponse` (never `BytesIO`).
- **Reuse one `DocumentConverter`** instance for the process lifetime.
- **Use `openpyxl` in `read_only=True, data_only=True` mode** for `.xlsx`.
- **Bake Docling models into the Docker image at build time.** Do not use a Render persistent disk for the model cache — Render mounts overlay the path, hiding image-baked files on first deploy.
- **Configurable per-file timeout** via `PER_FILE_TIMEOUT_S` env var (default 300 s). Generous enough to absorb Docling's first-call model load on top of a real parse.

## 13. Repository layout (create exactly these files)

```
bulk-reducto-converter-v2/
├── SPEC.md                       # this document
├── README.md                     # short usage notes (build + run)
├── requirements.txt              # §10 pinned versions
├── .env.example                  # §8 keys with safe defaults
├── .dockerignore                 # excludes .env, .git, __pycache__, tests/
├── Dockerfile                    # §15.1 — verbatim
├── render.yaml                   # §15.2 — verbatim
├── start.sh                      # `exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"`
├── app/
│   ├── __init__.py
│   ├── main.py                   # §10
│   ├── config.py                 # §8
│   ├── routing.py                # §9
│   ├── parsers.py                # §5 dispatcher + warmup_ocr
│   ├── jobs.py                   # §6
│   ├── packaging.py              # §7
│   └── handlers/
│       ├── __init__.py
│       ├── passthrough.py        # §4.1
│       ├── plaintext.py          # §4.2
│       ├── csv_.py               # §4.3
│       ├── docx_.py              # §4.4
│       ├── xlsx_.py              # §4.5
│       └── ocr.py                # thin wrapper calling parsers.parse_pdf_like
└── frontend/
    ├── index.html                # §11
    └── style.css                 # §11
```

## 14. Acceptance tests (definition of done)

The implementation is correct when, on a freshly deployed Render Standard instance with `OCR=docling` and no `REDUCTO_API_KEY`:

1. **Mixed-batch happy path.** A 10-file batch (mix of `.pdf` with embedded text, `.docx`, `.xlsx`, `.csv`, `.txt`, `.md`) totalling ~80 MB returns a zip with 10 `.md` files and no `errors.txt` in under 2 minutes.
2. **Memory ceiling.** Peak RSS during that batch stays under 1.6 GB (≥ 400 MB headroom under the 2 GB plan limit).
3. **Partial failure.** A batch containing `.doc`, `.pptx`, and `.zip` alongside valid files returns a zip with the valid files converted *and* an `errors.txt` listing the rejected ones.
4. **Health & cold start.** `GET /health` returns `{"status":"ok","ocr":"docling"}` within 30 s of the container starting.
5. **OCR fallback (optional).** With `REDUCTO_API_KEY` set, a scanned PDF in an otherwise-Docling batch is converted via Reducto and appears in the output zip.
6. **Reducto-only mode.** With `OCR=reducto` and a valid key, the same 10-file batch (with one scanned PDF added) completes successfully.

## 15. Build artifacts (commit these verbatim)

### 15.1 `Dockerfile`

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCLING_ARTIFACTS_PATH=/root/.cache/docling

WORKDIR /app

# System deps Docling needs for image/PDF backends. No LibreOffice — Office files use python-docx/openpyxl.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake Docling models into the image. The build fails loudly if download breaks —
# we never want to ship an image that has to fetch ~258 MB of weights inside the
# first user request.
RUN python -c "from docling.utils.model_downloader import download_models; download_models()" \
    && du -sh /root/.cache/docling \
    && ls /root/.cache/docling/models

COPY . .

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "start.sh"]
```

### 15.2 `render.yaml`

```yaml
services:
  - type: web
    name: bulk-doc-converter
    runtime: docker
    plan: standard            # 2 GB RAM, 1 CPU
    healthCheckPath: /health
    autoDeploy: true
    envVars:
      - key: OCR
        value: docling
      - key: REDUCTO_API_KEY
        sync: false           # optional; only required for OCR=reducto or scanned-PDF fallback
      - key: REDUCTO_API_URL
        value: https://platform.reducto.ai
      - key: MAX_UPLOAD_BYTES
        value: "209715200"
      - key: MAX_FILES_PER_JOB
        value: "50"
      - key: PER_FILE_TIMEOUT_S
        value: "300"
```

### 15.3 `requirements.txt` (pinned)

```
fastapi==0.115.6
uvicorn[standard]==0.30.6
python-multipart==0.0.20
pydantic==2.10.6
pydantic-settings==2.7.1
httpx==0.28.1
python-docx==1.1.2
openpyxl==3.1.5
docling>=2.0.0,<3
```

These are the only runtime dependencies. Do not add: `cloudconvert`, `reportlab`, `img2pdf`, `Pillow` (Docling pulls it transitively if needed), `pandas`, `mammoth`, any OCR SDK other than Docling.

### 15.4 `start.sh`

```sh
#!/bin/sh
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

### 15.5 `.dockerignore`

```
.git
.gitignore
.env
__pycache__
*.pyc
tests/
.venv
.vscode
.idea
```

## 16. Recreation steps (run in order)

An implementer building from scratch:

1. **Scaffold the directory layout** exactly as §13.
2. **Write `requirements.txt`, `.env.example`, `Dockerfile`, `render.yaml`, `start.sh`, `.dockerignore`** verbatim from §15.
3. **Implement `app/config.py`** from §8.
4. **Implement each handler in `app/handlers/`** from §4. One file per handler; each exports `convert(path: Path) -> str`.
5. **Implement `app/parsers.py`** with `parse_docling`, `parse_reducto`, and a dispatcher `parse_pdf_like(path)` that picks based on `settings.OCR`. Also export `warmup_ocr()` per §10. Use Python's `logging` module — never silently swallow Docling errors.
6. **Implement `app/handlers/ocr.py`** as a one-liner: `convert = parse_pdf_like` (or a thin wrapper).
7. **Implement `app/routing.py`** from §9.
8. **Implement `app/packaging.py`** from §7.
9. **Implement `app/jobs.py`** from §6 — including the module-level `asyncio.Semaphore(1)` and configurable timeout via `settings.PER_FILE_TIMEOUT_S`.
10. **Implement `app/main.py`** from §10 — including `logging.basicConfig(... force=True)` so module loggers reach Render's log viewer.
11. **Build `frontend/index.html` and `frontend/style.css`** per §11.
12. **Local smoke test:**
    ```sh
    pip install -r requirements.txt
    cp .env.example .env
    uvicorn app.main:app --reload
    # then in a browser: http://localhost:8000
    ```
13. **Docker smoke test:**
    ```sh
    docker build -t bulk-conv .
    docker run --rm -p 8000:8000 -e OCR=docling bulk-conv
    ```
14. **Run the acceptance batch from §14.1** against the local server using `curl`:
    ```sh
    curl -sS -X POST http://localhost:8000/convert \
        -F "files=@sample.pdf" -F "files=@sample.docx" -F "files=@sample.xlsx" \
        -F "files=@sample.csv" -F "files=@sample.txt" -F "files=@sample.md" \
        -o out.zip
    unzip -l out.zip
    ```
15. **Deploy** by pushing the repo to GitHub and pointing Render at `render.yaml`. Verify `/health` returns the expected JSON and re-run the acceptance batch against the deployed URL.

## 17. Error model

- **Per-file errors** are isolated: any single-file exception (handler error, timeout, empty output, unsupported extension, Reducto 4xx/5xx) is captured as `(original_filename, "<ExceptionType>: <message>")` and the loop continues. The zip is still returned 200 OK and contains `errors.txt`.
- **Whole-request errors** (no files, payload too large, malformed multipart, file count over `MAX_FILES_PER_JOB`) return standard 4xx with JSON `{"error": "<message>"}`.
- **Server errors** during orchestration (disk full, OOM-style failures the process can catch) return 500 with `{"error": "<message>"}` and clean up the temp dir.

## 18. Explicitly out of scope (v1)

- Persistent job queue, polling, SSE progress, websockets.
- Authentication or per-user quotas — assume the service sits behind a trusted gateway or is private.
- Output formats other than Markdown.
- Any OCR engine other than Docling (local) and Reducto (hosted).
- Office formats outside `.docx` / `.xlsx` / `.xlsm`.
- Image format conversion or pre-processing.
