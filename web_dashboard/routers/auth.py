"""Authentication routes for the Web Dashboard.

Provides the login endpoint that validates user credentials and
returns a JWT token using the same ``AUTH_SECRET_KEY`` as the
main agent runtime.

Requirements: 1.2, 6.1
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter

from web_dashboard.models import ApiResponse, LoginData, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Default credentials — override via environment variables in production.
_DEFAULT_USERNAME = "admin"
_DEFAULT_PASSWORD = "admin123"


def _verify_credentials(username: str, password: str) -> dict | None:
    """Check username/password against configured credentials.

    Returns user info dict on success, ``None`` on failure.
    """
    expected_user = os.environ.get("WEB_DASHBOARD_USERNAME", _DEFAULT_USERNAME)
    expected_pass = os.environ.get("WEB_DASHBOARD_PASSWORD", _DEFAULT_PASSWORD)

    if username == expected_user and password == expected_pass:
        return {
            "user_id": username,
            "username": username,
            "roles": ["operator"],
        }
    return None


def _get_secret_key() -> str:
    """Resolve AUTH_SECRET_KEY from env or dev config fallback."""
    key = os.environ.get("AUTH_SECRET_KEY", "")
    if key:
        return key
    # Fallback: read from dev environment config
    try:
        import yaml

        with open("config/environments/dev.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("auth_secret_key", "")
    except Exception:
        return ""


def _create_token(user_info: dict, expires_hours: int = 24) -> str:
    """Generate a HS256 JWT token for the given user."""
    secret_key = _get_secret_key()
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_info["user_id"],
        "sub": user_info["user_id"],
        "username": user_info["username"],
        "roles": user_info["roles"],
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


@router.post("/login")
async def login(request: LoginRequest) -> ApiResponse:
    """Validate credentials and return a JWT token.

    Requirements: 1.2, 6.1
    """
    user_info = _verify_credentials(request.username, request.password)
    if user_info is None:
        return ApiResponse(
            status="error",
            message="Invalid username or password",
        )

    token = _create_token(user_info)
    return ApiResponse(
        status="success",
        data=LoginData(
            token=token,
            user_id=user_info["user_id"],
            username=user_info["username"],
        ).model_dump(),
        message="Login successful",
    )
