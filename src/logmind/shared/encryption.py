"""
AES Encryption Utilities

Encrypts/decrypts sensitive data (API keys) using Fernet symmetric encryption.
"""

from cryptography.fernet import Fernet, InvalidToken

from logmind.core.config import get_settings


def _get_fernet() -> Fernet:
    """Get Fernet cipher with the configured encryption key."""
    settings = get_settings()
    key = settings.encryption_key
    # If key is not a valid Fernet key, derive one
    if len(key) != 44:
        import base64
        import hashlib
        derived = hashlib.sha256(key.encode()).digest()
        key = base64.urlsafe_b64encode(derived).decode()
    return Fernet(key.encode())


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext."""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt value — invalid key or corrupted data")
