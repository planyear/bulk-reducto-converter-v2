import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    ocr: str = (os.getenv("OCR") or "reducto").strip().lower()

    cloudconvert_api_key: str | None = os.getenv("CLOUDCONVERT_API_KEY")

    reducto_api_url: str | None = os.getenv("REDUCTO_API_URL")
    reducto_api_key: str | None = os.getenv("REDUCTO_API_KEY")

    docling_do_ocr: bool = (os.getenv("DOCLING_DO_OCR") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "5"))

    max_upload_bytes: int = int(
        os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024))
    )


settings = Settings()
