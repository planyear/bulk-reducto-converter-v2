import logging
import shutil
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app.auth import SESSION_COOKIE_NAME, get_authenticated_user
from app.auth_routes import router as auth_router
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
app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok", "ocr": settings.OCR}


@app.get("/")
def index(request: Request):
    # Explicit redirect (does not depend on Accept header) so that a fresh
    # browser visit on Render — where the proxy might normalize headers —
    # always lands on the branded /sign-in page when unauthenticated.
    if not request.cookies.get(SESSION_COOKIE_NAME):
        return RedirectResponse(url="/sign-in", status_code=302)
    try:
        get_authenticated_user(request)
    except HTTPException:
        # Cookie present but invalid/expired/tampered → clear it and send to sign-in.
        response = RedirectResponse(url="/sign-in", status_code=302)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response
    return FileResponse(_FRONTEND / "index.html")


class _AssetsOnly(StaticFiles):
    """Static mount that refuses to serve HTML — index.html and login.html
    must only be reachable through their authenticated FastAPI routes."""

    async def get_response(self, path, scope):
        if path.lower().endswith(".html"):
            return Response(status_code=404)
        return await super().get_response(path, scope)


app.mount("/static", _AssetsOnly(directory=str(_FRONTEND)), name="static")


@app.post("/convert")
async def convert(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_authenticated_user),
):
    zip_path, tmp = await process_batch(files)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"converted-{stamp}.zip",
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )


_JSON_API_PATHS = {"/convert", "/me"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # 401 on a JSON-API path → JSON 401 (frontend handles the redirect).
    # 401 on any other path → 302 to /sign-in so a plain browser visit always
    # lands on the branded sign-in page, regardless of Accept header (which
    # some reverse proxies may normalize).
    if exc.status_code == 401 and request.url.path not in _JSON_API_PATHS:
        return RedirectResponse(url="/sign-in", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
