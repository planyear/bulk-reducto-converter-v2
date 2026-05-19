import logging
import shutil
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app.config import settings
from app.jobs import process_batch
from app.parsers import warmup_ocr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
log = logging.getLogger(__name__)

_FRONTEND = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("bulk-doc-converter starting (OCR=%s)", settings.OCR)
    warmup_ocr()
    yield
    log.info("bulk-doc-converter shutting down")


app = FastAPI(lifespan=lifespan, title="bulk-doc-converter")


@app.get("/health")
def health():
    return {"status": "ok", "ocr": settings.OCR}


@app.get("/")
def index():
    return FileResponse(_FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")


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


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
