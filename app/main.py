from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .auth import (
    require_user,
    login as auth_login,
    auth_callback as auth_cb,
    logout as do_logout,
    swagger_ui,
)
from .models import JobRequest
from .jobs import create_job, get_status, process_job

import logging, sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bulk-reducto")

app = FastAPI(title="Bulk Reducto Converter – Internal", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=False)

# ---- Auth routes (hidden from schema) ----
@app.get("/login", include_in_schema=False)
async def login(request: Request, next: str | None = "/docs"):
    return await auth_login(request, next)

@app.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request):
    return await auth_cb(request)

@app.get("/logout", include_in_schema=False)
async def logout(request: Request):
    return do_logout(request)

# ---- Protected Swagger (hidden from schema) ----
@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def docs(request: Request, user=Depends(require_user)):
    return swagger_ui(request, user)

# Protect OpenAPI JSON too
@app.get("/openapi.json", include_in_schema=False)
async def openapi(request: Request, user=Depends(require_user)):
    return app.openapi()

# ---- Jobs ----
# Only accept form fields (application/x-www-form-urlencoded or multipart/form-data)
@app.post("/jobs", tags=["Run"])
async def create_jobs(
    background: BackgroundTasks,
    req: JobRequest = Depends(JobRequest.as_form),
    user=Depends(require_user),
):
    status = create_job(
        requested_by=user["email"],
        input_folder_url=str(req.input_folder_url),
        output_folder_url=str(req.output_folder_url),
    )
    # fire-and-forget worker
    background.add_task(process_job, status["job_id"])
    return status


@app.get("/jobs/{job_id}", tags=["Run"])
async def job_status(job_id: str, user=Depends(require_user)):
    try:
        return get_status(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
