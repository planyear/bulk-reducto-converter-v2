from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OCR: str = "docling"
    REDUCTO_API_KEY: str = ""
    REDUCTO_API_URL: str = "https://platform.reducto.ai"
    MAX_UPLOAD_BYTES: int = 209_715_200
    MAX_FILES_PER_JOB: int = 50
    PER_FILE_TIMEOUT_S: int = 300
    PORT: int = 8000

    WORKOS_API_KEY: str = ""
    WORKOS_CLIENT_ID: str = ""
    WORKOS_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    WORKOS_COOKIE_PASSWORD: str = ""
    WORKOS_DEFAULT_ORG_ID: str = ""
    APP_BASE_URL: str = "http://localhost:8000"


settings = Settings()
