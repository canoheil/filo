from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str = ""
    DATABASE_URL: str = "mysql+asyncmy://secretary:secretary@localhost:3306/secretary?charset=utf8mb4"
    GEMINI_API_KEY: str = ""
    GEMMA_MODEL: str = "gemma-4-26b-it"
    AI_ENDPOINT: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    FREE_DAILY_LIMIT: int = 30
    DRAFT_TTL_MINUTES: int = Field(default=60, ge=1, le=1440)
    DRAFT_ERROR_RETENTION_HOURS: int = Field(default=24, ge=1, le=720)
    LOG_LEVEL: str = "INFO"
    ADMIN_IDS: str = ""

    @model_validator(mode="after")
    def retention_after_expiry(self):
        if self.DRAFT_ERROR_RETENTION_HOURS * 60 <= self.DRAFT_TTL_MINUTES:
            raise ValueError("Draft error retention must exceed the send validity period")
        return self

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
