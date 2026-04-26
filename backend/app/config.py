from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OPENAI_API_KEY: str = ""
    EMBED_MODEL: str = "text-embedding-3-large"
    CHAT_MODEL: str = "gpt-4.1-mini"

    PARENT_TOKENS: int = 1000
    CHILD_TOKENS: int = 200

    DEFAULT_TOP_K: int = 5
    PARENT_CONSOLIDATION_THRESHOLD: int = 3

    UPLOAD_DIR: str = "../data/uploads"
    CACHE_DIR: str = "../data/cache"
    CHROMA_DIR: str = "../data/chroma"
    SQLITE_URL: str = "sqlite:///../data/app.db"

    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_path(self) -> Path:
        p = Path(self.CACHE_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def chroma_path(self) -> Path:
        p = Path(self.CHROMA_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
