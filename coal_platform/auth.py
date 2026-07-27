from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from coal_platform.config import Settings, get_settings

password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hash.verify(password, encoded_hash)
    except (TypeError, ValueError):
        return False


def access_token_expires_at(settings: Settings | None = None) -> datetime:
    active_settings = settings or get_settings()
    return datetime.now(UTC) + timedelta(minutes=active_settings.access_token_expire_minutes)


def create_access_token(
    user: dict[str, Any],
    session_id: str,
    expires_at: datetime,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "jti": session_id,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, active_settings.secret_key, algorithm=active_settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    active_settings = settings or get_settings()
    try:
        payload = jwt.decode(token, active_settings.secret_key, algorithms=[active_settings.jwt_algorithm])
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired access token") from exc
    if not payload.get("sub") or not payload.get("jti"):
        raise HTTPException(status_code=401, detail="invalid access token claims")
    return payload


def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    store = request.app.state.store
    user = store.get_user(payload["sub"])
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="user is unavailable")
    if not store.is_auth_session_active(payload["jti"], user["id"]):
        raise HTTPException(status_code=401, detail="access token session is unavailable")
    request.state.current_user = user
    request.state.auth_session_id = payload["jti"]
    return user


def require_admin(user: Annotated[dict[str, Any], Depends(require_user)]) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="administrator role required")
    return user
