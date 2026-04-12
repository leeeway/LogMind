"""
FastAPI Dependency Injection

Provides reusable dependencies for authentication, tenant resolution,
database sessions, and service instances.
"""

from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.config import Settings, get_settings
from logmind.core.database import get_db_session
from logmind.core.security import TokenPayload, decode_access_token

# ── Type Aliases ─────────────────────────────────────────
SettingsDep = Annotated[Settings, Depends(get_settings)]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]


# ── Auth Dependencies ────────────────────────────────────
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    auth: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TokenPayload:
    """
    Extract and validate JWT token from Authorization header.
    Returns structured token payload with user_id, tenant_id, role.
    """
    token = auth.credentials

    try:
        payload = decode_access_token(token)
        return TokenPayload.from_dict(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]


async def require_admin(current_user: CurrentUser) -> TokenPayload:
    """Require admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


AdminUser = Annotated[TokenPayload, Depends(require_admin)]


optional_security = HTTPBearer(auto_error=False)

async def get_optional_user(
    auth: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_security)],
) -> TokenPayload | None:
    """Optional auth — returns None if no token provided."""
    if not auth:
        return None
    try:
        return await get_current_user(auth)
    except HTTPException:
        return None


OptionalUser = Annotated[TokenPayload | None, Depends(get_optional_user)]
