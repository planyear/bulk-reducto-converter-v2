from pathlib import Path


def convert(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
