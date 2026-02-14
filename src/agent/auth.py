"""Authentication module for the AgentCore Runtime entrypoint.

Validates JWT tokens carried in request payloads and extracts user
identity information.  Configuration is driven by environment variables:

- ``AUTH_ENDPOINT``   – authentication service URL (reserved for future
  remote verification; not used in local JWT validation).
- ``AUTH_SECRET_KEY`` – HMAC secret used for local JWT verification.

Requirements: 6.1, 6.2, 6.3, 6.4
"""

from __future__ import annotations

import os

import jwt


class AuthenticationError(Exception):
    """Raised when a token is missing, malformed, or expired."""


def validate_token(token: str) -> dict:
    """Validate a JWT token and return the decoded user information.

    Parameters
    ----------
    token:
        A JWT string signed with the configured ``AUTH_SECRET_KEY``.

    Returns
    -------
    dict
        ``{"user_id": str, "username": str, "roles": list[str]}``

    Raises
    ------
    AuthenticationError
        If the token is empty, malformed, expired, or the secret key
        is not configured.
    """
    if not token or not token.strip():
        raise AuthenticationError("Missing authentication token")

    secret_key = os.environ.get("AUTH_SECRET_KEY")
    if not secret_key:
        raise AuthenticationError("AUTH_SECRET_KEY not configured")

    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid or expired authentication token")

    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id:
        raise AuthenticationError("Token missing user identity")

    return {
        "user_id": str(user_id),
        "username": payload.get("username", ""),
        "roles": payload.get("roles", []),
    }
