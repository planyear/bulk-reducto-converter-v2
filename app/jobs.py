import asyncio
from typing import Dict, Any, List
from .drive import list_folder_files, download_file_bytes, upload_text_file
from .processors import office_to_pdf, reducto_clean_pdf, PDF_MIME
from .util import extract_drive_folder_id
from .config import settings
import logging
from datetime import datetime
import re

def make_output_name(src_name: str, suffix: str = "Reducto", ext: str = ".txt") -> str:
    """
    Turn 'foo.pdf' -> 'foo_Reducto_YYYY-MM-DD.txt'
    Also strips/normalizes characters that Google Drive might not love.
    """
    # drop the original extension
    stem = re.sub(r"\.[^.]+$", "", src_name)
    # make a simple, safe-ish stem
    stem = re.sub(r"[\\/<>:*?\"|]+", "_", stem).strip()  # replace forbidden-ish chars
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{stem}_{suffix}_{today}{ext}"

JOBS: Dict[str, Dict[str, Any]] = {}

MIME_OFFICE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.ms-excel.sheet.macroEnabled.12",  # xlsm
}
MIME_PDF = {"application/pdf"}

logger = logging.getLogger("bulk-reducto.jobs")

async def _process_one(file: dict, out_folder_id: str) -> dict:
    name = file.get("name", file["id"])
    mime = file.get("mimeType")
    fid  = file["id"]
    logger.info("START file id=%s name=%s mime=%s", fid, name, mime)
    try:
        src = download_file_bytes(fid)
        logger.info("DOWNLOADED bytes id=%s size=%s", fid, len(src))

        if mime in MIME_OFFICE:
            logger.info("CONVERT office->pdf id=%s", fid)
            pdf_bytes = await office_to_pdf(name, src, mime)
            logger.info("CONVERTED id=%s pdf_size=%s", fid, len(pdf_bytes))
        elif mime in MIME_PDF:
            pdf_bytes = src
            logger.info("PDF passthrough id=%s size=%s", fid, len(pdf_bytes))
        else:
            msg = f"Unsupported mimeType: {mime}"
            logger.warning("SKIP id=%s %s", fid, msg)
            return {"source_id": fid, "source_name": name, "status": "skipped", "message": msg}

        logger.info("REDUCTO upload+parse id=%s", fid)
        cleaned = await reducto_clean_pdf(pdf_bytes)
        logger.info("REDUCTO OK id=%s text_len=%s", fid, len(cleaned))

        out_name = make_output_name(name)
        out_id = upload_text_file(out_folder_id, out_name, cleaned)
        logger.info("UPLOAD OK id=%s -> out_file_id=%s out_name=%s", fid, out_id, out_name)

        return {"source_id": fid, "source_name": name, "status": "ok",
                "output_file_id": out_id, "output_name": out_name}
    except Exception as e:
        logger.exception("ERROR processing id=%s name=%s", fid, name)
        return {"source_id": fid, "source_name": name, "status": "error", "message": str(e)}

def create_job(*, requested_by: str, input_folder_url: str, output_folder_url: str) -> dict:
    in_id = extract_drive_folder_id(input_folder_url)
    out_id = extract_drive_folder_id(output_folder_url)

    files = list_folder_files(in_id)
    job_id = safe_basename(asyncio.uuid4().hex) if hasattr(asyncio, "uuid4") else __import__("uuid").uuid4().hex
    JOBS[job_id] = {
        "job_id": job_id,
        "requested_by": requested_by,
        "input_folder_id": in_id,
        "output_folder_id": out_id,
        "total": len(files),
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "items": [],        # will be filled by the worker
        "files": files,     # stash for the worker
    }
    return {k: JOBS[job_id][k] for k in ("job_id", "requested_by", "total", "done", "failed", "skipped", "items")}

async def process_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    logger.info("JOB START id=%s total=%s", job_id, len(job.get("files", [])))
    files: List[dict] = job.get("files", [])
    out_id = job["output_folder_id"]

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
    logger.info("JOB DONE id=%s done=%s failed=%s skipped=%s",
                job_id, job["done"], job["failed"], job["skipped"])


def get_status(job_id: str) -> dict:
    job = JOBS[job_id]
    return {k: job[k] for k in ("job_id", "requested_by", "total", "done", "failed", "skipped", "items")}
