"""Application settings from environment variables / backend/.env. Paths resolve against backend/."""
import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
FRONTEND_DIR = REPO_DIR / "frontend"
SECRETS_DIR = BACKEND_DIR / ".secrets"
DEMO_ADMIN_PASSWORD = "ChangeMe!2026"

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LendWise"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"
    log_file: str | None = None

    database_url: str = "sqlite:///./lendwise.db"
    data_dir: str = "data"                      # uploaded statements + generated letters (outside the web root)
    model_dir: str = "app/ml/artifacts"         # trained risk model + metrics + model card

    # Auth
    jwt_secret: str | None = Field(None, min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "lendwise"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool | None = None
    bootstrap_admin_email: str = "admin@lendwise.dev"
    bootstrap_admin_password: str = DEMO_ADMIN_PASSWORD
    allow_registration: bool = True              # applicants sign up; staff accounts are created by an admin
    bcrypt_rounds: int = Field(12, ge=4, le=15)
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    trusted_proxies: list[str] = []

    # LLM (Groq). Decisions are made by code; the LLM orchestrates tools and writes memos and letters.
    llm_mode: Literal["groq", "fake"] = "groq"
    groq_api_key: str | None = None
    model_router: str = "qwen/qwen3.8-27b"      # case Q&A, structured extraction
    model_fast: str = "openai/gpt-oss-20b"      # tool-calling underwriting agent
    model_large: str = "openai/gpt-oss-120b"    # credit memo + customer letter
    model_tokens_per_minute: int = 8000
    llm_timeout_seconds: float = 60
    llm_max_attempts: int = Field(3, ge=1, le=5)
    llm_max_wait_seconds: float = 65
    llm_breaker_failures: int = 4
    llm_breaker_reset_seconds: int = 60
    llm_cache_ttl_seconds: int = 3600
    run_max_llm_calls: int = 14

    # Lending
    four_eyes_amount: int = 1_000_000           # loans above this need a second approver (admin)
    max_statement_mb: float = 3
    applications_per_user_per_day: int = 5
    max_concurrent_assessments: int = 2

    enable_docs: bool | None = None

    @property
    def docs_enabled(self) -> bool:
        return self.enable_docs if self.enable_docs is not None else self.environment != "production"

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.environment == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        if isinstance(data, dict):
            for key in list(data):
                if key.lower() in ("jwt_secret", "groq_api_key", "log_file", "enable_docs", "cookie_secure") \
                        and data[key] == "":
                    data[key] = None
        return data

    @model_validator(mode="after")
    def _finalise(self):
        if self.environment == "production":
            if not self.jwt_secret or self.jwt_secret.startswith("change-me"):
                raise ValueError("Set a strong JWT_SECRET (32+ chars) in production")
            if self.bootstrap_admin_password == DEMO_ADMIN_PASSWORD:
                raise ValueError("Set BOOTSTRAP_ADMIN_PASSWORD in production")
            if self.llm_mode == "fake":
                raise ValueError("LLM_MODE=fake is for tests and demos, not production")
        if not self.jwt_secret or self.jwt_secret.startswith("change-me"):
            self.jwt_secret = _local_jwt_secret(self.environment)
        self.database_url = _anchor_sqlite(self.database_url)
        self.data_dir = _anchor(self.data_dir)
        self.model_dir = _anchor(self.model_dir)
        return self


def _anchor(path: str) -> str:
    if path == ":memory:" or Path(path).is_absolute():
        return path
    return str((BACKEND_DIR / path).resolve())


def _anchor_sqlite(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url == "sqlite:///:memory:":
        return url
    return prefix + Path(_anchor(url[len(prefix):])).as_posix()


def _local_jwt_secret(environment: str) -> str:
    """Never sign tokens with a secret published on GitHub: tests get a throwaway one, local dev a random
    one persisted in backend/.secrets/ (git-ignored)."""
    if environment == "test":
        return secrets.token_urlsafe(48)
    path = SECRETS_DIR / "jwt_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    SECRETS_DIR.mkdir(exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    log.warning("Generated a local JWT secret in %s (set JWT_SECRET to override)", path)
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
