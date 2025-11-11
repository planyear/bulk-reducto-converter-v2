# app/processors.py

import asyncio
import io
import os
import json
import pathlib
import tempfile
import base64
from typing import Optional, Dict, List, Any

import httpx
# pillow not strictly required by img2pdf; imported if you later need image manipulation
from PIL import Image  # noqa: F401
import img2pdf
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from googleapiclient.discovery import Resource  # type: ignore

from .config import settings

# =========================
# Google Drive helpers
# =========================

def drive_export_pdf(service: Resource, file_id: str) -> bytes:
    """
    Export a Google Docs/Sheets/Slides file to PDF using Drive API.
    Caller must supply the Drive service object.
    """
    # This returns raw bytes (not a MediaIoBaseDownload stream)
    return service.files().export(
        fileId=file_id, mimeType="application/pdf"
    ).execute()

# =========================
# Conversion helpers
# =========================

PDF_MIME = "application/pdf"
GOOGLE_MIME_PREFIX = "application/vnd.google-apps"

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


def _has_libreoffice() -> bool:
    """Return True if libreoffice/soffice is on PATH."""
    for name in ("soffice", "libreoffice"):
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if (pathlib.Path(p) / name).exists():
                return True
    return False


async def _convert_with_libreoffice(input_bytes: bytes, input_name: str) -> bytes:
    """
    Use LibreOffice (headless) to convert Office/OpenDocument to PDF.
    """
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


def _image_to_pdf(image_bytes: bytes) -> bytes:
    """Wrap a single image into a PDF page without rasterizing (img2pdf)."""
    return img2pdf.convert([image_bytes])


def _text_to_pdf(text_bytes: bytes) -> bytes:
    """
    Simple text/CSV/Markdown to PDF using reportlab.
    This is intentionally minimal—good enough for readable output.
    """
    text = text_bytes.decode("utf-8", errors="ignore")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    x = 72  # 1in left margin
    y = height - 72
    for line in text.splitlines():
        c.drawString(x, y, line[:110])  # crude wrapping
        y -= 14
        if y < 72:
            c.showPage()
            y = height - 72
    c.save()
    return buf.getvalue()


async def _cloudconvert_office_to_pdf(
    input_bytes: bytes,
    input_name: str,
    mime: str,
) -> bytes:
    """
    Convert Office-ish files (including .xlsm) to PDF via CloudConvert.
    Requires CLOUDCONVERT_API_KEY in settings.
    """
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
        # 1) create job
        resp = await client.post(
            "https://api.cloudconvert.com/v2/jobs",
            json=job_def,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        job_id = data["id"]

        # 2) poll until job is finished
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

        # 3) download the resulting PDF
        pdf_resp = await client.get(file_url)
        pdf_resp.raise_for_status()
        return pdf_resp.content


async def to_pdf_bytes(
    *,
    name: str,
    src_bytes: bytes,
    mime: str,
    drive_service: Optional[Resource] = None,
    drive_file_id: Optional[str] = None,
) -> bytes:
    """
    Convert arbitrary content to PDF bytes.
    - application/pdf                 -> passthrough
    - application/vnd.google-apps.*   -> Drive export to PDF (requires drive_service + file_id)
    - Office/OpenDocument mimes       -> CloudConvert (if configured) or LibreOffice
    - image/*                         -> image -> PDF
    - text/plain/csv/markdown         -> text -> PDF
    Raises RuntimeError if unsupported and cannot convert on this host.
    """
    if mime == PDF_MIME:
        return src_bytes

    if mime.startswith(GOOGLE_MIME_PREFIX):
        if not (drive_service and drive_file_id):
            raise RuntimeError("Google file export needs drive_service and drive_file_id")
        return drive_export_pdf(drive_service, drive_file_id)

    if mime in OFFICE_MIMES:
        # Prefer CloudConvert if available
        if settings.cloudconvert_api_key:
            return await _cloudconvert_office_to_pdf(src_bytes, name, mime)

        # Fallback: use LibreOffice if available locally
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


# Backward-compatible shim: if your code still calls office_to_pdf(...)
async def office_to_pdf(name: str, src_bytes: bytes, mime: str) -> bytes:
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
    raise RuntimeError(f"office_to_pdf cannot handle mime={mime}")


# =========================
# Reducto API helpers
# =========================

def _base_url() -> str:
    return settings.reducto_api_url.rstrip("/")


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.reducto_api_key}"}


async def _post_with_retries(
    url: str,
    *,
    json: dict | None = None,
    files=None,
    headers=None,
    attempts: int = 3,
):
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


async def _get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def reducto_clean_pdf(pdf_bytes: bytes) -> str:
    """
    Upload PDF to Reducto, parse with page chunking, and return a readable text.
    Handles large-document signed URL responses transparently.
    """
    base = _base_url()
    headers = _auth_headers()

    # 1) Upload
    upload_url = f"{base}/upload"
    files = {"file": ("doc.pdf", io.BytesIO(pdf_bytes), PDF_MIME)}
    up = await _post_with_retries(upload_url, files=files, headers=headers)
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
    pr = await _post_with_retries(
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
                big = await _get_json(signed_url)
                chunks = big.get("chunks")

    # 4) Build clean text
    if not chunks:
        # Fallback to raw text if provided
        if isinstance(parsed, dict) and "text" in parsed:
            return parsed["text"]
        return json.dumps(parsed, ensure_ascii=False, indent=2)

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
            pages.append(f"----- Page {page_no} -----\n{content}\n")

    cleaned_text = "\n".join(pages).strip()
    return cleaned_text or "(Empty document)"
