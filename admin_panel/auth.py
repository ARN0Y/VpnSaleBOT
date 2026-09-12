"""Session auth for the admin panel.

The panel is a single-page app that signs in against the JSON API, so there is
no server-rendered login page here — the shell and its assets load freely and
every endpoint that returns real data sits behind the session cookie.

Credentials live in the database (see credentials.py). The middleware reads them
from ``app.state.credentials``, which is refreshed when the operator changes
them, so a password change takes effect on the next request rather than on the
next restart.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE_NAME = "panel_session"
CSRF_HEADER = "x-csrf-token"

# Reachable before signing in: the login call itself, and the first-run setup
# pair that exists only while no administrator has been created.
OPEN_PATHS = frozenset({
    "/admin/api/v1/login",
    "/admin/api/v1/setup",
    # The sign-in screen's own appearance. It renders before anyone has a
    # session, and returns nothing but decoration.
    "/admin/api/v1/branding",
})


class LoginRateLimiter:
    """Slows down guessing, per client address."""

    def __init__(self, *, max_attempts: int = 8, window_seconds: int = 600, block_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def is_blocked(self, key: str) -> bool:
        until = self._blocked_until.get(key, 0)
        if until and until > time.time():
            return True
        if until:
            self._blocked_until.pop(key, None)
        return False

    def record_failure(self, key: str) -> None:
        now = time.time()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        recent.append(now)
        self._failures[key] = recent
        if len(recent) >= self.max_attempts:
            self._blocked_until[key] = now + self.block_seconds
            self._failures.pop(key, None)

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._blocked_until.pop(key, None)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        credentials = getattr(request.app.state, "credentials", None)

        # The SPA shell and its assets are public; the app authenticates itself
        # against the API and renders its own sign-in screen on a 401. Anything
        # that returns real data — the JSON API, and the Telegram file proxy
        # that serves payment receipts — stays behind the cookie.
        if path == "/" or path == "/admin" or (
            path.startswith("/admin/")
            and not path.startswith("/admin/api/")
            and not path.startswith("/admin/file/")
        ):
            return await call_next(request)
        if path in OPEN_PATHS or not path.startswith("/admin"):
            return await call_next(request)

        is_api = path.startswith("/admin/api/")
        secret = getattr(credentials, "secret", "") if credentials else ""
        session = verify_session(request.cookies.get(COOKIE_NAME, ""), secret) if secret else None
        if not session:
            if is_api:
                return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
            return Response("unauthorized", status_code=401)

        request.state.admin_session = session
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            body = await request.body()
            csrf_value = request.headers.get(CSRF_HEADER, "")
            if not csrf_value or not secrets.compare_digest(str(session.get("csrf", "")), csrf_value):
                if is_api:
                    return JSONResponse({"ok": False, "error": "csrf_failed"}, status_code=403)
                return Response("CSRF validation failed", status_code=403)
            request._body = body

        return await call_next(request)


def install_auth(app) -> None:
    app.state.auth_limiter = LoginRateLimiter()
    app.add_middleware(AdminAuthMiddleware)


def issue_session(credentials, username: str) -> tuple[str, str, int]:
    """A signed session for `username`: (cookie value, csrf token, max age)."""
    csrf = secrets.token_urlsafe(32)
    ttl = int(credentials.ttl_seconds)
    token = sign_session(
        {"u": username, "exp": int(time.time()) + ttl, "csrf": csrf, "n": secrets.token_urlsafe(12)},
        credentials.secret,
    )
    return token, csrf, ttl


def set_session_cookie(response, credentials, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=bool(credentials.cookie_secure),
        samesite="lax",
        path="/",
    )


def csrf_token(request: Request) -> str:
    session = getattr(request.state, "admin_session", None)
    return str(session.get("csrf", "")) if isinstance(session, dict) else ""


def current_admin_username(request: Request) -> str:
    session = getattr(request.state, "admin_session", None)
    return str(session.get("u", "")) if isinstance(session, dict) else ""


def sign_session(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str, secret: str) -> dict[str, Any] | None:
    if not token or "." not in token or not secret:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(pad_b64(body)).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload if isinstance(payload, dict) else None


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def pad_b64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")
