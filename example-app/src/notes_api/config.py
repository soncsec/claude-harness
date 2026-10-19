"""Settings, read from environment variables (see env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from notes_api import security


@dataclass(frozen=True)
class Settings:
    database_path: str = "notes.db"
    token_ttl_seconds: int = 3600
    scrypt_n: int = security.SCRYPT_N

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_path=os.environ.get("NOTES_DATABASE_PATH", cls.database_path),
            token_ttl_seconds=int(
                os.environ.get("NOTES_TOKEN_TTL_SECONDS", str(cls.token_ttl_seconds))
            ),
        )
