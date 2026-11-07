"""Encryption at rest for credentials we must be able to read back (mailbox passwords)."""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.errors import AppError


class SecretsNotConfiguredError(AppError):
    status_code = 503
    code = "secrets_not_configured"


class SecretBox:
    def __init__(self, settings: Settings) -> None:
        key = settings.secrets_encryption_key
        if key is None:
            raise SecretsNotConfiguredError(
                "SECRETS_ENCRYPTION_KEY must be set to store credentials "
                "(mailbox passwords, MCP client secrets)"
            )
        self._fernet = Fernet(key.get_secret_value().encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            # Wrong or rotated key: surface clearly instead of a cryptic failure mid-poll.
            raise SecretsNotConfiguredError(
                "Stored secret can't be decrypted with this key"
            ) from exc
