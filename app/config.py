from pydantic import BaseModel
import os

from dotenv import load_dotenv
load_dotenv()

class Settings(BaseModel):
    # App / auth
    session_secret: str = os.getenv("SESSION_SECRET")
    allowed_domain: str = os.getenv("ALLOWED_DOMAIN")


    # Google OAuth for gating Swagger/UI
    google_client_id: str | None = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret: str | None = os.getenv("GOOGLE_CLIENT_SECRET")


    # Google Drive service account
    service_account_json: str = os.getenv("SERVICE_ACCOUNT_JSON")
    delegate_user: str | None = os.getenv("DELEGATE_USER")


    # CloudConvert
    cloudconvert_api_key: str | None = os.getenv("CLOUDCONVERT_API_KEY")


    # Reducto
    reducto_api_url: str = os.getenv("REDUCTO_API_URL")
    reducto_api_key: str | None = os.getenv("REDUCTO_API_KEY")


    # Processing controls
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "5"))


settings = Settings()