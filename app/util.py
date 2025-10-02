import re
from urllib.parse import urlparse


# Simple folder-id parser for common Drive URL patterns
_DRIVE_FOLDER_RE = re.compile(r"/folders/([a-zA-Z0-9_-]+)")


_DEF_QUERY_ID = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")


def extract_drive_folder_id(url: str) -> str:
    """Extracts a folderId from typical Google Drive folder links.
    Supports forms like:
    https://drive.google.com/drive/folders/<ID>
    https://drive.google.com/drive/u/0/folders/<ID>
    https://drive.google.com/drive/folders/<ID>?usp=share_link
    https://drive.google.com/open?id=<ID>
    Raises ValueError if not found.
    """
    m = _DRIVE_FOLDER_RE.search(url)
    if m:
        return m.group(1)
    m = _DEF_QUERY_ID.search(url)
    if m:
        return m.group(1)
    raise ValueError(f"Could not parse Drive folder id from URL: {url}")


def safe_basename(filename: str) -> str:
    # Avoid path separators + trim
    return re.sub(r"[\\/]+", "_", filename).strip()