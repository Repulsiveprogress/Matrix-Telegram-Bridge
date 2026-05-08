from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MATRIX_NIO_DEVICE_ID = "MATRIX_TG_BRIDGE"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str
    telegram_bot_username: str = "bot"

    matrix_hs_url: str
    matrix_user_id: str
    matrix_access_token: str

    database_path: str = "/data/bridge.db"
    link_code_ttl_seconds: int = 3600

    matrix_allowed_server: str | None = None

    # BCP-47 locale tag. Use "ru" for Russian, default is English.
    locale: str = "en"

    @field_validator("matrix_allowed_server", mode="before")
    @classmethod
    def _strip_allowed_server(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return None

    rate_limit_link_attempts: int = 20
    rate_limit_link_window_seconds: int = 300
    rate_limit_code_generations: int = 10
    rate_limit_code_window_seconds: int = 3600

    def matrix_homeserver_base(self) -> str:
        return self.matrix_hs_url.rstrip("/")
