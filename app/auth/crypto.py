import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    source = settings.token_encryption_key or settings.jwt_secret
    if not source:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY or JWT_SECRET must be set")
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_token(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
