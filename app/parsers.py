import asyncio
import io
import json
import logging
import pathlib
import tempfile
import threading
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

logger = logging.getLogger("bulk-reducto.parsers")

PDF_MIME = "application/pdf"

_docling_converter = None
_docling_lock = threading.Lock()


def _get_docling_converter():
    # Cache one DocumentConverter for the process and serialize its construction
    # so we never hit the tqdm._lock race that happens when multiple threads
    # trigger snapshot_download() concurrently inside DocumentConverter.__init__.
    global _docling_converter
    if _docling_converter is not None:
        return _docling_converter
    with _docling_lock:
        if _docling_converter is None:
            from docling.document_converter import DocumentConverter

            _docling_converter = DocumentConverter()
    return _docling_converter


def warmup_docling() -> None:
    _get_docling_converter()


async def parse_pdf(pdf_bytes: bytes, source_name: str) -> str:
    backend = settings.ocr
    if backend == "reducto":
        return await reducto_clean_pdf(pdf_bytes)
    if backend == "docling":
        return await docling_clean_pdf(pdf_bytes, source_name)
    raise RuntimeError(f"Unknown OCR backend in settings.ocr: {backend!r}")


def _reducto_base_url() -> str:
    if not settings.reducto_api_url:
        raise RuntimeError("REDUCTO_API_URL is not set")
    return settings.reducto_api_url.rstrip("/")


def _reducto_auth_headers() -> Dict[str, str]:
    if not settings.reducto_api_key:
        raise RuntimeError("REDUCTO_API_KEY is not set")
    return {"Authorization": f"Bearer {settings.reducto_api_key}"}


async def _reducto_post_with_retries(
    url, *, json=None, files=None, headers=None, attempts: int = 3
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


async def _reducto_get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def reducto_clean_pdf(pdf_bytes: bytes) -> str:
    base = _reducto_base_url()
    headers = _reducto_auth_headers()

    upload_url = f"{base}/upload"
    files = {"file": ("doc.pdf", io.BytesIO(pdf_bytes), PDF_MIME)}
    up = await _reducto_post_with_retries(upload_url, files=files, headers=headers)
    up_data = up.json()

    document_url = (
        up_data.get("url") or up_data.get("document_url") or up_data.get("file_id")
    )
    if not document_url:
        raise RuntimeError(f"Reducto /upload missing url/file_id field: {up_data}")

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

    result = parsed.get("result") if isinstance(parsed, dict) else {}
    chunks: Optional[List[Dict[str, Any]]] = None

    if isinstance(result, dict):
        chunks = result.get("chunks")
        if not chunks:
            signed_url = result.get("url")
            if signed_url:
                big = await _reducto_get_json(signed_url)
                chunks = big.get("chunks")

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


def _docling_convert_sync(pdf_bytes: bytes, source_name: str) -> str:
    converter = _get_docling_converter()

    with tempfile.TemporaryDirectory() as td:
        suffix = "" if source_name.lower().endswith(".pdf") else ".pdf"
        path = pathlib.Path(td) / (source_name + suffix)
        path.write_bytes(pdf_bytes)

        result = converter.convert(str(path))
        return result.document.export_to_markdown()


async def docling_clean_pdf(pdf_bytes: bytes, source_name: str) -> str:
    md = await asyncio.to_thread(_docling_convert_sync, pdf_bytes, source_name)
    md = (md or "").strip()
    return md or "_(Empty document)_"
