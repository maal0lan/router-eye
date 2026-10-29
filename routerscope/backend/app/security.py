from cryptography.fernet import Fernet, InvalidToken
from .config import settings


def _fernet() -> Fernet:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is missing. Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
    try:
        return Fernet(settings.encryption_key.encode())
    except Exception as exc:
        raise RuntimeError("ENCRYPTION_KEY is invalid; it must be a Fernet key.") from exc

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt account key. Was ENCRYPTION_KEY changed?") from exc
