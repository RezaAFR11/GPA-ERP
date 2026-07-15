import warnings
from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET_KEY = "dev-secret-key-replace-in-production"
# Also catch the .env placeholder value shipped with the repo
_PLACEHOLDER_SECRET_KEYS = {
    _DEFAULT_SECRET_KEY,
    "change-me-to-a-very-long-random-string-in-production",
}
_DEFAULT_DB_PASSWORD_FRAGMENT = "gpa_pass"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql://gpa_user:gpa_pass@localhost:5432/gpa_erp"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30

    # JWT
    SECRET_KEY: str = _DEFAULT_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    SESSION_COOKIE_SAMESITE: str = "lax"

    # App
    APP_NAME: str = "GPA-ERP"
    APP_VERSION: str = "5.0.0"
    DEBUG: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:3001,http://localhost:5173"
    FRONTEND_URL: str = "http://localhost:3000"

    # Seed
    SEED_SUPER_ADMIN_EMAIL: str = "admin@gpa.local"
    SEED_SUPER_ADMIN_PASSWORD: str = "ChangeMe123!"
    SEED_SUPER_ADMIN_NAME: str = "System Administrator"

    # Uploads
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_MB: int = 10

    # Statutory payroll parameters. BPJS updates the JP ceiling periodically.
    BPJS_JP_SALARY_CEILING: Decimal = Decimal("10547400")
    BPJS_KES_SALARY_CEILING: Decimal = Decimal("12000000")
    BPJS_JKK_RATE: Decimal = Decimal("0.0089")

    # SMTP email (optional — leave blank to disable email notifications)
    SMTP_HOST:     str = ""
    SMTP_PORT:     int = 587
    SMTP_USER:     str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM:     str = "GPA ERP <noreply@gpa.local>"
    SMTP_USE_TLS:  bool = True
    SMTP_TIMEOUT_SECONDS: int = 15
    EMAIL_OUTBOX_POLL_SECONDS: int = 5

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        # Validation against DEBUG is done in model_validator below; just return here.
        return v

    @model_validator(mode="after")
    def _security_checks(self) -> "Settings":
        # In production (DEBUG=False), reject any known placeholder SECRET_KEY.
        if not self.DEBUG and self.SECRET_KEY in _PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is still set to the insecure development default. "
                "Set a strong random SECRET_KEY before running in production (DEBUG=False)."
            )
        if not self.DEBUG and len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters in production")

        self.SESSION_COOKIE_SAMESITE = self.SESSION_COOKIE_SAMESITE.lower().strip()
        if self.SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
            raise ValueError("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
        if not self.DEBUG:
            weak_seed_passwords = {"ChangeMe123!", "REPLACE_WITH_STRONG_PASSWORD"}
            if (
                self.SEED_SUPER_ADMIN_PASSWORD in weak_seed_passwords
                or len(self.SEED_SUPER_ADMIN_PASSWORD) < 12
            ):
                raise ValueError(
                    "SEED_SUPER_ADMIN_PASSWORD must be a strong password of at least 12 characters"
                )

        if self.DB_POOL_SIZE < 1 or self.DB_MAX_OVERFLOW < 0 or self.DB_POOL_TIMEOUT < 1:
            raise ValueError("Database pool settings must be positive")

        # Always warn if the DATABASE_URL still uses the default dev password.
        if _DEFAULT_DB_PASSWORD_FRAGMENT in self.DATABASE_URL:
            warnings.warn(
                f"WARNING: DATABASE_URL still contains the default dev password "
                f"('{_DEFAULT_DB_PASSWORD_FRAGMENT}'). "
                "Update DATABASE_URL with a strong password before deploying to production.",
                stacklevel=2,
            )

        if self.BPJS_JP_SALARY_CEILING <= 0 or self.BPJS_KES_SALARY_CEILING <= 0:
            raise ValueError("BPJS salary ceilings must be greater than zero")
        if not Decimal("0.0024") <= self.BPJS_JKK_RATE <= Decimal("0.0174"):
            raise ValueError("BPJS_JKK_RATE must be between 0.0024 and 0.0174")
        if self.SMTP_TIMEOUT_SECONDS < 1 or self.EMAIL_OUTBOX_POLL_SECONDS < 1:
            raise ValueError("SMTP timeout and email outbox polling interval must be at least 1 second")

        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
