import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .config import settings
from .parsers import parse_pdf
from .processors import PDF_MIME, to_pdf_bytes

logger = logging.getLogger("bulk-reducto.jobs")


@dataclass
class FileResult:
    source_name: str
    output_name: str
    status: str
    content: Optional[bytes]
    message: Optional[str]


def make_output_name(src_name: str, ext: str = ".md") -> str:
    stem = re.sub(r"\.[^.]+$", "", src_name)
    stem = re.sub(r"[\\/<>:*?\"|]+", "_", stem).strip()
    return f"{stem}{ext}"


async def _process_one(name: str, mime: str, src_bytes: bytes) -> FileResult:
    out_name = make_output_name(name)
    logger.info("START name=%s mime=%s size=%s", name, mime, len(src_bytes))

    try:
        pdf_bytes = await to_pdf_bytes(name=name, src_bytes=src_bytes, mime=mime)
        if mime == PDF_MIME:
            logger.info("PDF passthrough name=%s size=%s", name, len(pdf_bytes))
        else:
            logger.info("CONVERTED -> PDF name=%s pdf_size=%s", name, len(pdf_bytes))

        markdown = await parse_pdf(pdf_bytes, name)
        logger.info("PARSED name=%s md_len=%s", name, len(markdown))

        return FileResult(
            source_name=name,
            output_name=out_name,
            status="ok",
            content=markdown.encode("utf-8"),
            message=None,
        )
    except Exception as e:
        logger.exception("ERROR processing name=%s", name)
        return FileResult(
            source_name=name,
            output_name=out_name,
            status="error",
            content=None,
            message=str(e),
        )


async def process_files(
    items: Iterable[tuple[str, str, bytes]],
) -> list[FileResult]:
    items = list(items)
    sem = asyncio.Semaphore(int(settings.max_concurrency or 1))

    async def bounded(name: str, mime: str, data: bytes) -> FileResult:
        async with sem:
            return await _process_one(name, mime, data)

    return list(await asyncio.gather(*[bounded(n, m, d) for (n, m, d) in items]))
