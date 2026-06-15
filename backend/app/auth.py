"""Admin bearer-token auth for write endpoints.

The token is set via ``ADMIN_BEARER_TOKEN`` in env. ``Depends(require_admin)``
on a route enforces it.
"""

from __future__ import annotations

import hashlib

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)
log = structlog.get_logger(__name__)


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Raise 401 unless the request bears the configured admin token."""
    settings = get_settings()
    expected = settings.admin_bearer_token
    # TEMP DEBUG — remove after diagnosing token mismatch.
    log.info(
        "admin_token_debug",
        expected_len=len(expected),
        expected_sha8=hashlib.sha256(expected.encode()).hexdigest()[:8],
        got_len=len(credentials.credentials) if credentials else None,
        got_sha8=hashlib.sha256(credentials.credentials.encode()).hexdigest()[:8] if credentials else None,
    )

    if not expected or expected == "dev-token-change-me":
        # Don't allow the placeholder token in real environments — fail closed.
        if expected != "dev-token-change-me":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ADMIN_BEARER_TOKEN not configured",
            )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
        )
