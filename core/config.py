from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

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
    SUPPORT_CHANNEL: str = ""
    SUPPORT_CHANNEL_JOIN_URL: str = ""

    @property
    def required_channels(self):
        from core.membership import required_channels
        return required_channels(self.SUPPORT_CHANNEL, self.SUPPORT_CHANNEL_JOIN_URL)

    @model_validator(mode="after")
    def validate_membership(self):
        self.required_channels
        parts = [p.strip() for p in self.ADMIN_IDS.split(",")]
        if self.ADMIN_IDS.strip() and any(not p.isascii() or not p.isdigit() or not 0 < int(p) < 2**63 for p in parts):
            raise ValueError("ADMIN_IDS must contain positive numeric IDs separated by commas")
        return self

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
