from datetime import datetime

import redis
from flask import current_app
from limits.storage import MemoryStorage


def init_token_store(app) -> None:
    if app.config["RATELIMIT_STORAGE_URI"] == "memory://":
        if app.config.get("ENV_NAME") == "production" and not app.testing:
            raise RuntimeError("Redis es obligatorio en producción para revocar sesiones entre procesos.")
        app.extensions["auth_memory_blocklist"] = MemoryStorage()


def _client():
    return redis.Redis.from_url(
        current_app.config["RATELIMIT_STORAGE_URI"],
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def revoke_token(jti: str, expires_at: int) -> None:
    ttl = max(1, expires_at - int(datetime.now().timestamp()))
    if current_app.config["RATELIMIT_STORAGE_URI"] == "memory://":
        current_app.extensions["auth_memory_blocklist"].incr(f"auth:revoked:{jti}", ttl)
        return
    _client().setex(f"auth:revoked:{jti}", ttl, "1")


def token_is_revoked(jti: str) -> bool:
    if not jti:
        return True
    if current_app.config["RATELIMIT_STORAGE_URI"] == "memory://":
        return bool(current_app.extensions["auth_memory_blocklist"].get(f"auth:revoked:{jti}"))
    try:
        return bool(_client().exists(f"auth:revoked:{jti}"))
    except (redis.RedisError, ValueError):
        current_app.logger.exception("No se pudo consultar la revocación JWT.")
        return True
