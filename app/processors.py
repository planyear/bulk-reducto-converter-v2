import asyncio
import base64
import io
import os
import pathlib
import tempfile

import httpx
import img2pdf
from PIL import Image  # noqa: F401
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from .config import settings

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
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            td,
            str(in_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"LibreOffice convert failed: {out.decode(errors='ignore')}"
            )

        expected = in_path.with_suffix(".pdf")
        if expected.exists():
            return expected.read_bytes()

        pdfs = list(pathlib.Path(td).glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("Converted PDF not found")
        return pdfs[0].read_bytes()


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
            raise RuntimeError(
                f"No files in CloudConvert export result: {export_tasks[0]}"
            )

        file_url = files[0]["url"]

        pdf_resp = await client.get(file_url)
        pdf_resp.raise_for_status()
        return pdf_resp.content
