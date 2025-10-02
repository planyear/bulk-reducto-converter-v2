from typing import Iterable
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
from .config import settings

SCOPES = ["https://www.googleapis.com/auth/drive"]

def _scoped_credentials():
    creds = service_account.Credentials.from_service_account_file(
        settings.service_account_json, scopes=SCOPES
    )
    if settings.delegate_user:
        creds = creds.with_subject(settings.delegate_user)
    return creds

def drive_svc():
    return build("drive", "v3", credentials=_scoped_credentials(), cache_discovery=False)

def list_folder_files(folder_id: str, mime_filter: Iterable[str] | None = None) -> list[dict]:
    svc = drive_svc()
    page_token = None
    files: list[dict] = []

    q = f"'{folder_id}' in parents and trashed = false"
    if mime_filter:
        mime_q = " or ".join([f"mimeType='{m}'" for m in mime_filter])
        q += f" and ({mime_q})"

    while True:
        resp = svc.files().list(
            q=q,
            fields="nextPageToken, files(id, name, mimeType, md5Checksum)",
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files

def download_file_bytes(file_id: str) -> bytes:
    svc = drive_svc()
    # supportsAllDrives not needed for get_media
    req = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

def upload_text_file(folder_id: str, name: str, content: str) -> str:
    svc = drive_svc()
    body = {"name": name, "parents": [folder_id], "mimeType": "text/plain"}
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    file = svc.files().create(
        body=body, media_body=media, fields="id", supportsAllDrives=True
    ).execute()
    return file["id"]

def upload_pdf_file(folder_id: str, name: str, pdf_bytes: bytes) -> str:
    svc = drive_svc()
    body = {"name": name, "parents": [folder_id], "mimeType": "application/pdf"}
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
    file = svc.files().create(
        body=body, media_body=media, fields="id", supportsAllDrives=True
    ).execute()
    return file["id"]
