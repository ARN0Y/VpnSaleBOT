from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware


COOKIE_NAME = "navidvpn_admin_session"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password: str
    secret: str
    ttl_seconds: int = 28800
    cookie_secure: bool = False

    @classmethod
    def from_env(cls) -> "AuthConfig":
        username = os.getenv("ADMIN_PANEL_USERNAME", "").strip()
        password = os.getenv("ADMIN_PANEL_PASSWORD", "")
        secret = os.getenv("ADMIN_SESSION_SECRET", "")
        if not username or not password or not secret:
            raise RuntimeError(
                "ADMIN_PANEL_USERNAME, ADMIN_PANEL_PASSWORD and ADMIN_SESSION_SECRET must be set before starting admin_panel."
            )
        ttl = _as_int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "28800"), 28800)
        return cls(
            username=username,
            password=password,
            secret=secret,
            ttl_seconds=max(900, ttl),
            cookie_secure=os.getenv("ADMIN_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"},
        )


class LoginRateLimiter:
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
        failures = [ts for ts in self._failures.get(key, []) if now - ts <= self.window_seconds]
        failures.append(now)
        self._failures[key] = failures
        if len(failures) >= self.max_attempts:
            self._blocked_until[key] = now + self.block_seconds
            self._failures[key] = []

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._blocked_until.pop(key, None)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, config: AuthConfig, limiter: LoginRateLimiter):
        super().__init__(app)
        self.config = config
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path in {"/favicon.ico"}:
            return await call_next(request)

        # The SPA shell + its static assets load without server auth; the app's
        # JavaScript authenticates itself against the protected JSON API and
        # renders its own login screen on a 401.
        if path == "/admin/app" or path.startswith("/admin/app/"):
            return await call_next(request)

        if path in {"/admin/login", "/admin/api/v1/login"}:
            return await call_next(request)

        if path == "/":
            return await call_next(request)

        if not path.startswith("/admin"):
            return await call_next(request)

        is_api = path.startswith("/admin/api/")
        session = verify_session(request.cookies.get(COOKIE_NAME, ""), self.config.secret)
        if not session:
            if is_api:
                from fastapi.responses import JSONResponse

                return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
            return login_redirect(request)

        request.state.admin_session = session
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            body = await request.body()
            csrf_value = request.headers.get(CSRF_HEADER, "")
            if not csrf_value:
                csrf_value = extract_csrf_from_body(body, request.headers.get("content-type", ""))
            if not csrf_value or not secrets.compare_digest(str(session.get("csrf", "")), csrf_value):
                if is_api:
                    from fastapi.responses import JSONResponse

                    return JSONResponse({"ok": False, "error": "csrf_failed"}, status_code=403)
                return Response("CSRF validation failed", status_code=403)
            request._body = body

        return await call_next(request)


def install_auth(app, config: AuthConfig) -> None:
    limiter = LoginRateLimiter()
    app.state.auth_config = config
    app.state.auth_limiter = limiter
    app.add_middleware(AdminAuthMiddleware, config=config, limiter=limiter)

    @app.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if verify_session(request.cookies.get(COOKIE_NAME, ""), config.secret):
            return RedirectResponse("/admin", status_code=303)
        return render_login(request)

    @app.post("/admin/login")
    async def login_submit(request: Request):
        ip = client_key(request)
        if limiter.is_blocked(ip):
            return render_login(request, error="تعداد تلاش‌های ناموفق زیاد است. چند دقیقه بعد دوباره تلاش کنید.", status_code=429)

        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        valid = secrets.compare_digest(username, config.username) and secrets.compare_digest(password, config.password)
        if not valid:
            limiter.record_failure(ip)
            return render_login(request, error="نام کاربری یا رمز عبور نادرست است.", username=username, status_code=401)

        limiter.record_success(ip)
        csrf = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + config.ttl_seconds
        token = sign_session({"u": username, "exp": expires_at, "csrf": csrf, "n": secrets.token_urlsafe(12)}, config.secret)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=config.ttl_seconds,
            httponly=True,
            secure=config.cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/admin/logout")
    async def logout():
        response = RedirectResponse("/admin/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response


def render_login(request: Request, *, error: str = "", username: str = "", status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "error": error,
            "username": username,
            "title": "ورود مدیریت",
        },
        status_code=status_code,
    )


def csrf_token(request: Request) -> str:
    session = getattr(request.state, "admin_session", None)
    if isinstance(session, dict):
        return str(session.get("csrf", ""))
    return ""


def current_admin_username(request: Request) -> str:
    session = getattr(request.state, "admin_session", None)
    if isinstance(session, dict):
        return str(session.get("u", ""))
    return ""


def sign_session(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str, secret: str) -> dict[str, Any] | None:
    if not token or "." not in token:
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


def extract_csrf_from_body(body: bytes, content_type: str) -> str:
    if not body or "application/x-www-form-urlencoded" not in content_type.lower():
        return ""
    try:
        from urllib.parse import parse_qs

        values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return str((values.get("csrf_token") or [""])[0])
    except Exception:
        return ""


def login_redirect(request: Request) -> RedirectResponse:
    target = "/admin/login"
    if request.url.path and request.url.path != "/admin/login":
        target += "?" + urlencode({"next": request.url.path})
    return RedirectResponse(target, status_code=303)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def pad_b64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")


def _as_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default
