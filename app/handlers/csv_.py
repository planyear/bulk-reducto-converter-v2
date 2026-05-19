import csv
from pathlib import Path


def convert(path: Path) -> str:
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            cells = [(c or "").replace("|", "\\|") for c in row]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
