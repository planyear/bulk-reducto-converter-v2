import logging
import mimetypes
import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .jobs import process_files
from .packaging import build_zip, make_archive_name

# python:3.11-slim has no /etc/mime.types, so .js falls back to text/plain
# and browsers with strict MIME checking refuse to execute it.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/svg+xml", ".svg")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bulk-reducto")


app = FastAPI(title="Bulk Reducto Converter")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok", "ocr": settings.ocr}


@app.post("/convert")
async def convert(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

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
        items.append(
            (
                f.filename or "unnamed",
                f.content_type or "application/octet-stream",
                data,
            )
        )

    logger.info(
        "CONVERT received n=%s total_bytes=%s ocr=%s",
        len(items),
        total,
        settings.ocr,
    )

    try:
        results = await process_files(items)
    except Exception as e:
        logger.exception("Conversion run failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

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
