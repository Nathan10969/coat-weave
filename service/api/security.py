from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from .config import CoatingApiSettings


def check_bearer_token(authorization: str | None, expected_token: str) -> bool:
    if not expected_token or not authorization:
        return False
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    return hmac.compare_digest(authorization.removeprefix(prefix).strip(), expected_token)


def check_client_ip(request: Request, allowed_ips: list[str]) -> bool:
    if not allowed_ips:
        return True
    client = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return client in allowed_ips or forwarded in allowed_ips


def require_api_auth(
    request: Request,
    settings: CoatingApiSettings,
    authorization: str | None = Header(default=None),
) -> None:
    if not settings.auth_required:
        return
    if not check_bearer_token(authorization, settings.api_token):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    if not check_client_ip(request, settings.allowed_ips):
        raise HTTPException(status_code=403, detail="client ip is not allowed")
