import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, List
from uuid import uuid4

from .drive import list_folder_files, download_file_bytes, upload_text_file
from .processors import to_pdf_bytes, reducto_clean_pdf, PDF_MIME
from .util import extract_drive_folder_id
from .config import settings

logger = logging.getLogger("bulk-reducto.jobs")

# -----------------------------
# Output filename helper
# -----------------------------
def make_output_name(src_name: str, suffix: str = "Reducto", ext: str = ".txt") -> str:
    """
    Turn 'foo.pdf' -> 'foo_Reducto_YYYY-MM-DD.txt'
    Also strips/normalizes characters that Google Drive might not love.
    """
    stem = re.sub(r"\.[^.]+$", "", src_name)                             # drop extension
    stem = re.sub(r"[\\/<>:*?\"|]+", "_", stem).strip()                   # safe-ish stem
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{stem}_{suffix}_{today}{ext}"


# In-memory job store
JOBS: Dict[str, Dict[str, Any]] = {}

# -----------------------------
# Try to obtain a Drive service
# -----------------------------
def _get_drive_service_or_none():
    """
    Best-effort: return a Google Drive service client if available.
    Works with either a `drive_svc()` in .drive OR a `get_service()` in .gdrive.
    Returns None if not available (conversion still works for non-Google-native files).
    """
    # 1) Projects that expose drive_svc() in app/drive.py
    try:
        from .drive import drive_svc  # type: ignore
        return drive_svc()
    except Exception:
        pass

    # 2) Projects that expose get_service(...) in app/gdrive.py
    try:
        from .gdrive import get_service  # type: ignore
        return get_service(settings.service_account_json)
    except Exception:
        pass

    logger.info("No Drive service helper found; Google-native exports will be skipped.")
    return None


# -----------------------------
# Single-file worker
# -----------------------------
async def _process_one(file: dict, out_folder_id: str) -> dict:
    name = file.get("name", file["id"])
    mime = file.get("mimeType")
    fid = file["id"]

    logger.info("START file id=%s name=%s mime=%s", fid, name, mime)

    try:
        # 1) Download raw bytes
        src = download_file_bytes(fid)
        logger.info("DOWNLOADED bytes id=%s size=%s", fid, len(src))

        # 2) Convert *anything* to PDF bytes (passthrough for PDFs)
        svc = _get_drive_service_or_none()
        pdf_bytes = await to_pdf_bytes(
            name=name,
            src_bytes=src,
            mime=mime,
            drive_service=svc,        # used for Google Docs/Sheets/Slides exports
            drive_file_id=fid,
        )

        if mime == PDF_MIME:
            logger.info("PDF passthrough id=%s size=%s", fid, len(pdf_bytes))
        else:
            logger.info("CONVERTED -> PDF id=%s pdf_size=%s", fid, len(pdf_bytes))

        # 3) Reducto: upload + parse -> cleaned text
        logger.info("REDUCTO upload+parse id=%s", fid)
        cleaned = await reducto_clean_pdf(pdf_bytes)
        logger.info("REDUCTO OK id=%s text_len=%s", fid, len(cleaned))

        # 4) Upload .txt result to destination folder
        out_name = make_output_name(name)
        out_id = upload_text_file(out_folder_id, out_name, cleaned)
        logger.info("UPLOAD OK id=%s -> out_file_id=%s out_name=%s", fid, out_id, out_name)

        return {
            "source_id": fid,
            "source_name": name,
            "status": "ok",
            "output_file_id": out_id,
            "output_name": out_name,
        }

    except Exception as e:
        logger.exception("ERROR processing id=%s name=%s", fid, name)
        return {
            "source_id": fid,
            "source_name": name,
            "status": "error",
            "message": str(e),
        }

def create_job(*, requested_by: str, input_folder_url: str, output_folder_url: str) -> dict:
    in_id = extract_drive_folder_id(input_folder_url)
    out_id = extract_drive_folder_id(output_folder_url)

    files = list_folder_files(in_id)
    job_id = uuid4().hex

    JOBS[job_id] = {
        "job_id": job_id,
        "requested_by": requested_by,
        "input_folder_id": in_id,
        "output_folder_id": out_id,
        "total": len(files),
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "items": [],
        "files": files,
    }

    return {k: JOBS[job_id][k] for k in ("job_id", "requested_by", "total", "done", "failed", "skipped", "items")}


async def process_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return

    files: List[dict] = job.get("files", [])
    out_id = job["output_folder_id"]
    logger.info("JOB START id=%s total=%s", job_id, len(files))

    sem = asyncio.Semaphore(int(settings.max_concurrency or 1))

    async def bounded(file):
        async with sem:
            res = await _process_one(file, out_id)
            job["items"].append(res)
            status = res["status"]
            if status == "ok":
                job["done"] += 1
            elif status == "skipped":
                job["skipped"] += 1
            else:
                job["failed"] += 1

    await asyncio.gather(*[bounded(f) for f in files])

    logger.info(
        "JOB DONE id=%s done=%s failed=%s skipped=%s",
        job_id, job["done"], job["failed"], job["skipped"]
    )

def get_status(job_id: str) -> dict:
    job = JOBS[job_id]
    return {k: job[k] for k in ("job_id", "requested_by", "total", "done", "failed", "skipped", "items")}
