import asyncio
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.packaging import build_zip
from app.routing import HANDLERS

CHUNK = 1 << 20  # 1 MiB
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
PER_FILE_TIMEOUT_S = 90


def _safe_stem(name: str) -> str:
    stem = Path(name).stem or "file"
    cleaned = _SAFE.sub("_", stem)[:120]
    return cleaned or "file"


def _unique(stem: str, used: set[str]) -> str:
    if stem not in used:
        used.add(stem)
        return stem
    n = 2
    while f"{stem}-{n}" in used:
        n += 1
    final = f"{stem}-{n}"
    used.add(final)
    return final


async def process_batch(uploads: list[UploadFile]) -> tuple[Path, Path]:
    if not uploads:
        raise HTTPException(400, "no files provided")
    if len(uploads) > settings.MAX_FILES_PER_JOB:
        raise HTTPException(413, f"too many files (max {settings.MAX_FILES_PER_JOB})")

    tmp = Path(tempfile.mkdtemp(prefix="bulkconv-"))
    out = tmp / "out"
    out.mkdir()
    errors: list[tuple[str, str]] = []

    try:
        staged: list[tuple[str, Path]] = []
        total = 0
        for u in uploads:
            original = u.filename or f"file_{len(staged):03d}"
            ext = Path(original).suffix.lower()
            dest = tmp / f"in_{len(staged):03d}_{_safe_stem(original)}{ext}"
            with dest.open("wb") as f:
                while chunk := await u.read(CHUNK):
                    total += len(chunk)
                    if total > settings.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "batch exceeds MAX_UPLOAD_BYTES")
                    f.write(chunk)
            staged.append((original, dest))

        used: set[str] = set()
        for original, path in staged:
            ext = path.suffix.lower()
            handler = HANDLERS.get(ext)
            if handler is None:
                errors.append((original, f"unsupported file type: {ext or '(none)'}"))
                continue
            try:
                md = await asyncio.wait_for(asyncio.to_thread(handler, path), timeout=PER_FILE_TIMEOUT_S)
                if not md or not md.strip():
                    raise ValueError("converter produced empty output")
                final = _unique(_safe_stem(original), used)
                (out / f"{final}.md").write_text(md, encoding="utf-8")
            except asyncio.TimeoutError:
                errors.append((original, f"TimeoutError: exceeded {PER_FILE_TIMEOUT_S}s"))
            except Exception as e:
                errors.append((original, f"{type(e).__name__}: {e}"))

        zip_path = build_zip(out, errors, tmp)
        return zip_path, tmp

    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
