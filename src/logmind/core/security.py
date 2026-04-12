"""
JWT Authentication & Security

Provides JWT token creation/validation and password hashing.
"""

from datetime import datetime, timedelta, timezone

import jwt
import bcrypt

from logmind.core.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT token.
    Raises jwt.InvalidTokenError on failure.
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


class TokenPayload:
    """Structured token payload."""

    def __init__(self, sub: str, tenant_id: str, role: str):
        self.sub = sub              # user_id
        self.tenant_id = tenant_id
        self.role = role

    @classmethod
    def from_dict(cls, data: dict) -> "TokenPayload":
        return cls(
            sub=data["sub"],
            tenant_id=data["tenant_id"],
            role=data.get("role", "viewer"),
        )
