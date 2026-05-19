import time
from pathlib import Path
from typing import Callable

import httpx

from app.config import settings

_ASSETS = Path(__file__).parent / "assets"
_WARMUP_PDF = _ASSETS / "warmup.pdf"

_docling_converter = None


def _get_docling_converter():
    global _docling_converter
    if _docling_converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
        _docling_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)},
        )
    return _docling_converter


def parse_docling(path: Path) -> str:
    converter = _get_docling_converter()
    result = converter.convert(path)
    return result.document.export_to_markdown()


def parse_reducto(path: Path) -> str:
    if not settings.REDUCTO_API_KEY:
        raise RuntimeError("REDUCTO_API_KEY is required when OCR=reducto")
    headers = {"Authorization": f"Bearer {settings.REDUCTO_API_KEY}"}
    base = settings.REDUCTO_API_URL.rstrip("/")

    with httpx.Client(timeout=120.0) as client:
        with path.open("rb") as f:
            up = client.post(f"{base}/upload", headers=headers, files={"file": (path.name, f)})
        up.raise_for_status()
        payload = up.json()
        document_url = payload.get("document_url") or payload.get("url") or payload.get("file_id")
        if not document_url:
            raise RuntimeError(f"Reducto upload returned no document handle: {payload}")

        body = {
            "document_url": document_url,
            "options": {
                "ocr_mode": "standard",
                "extraction_mode": "ocr",
                "chunking": {"chunk_mode": "page"},
            },
        }
        last: httpx.Response | None = None
        for attempt in range(3):
            r = client.post(f"{base}/parse", headers=headers, json=body)
            last = r
            if r.status_code < 500 and r.status_code != 429:
                break
            time.sleep(0.75 * (2 ** attempt))
        assert last is not None
        last.raise_for_status()
        chunks = last.json().get("result", {}).get("chunks", [])

    return "\n\n".join((c.get("content") or "").strip() for c in chunks).strip()


def parse_pdf_like(path: Path) -> str:
    backend = settings.OCR.lower()
    if backend == "reducto":
        return parse_reducto(path)
    if backend == "docling":
        md = parse_docling(path)
        if md and md.strip():
            return md
        if settings.REDUCTO_API_KEY:
            return parse_reducto(path)
        raise ValueError("no extractable text; set REDUCTO_API_KEY to enable OCR fallback")
    raise RuntimeError(f"unknown OCR backend: {settings.OCR!r}")


def warmup_ocr() -> None:
    if settings.OCR.lower() != "docling":
        return
    if not _WARMUP_PDF.exists():
        return
    try:
        parse_docling(_WARMUP_PDF)
    except Exception:
        pass
