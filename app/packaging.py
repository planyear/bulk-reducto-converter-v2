import zipfile
from pathlib import Path


def build_zip(out_dir: Path, errors: list[tuple[str, str]], tmp: Path) -> Path:
    zip_path = tmp / "result.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(out_dir.iterdir()):
            if p.is_file():
                zf.write(p, arcname=p.name)
        if errors:
            body = "\n".join(f"{name}\t{reason}" for name, reason in errors) + "\n"
            zf.writestr("errors.txt", body)
    return zip_path
