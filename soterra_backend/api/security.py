from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request

logger = logging.getLogger("soterra_backend")

class RateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        starts_after = now - window_seconds
        attempts = [value for value in self._attempts.get(key, []) if value >= starts_after]
        allowed = len(attempts) < limit
        attempts.append(now)
        self._attempts[key] = attempts
        return allowed


rate_limiter = RateLimiter()


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def set_auth_headers(request: Request, *, tenant_id: str, user_id: str) -> None:
    next_headers = [
        item
        for item in request.scope.get("headers", [])
        if item[0].lower() not in {b"x-soterra-tenant-id", b"x-soterra-user-id"}
    ]
    next_headers.extend(
        [
            (b"x-soterra-tenant-id", tenant_id.encode("utf-8")),
            (b"x-soterra-user-id", user_id.encode("utf-8")),
        ]
    )
    request.scope["headers"] = next_headers


def check_rate_limit(request: Request, action: str, subject: str, *, limit: int, window_seconds: int) -> None:
    ip_address = request.client.host if request.client else "unknown"
    key = f"{action}:{ip_address}:{subject}"
    if not rate_limiter.check(key, limit=limit, window_seconds=window_seconds):
        logger.warning("rate_limit_rejected action=%s subject=%s ip=%s", action, subject, ip_address)
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
