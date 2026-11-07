from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str = ""
    DATABASE_URL: str = "mysql+asyncmy://secretary:secretary@localhost:3306/secretary?charset=utf8mb4"
    GEMINI_API_KEY: str = ""
    GEMMA_MODEL: str = "gemma-4-26b-it"
    AI_ENDPOINT: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    FREE_DAILY_LIMIT: int = 30
    LOG_LEVEL: str = "INFO"
    ADMIN_IDS: str = ""

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
