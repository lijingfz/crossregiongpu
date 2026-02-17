"""FastAPI dependencies for JWT authentication.

Uses HTTPBearer scheme and delegates token validation to
``src.agent.auth.validate_token``.

Requirements: 1.1, 1.4, 6.5
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.agent.auth import AuthenticationError, validate_token

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Extract and validate user info from a Bearer token.

    Returns
    -------
    dict
        ``{"user_id": str, "username": str, "roles": list[str]}``

    Raises
    ------
    HTTPException 401
        If the token is missing, malformed, or expired.
    """
    try:
        return validate_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
