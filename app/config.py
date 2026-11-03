import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from cryptography.fernet import Fernet
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def split_origins(value: str | None) -> list[str]:
    if not value:
        return []
    return list(dict.fromkeys(origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()))


class Config:
    APPLICATION_YEAR = int(os.getenv("APPLICATION_YEAR", str(date.today().year)))
    ENV_NAME = os.getenv("FLASK_ENV", "production").strip().lower()
    TESTING = False
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_TYPE = "Bearer"
    SESSION_COOKIE_NAME = "hogares_admin"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = as_bool(os.getenv("SESSION_COOKIE_SECURE"), ENV_NAME == "production")
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY", "")
    FILE_ENCRYPTION_KEY = os.getenv("FILE_ENCRYPTION_KEY", "")
    _database_password = quote_plus(os.getenv("DB_PASSWORD", ""))
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "") or (
        f"mariadb+pymysql://{quote_plus(os.getenv('DB_USER', 'hogares'))}:{_database_password}"
        f"@{os.getenv('DB_HOST', 'database')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME', 'hogares_db')}"
        "?charset=utf8mb4"
        if _database_password
        else ""
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    RATELIMIT_STORAGE_URI = os.getenv("REDIS_URL") or "memory://"
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_SWALLOW_ERRORS = True
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True
    CORS_ALLOWED_ORIGINS = split_origins(os.getenv("CORS_ORIGINS"))
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024
    MAX_IMAGE_SIZE = 5 * 1024 * 1024
    MAX_PDF_SIZE = 10 * 1024 * 1024
    MAX_IMAGE_PIXELS = 40_000_000
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
    MINIO_SECURE = as_bool(os.getenv("MINIO_SECURE"), True)
    MINIO_BUCKET_NAME = os.getenv("MINIO_BUCKET_NAME", "hogares-estudiantiles-documentos")
    MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", "")
    MINIO_REGION = os.getenv("MINIO_REGION") or None
    DRAFT_TTL_DAYS = int(os.getenv("DRAFT_TTL_DAYS", "30"))
    APP_NAME = os.getenv("APP_NAME", "Hogares Estudiantiles")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = as_bool(os.getenv("MAIL_USE_TLS"), True)
    MAIL_USE_SSL = as_bool(os.getenv("MAIL_USE_SSL"), False)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "")
    MAIL_SUPPRESS_SEND = False
    TRUST_PROXY = as_bool(os.getenv("TRUST_PROXY"), True)
    FORCE_HTTPS = as_bool(os.getenv("FORCE_HTTPS"), True)

    @classmethod
    def validate(cls) -> None:
        if cls.TESTING:
            return
        if not 2000 <= cls.APPLICATION_YEAR <= 2100:
            raise RuntimeError("APPLICATION_YEAR debe estar entre 2000 y 2100.")
        if cls.MAIL_USE_TLS and cls.MAIL_USE_SSL:
            raise RuntimeError("Elegí MAIL_USE_TLS o MAIL_USE_SSL; no pueden estar ambos activos.")
        missing = [
            name
            for name in (
                "SECRET_KEY",
                "JWT_SECRET_KEY",
                "DATA_ENCRYPTION_KEY",
                "FILE_ENCRYPTION_KEY",
                "SQLALCHEMY_DATABASE_URI",
                "RATELIMIT_STORAGE_URI",
                "MINIO_ENDPOINT",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
                "MAIL_SERVER",
                "MAIL_DEFAULT_SENDER",
            )
            if not getattr(cls, name)
        ]
        if missing:
            raise RuntimeError(f"Faltan variables de entorno obligatorias: {', '.join(missing)}")
        if len(cls.SECRET_KEY) < 32:
            raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres.")
        try:
            Fernet(cls.DATA_ENCRYPTION_KEY.encode("ascii"))
            Fernet(cls.FILE_ENCRYPTION_KEY.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Las claves de cifrado no son claves Fernet válidas.") from exc
        if cls.ENV_NAME == "production" and cls.RATELIMIT_STORAGE_URI.startswith("memory://"):
            raise RuntimeError("REDIS_URL es obligatorio en producción para rate limiting compartido.")
