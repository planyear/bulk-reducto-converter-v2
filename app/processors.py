# app/processors.py

import io
import json
import httpx
import asyncio
from typing import Optional, Dict, Any, List
from .config import settings

PDF_MIME = "application/pdf"

# ---------- helpers ----------

def _base_url() -> str:
    return settings.reducto_api_url.rstrip("/")

def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.reducto_api_key}"}

async def _post_with_retries(url: str, *, json: dict | None = None, files=None, headers=None, attempts: int = 3):
    backoff = 0.75
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(attempts):
            resp = await client.post(url, json=json, files=files, headers=headers)
            # 429/5xx retry
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

# ---------- public API used by jobs.py ----------

async def office_to_pdf(name: str, src_bytes: bytes, mime: str) -> bytes:
    """
    If later you integrate CloudConvert or LibreOffice, convert Office here.
    For now we assume PDFs are already PDFs and this is a placeholder.
    """
    raise NotImplementedError("office_to_pdf is not wired yet in this sample")

async def reducto_clean_pdf(pdf_bytes: bytes) -> str:
    """
    Upload PDF to Reducto, parse with page chunking, clean to a readable .txt string.
    Handles large-doc signed URL case.
    """
    base = _base_url()
    headers = _auth_headers()

    # 1) /upload → returns {'file_id': 'reducto://...pdf', ...}
    upload_url = f"{base}/upload"
    files = {"file": ("doc.pdf", io.BytesIO(pdf_bytes), PDF_MIME)}
    up = await _post_with_retries(upload_url, files=files, headers=headers)
    up_data = up.json()

    document_url = up_data.get("url") or up_data.get("document_url") or up_data.get("file_id")
    if not document_url:
        raise RuntimeError(f"Reducto /upload missing url/file_id field: {up_data}")

    # 2) /parse with options (page chunking, rotate_pages, page markers)
    parse_url = f"{base}/parse"
    body = {
        "document_url": document_url,
        "options": {"chunking": {"chunk_mode": "page"}},
        "experimental_options": {"rotate_pages": True},
        "advanced_options": {"add_page_markers": True},
    }
    pr = await _post_with_retries(parse_url, json=body, headers={**headers, "Content-Type": "application/json"})
    parsed = pr.json()

    # 3) Prefer inline chunks; if absent, follow signed URL (large docs)
    result = parsed.get("result") if isinstance(parsed, dict) else {}
    chunks: Optional[List[Dict[str, Any]]] = None

    if isinstance(result, dict):
        chunks = result.get("chunks")
        if not chunks:
            signed_url = result.get("url")
            if signed_url:
                # Reducto puts the whole JSON at this signed S3 URL
                big = await _get_json(signed_url)
                chunks = big.get("chunks")

    # 4) Build clean text
    if not chunks:
        # Fallback to raw text if API returns plain 'text'
        if isinstance(parsed, dict) and "text" in parsed:
            return parsed["text"]
        # otherwise dump JSON as-is (rare)
        return json.dumps(parsed, ensure_ascii=False, indent=2)

    # chunks[i].content holds page text; page number derivation from blocks[].bbox.page
    pages: List[str] = []
    for ch in chunks:
        if not ch:
            continue
        content = ch.get("content", "").strip()
        # Try to read page number from blocks; default to 1-based index if missing
        page_no = None
        blocks = ch.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            bbox = (blocks[0] or {}).get("bbox") or {}
            page_no = bbox.get("page")
        if page_no is None:
            page_no = len(pages) + 1

        if content:
            pages.append(f"----- Page {page_no} -----\n{content}\n")

    cleaned_text = "\n".join(pages).strip()
    return cleaned_text or "(Empty document)"
