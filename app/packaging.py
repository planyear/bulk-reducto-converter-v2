import io
import zipfile
from datetime import datetime
from typing import Iterable

from .jobs import FileResult


def make_archive_name() -> str:
    return f"converted_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"


def build_zip(results: Iterable[FileResult]) -> bytes:
    results = list(results)
    buf = io.BytesIO()
    errors_lines: list[str] = []
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            if r.status == "ok" and r.content is not None:
                name = _unique(r.output_name, seen_names)
                zf.writestr(name, r.content)
            else:
                errors_lines.append(
                    f"{r.source_name}\t{r.message or 'unknown error'}"
                )

        if errors_lines:
            zf.writestr("errors.txt", ("\n".join(errors_lines) + "\n").encode("utf-8"))

    return buf.getvalue()


def _unique(name: str, seen: dict[str, int]) -> str:
    if name not in seen:
        seen[name] = 1
        return name
    seen[name] += 1
    stem, _, ext = name.rpartition(".")
    if stem == "":
        return f"{name}_{seen[name]}"
    return f"{stem}_{seen[name]}.{ext}"
