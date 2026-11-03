import json

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class EncryptionService:
    @staticmethod
    def encrypt(payload: dict) -> bytes:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value),
        ).encode("utf-8")
        return Fernet(current_app.config["DATA_ENCRYPTION_KEY"].encode("ascii")).encrypt(serialized)

    @staticmethod
    def decrypt(payload: bytes | None) -> dict | None:
        if not payload:
            return None
        try:
            decrypted = Fernet(current_app.config["DATA_ENCRYPTION_KEY"].encode("ascii")).decrypt(payload)
            value = json.loads(decrypted)
            return value if isinstance(value, dict) else None
        except (InvalidToken, ValueError, json.JSONDecodeError):
            current_app.logger.exception("No se pudo descifrar una solicitud.")
            return None

    @staticmethod
    def encrypt_file(content: bytes) -> bytes:
        return Fernet(current_app.config["FILE_ENCRYPTION_KEY"].encode("ascii")).encrypt(content)

    @staticmethod
    def decrypt_file(content: bytes) -> bytes:
        return Fernet(current_app.config["FILE_ENCRYPTION_KEY"].encode("ascii")).decrypt(content)
