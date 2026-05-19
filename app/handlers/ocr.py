from pathlib import Path

from app.parsers import parse_pdf_like


def convert(path: Path) -> str:
    return parse_pdf_like(path)
