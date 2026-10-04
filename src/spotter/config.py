"""Settings from environment variables (SPEC section 13).

Reads `.env` for local development. Production commands pass the Neon values
explicitly: `uv run --env-file .env.local spotter <command>`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def sqlalchemy_url(url: str) -> str:
    """Point a plain Postgres URL at the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://spotter:spotter@localhost:5432/spotter"
    database_url_unpooled: str | None = None
    garmin_token_key: SecretStr | None = None

    # MCP server only (SPEC section 13). Optional so the CLI runs without them.
    base_url: str | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    allowed_github_user_id: str | None = None
    jwt_signing_key: SecretStr | None = None
    storage_encryption_key: SecretStr | None = None

    @property
    def db_url(self) -> str:
        return sqlalchemy_url(self.database_url)

    @property
    def migration_url(self) -> str:
        """Direct connection for migrations. Neon's pooler does not suit DDL sessions."""
        return sqlalchemy_url(self.database_url_unpooled or self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
